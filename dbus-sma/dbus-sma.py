#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""dbus-sma.py: Driver to integrate SMA SunnyIsland inverters 
                with Victron Venus OS. """

__author__      = "github usernames: madsci1016, jaedog"
__copyright__   = "Copyright 2020"
__license__     = "MIT"
__version__     = "1.1"

import os
import signal
import sys
import argparse
import serial
import socket
import logging

from dbus.mainloop.glib import DBusGMainLoop
import dbus
import gobject

import can
from can.bus import BusState
from timeit import default_timer as timer
import time
from datetime import datetime, timedelta

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from vedbus import VeDbusService
from ve_utils import get_vrm_portal_id, exit_on_error
from dbusmonitor import DbusMonitor

#from settingsdevice import SettingsDevice
#from logger import setup_logging
#import delegates
#from sc_utils import safeadd as _safeadd, safemax as _safemax

# ignore terminal resize signals (keeps exception from being thrown)
signal.signal(signal.SIGWINCH, signal.SIG_IGN)


softwareVersion = '1.1'
logger = logging.getLogger("dbus-sma")

# global logger for all modules imported here
#logger = logging.getLogger()

#logging.basicConfig(filename='/data/etc/dbus-sma/logging.log', encoding='utf-8', level=logging.INFO)
#logger.setLevel(logging.DEBUG)
logger.setLevel(logging.INFO)


# The CANable (https://canable.io/) is a small open-source USB to CAN adapter. The CANable can show up as a virtual serial port (slcan): /dev/ttyACM0 or
# as a socketcan: can0. In testing both methods work, however, I found the can0 to be much more robust.
# Devices from http://protofusion.org store ship by default with the "slcan" firmware. It can be flashed with the "candlelight" firmware to
# support socketcan.

# When the adapter is a socketcan, bring up link first as root:
# ip link set can0 up type can bitrate 500000
#

# TODO: change to input param
#canBusChannel = "/dev/ttyACM0"
canBusChannel = "can5"

#canBusType = "slcan"
canBusType = "socketcan"

# connect and register to dbus
driver = {
	'name'        : "SMA SunnyIsland",
	'servicename' : "smasunnyisland",
	'instance'    : 261,
	'id'          : 2754,
	'version'     : 476,
	'serial'      : "SMABillConnect",
	'connection'  : "com.victronenergy.vebus.ttyACM0"
}

CAN_tx_msg = {"BatChg": 0x351, "BatSoC": 0x355, "BatVoltageCurrent" : 0x356, "AlarmWarning": 0x35a, "BMSOem": 0x35e, "BatData": 0x35f}
CANFrames = {"ExtPwr": 0x300, "InvPwr": 0x301, "OutputVoltage": 0x304, "Battery": 0x305, "Relay": 0x306, "LoadPwr": 0x308, "ExtVoltage": 0x309}
Line1 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0, "ExtFreq": 0.00, "OutputFreq": 0.00}
Line2 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0}
Battery = {"Voltage": 0, "Current": 0}
System = {"ExtRelay" : 0, "Load" : 0}

def getSignedNumber(number, bitLength):
    mask = (2 ** bitLength) - 1
    if number & (1 << (bitLength - 1)):
        return number | ~mask
    else:
        return number & mask

def bytes(integer):
    return divmod(integer, 0x100)

class BMSData:
  def __init__(self, max_battery_voltage, min_battery_voltage, max_charge_amps, max_discharge_amps):
    self.max_battery_voltage = max_battery_voltage
    self.min_battery_voltage = min_battery_voltage
    self.max_charge_amps = max_charge_amps
    self.max_discharge_amps = max_discharge_amps
    self.state_of_charge = 42.0  # sane initial value
    self.actual_battery_voltage = 0.0
    self.req_charge_amps = 0.0
    self.req_discharge_amps = 0.0
    self.battery_current = 0.0
    self.pv_current = 0.0

# SMA Driver Class
class SmaDriver:

  def __init__(self):
    self.driver_start_time = datetime.now()
    
    #Initial BMS values eventually read from settings. 
    #Abs_V = 56.5
    self._bms_data = BMSData(60.0, 46.0, 100.0, 100.0)

    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    self._can_bus = False

    logger.debug("Can bus init")
    try :
      self._can_bus = can.interface.Bus(bustype=canBusType, channel=canBusChannel, bitrate=500000)
    except can.CanError as e:
     logger.error(e)

    logger.debug("Can bus init done")

		# Why this dummy? Because DbusMonitor expects these values to be there, even though we don't
		# need them. So just add some dummy data. This can go away when DbusMonitor is more generic.
    dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
    dbus_tree = {'com.victronenergy.system': 
      {'/Dc/Battery/Soc': dummy, '/Dc/Battery/Current': dummy, '/Dc/Pv/Current': dummy, '/Dc/Battery/Voltage': dummy }}

    self._dbusmonitor = self._create_dbus_monitor(dbus_tree, valueChangedCallback=self._dbus_value_changed)

    self._dbusservice = self._create_dbus_service()

    self._dbusservice.add_path('/Serial',        value=12345)
    self._dbusservice.add_path('/State',                   9)
    self._dbusservice.add_path('/Mode',                    3)
    self._dbusservice.add_path('/Ac/PowerMeasurementType', 0)
    self._dbusservice.add_path('/VebusChargeState',        1)

    # Create the inverter/charger paths
    self._dbusservice.add_path('/Ac/Out/L1/P',            -1)
    self._dbusservice.add_path('/Ac/Out/L2/P',            -1)
    self._dbusservice.add_path('/Ac/Out/L1/I',            -1)
    self._dbusservice.add_path('/Ac/Out/L2/I',            -1)
    self._dbusservice.add_path('/Ac/Out/L1/V',            -1)
    self._dbusservice.add_path('/Ac/Out/L2/V',            -1)
    self._dbusservice.add_path('/Ac/Out/L1/F',            -1)
    self._dbusservice.add_path('/Ac/Out/L2/F',            -1)
    self._dbusservice.add_path('/Ac/Out/P',               -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/P',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/P',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/P',          -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/V',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/V',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/F',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/F',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/I',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/I',       -1)
    self._dbusservice.add_path('/Ac/ActiveIn/Connected',   1)
    self._dbusservice.add_path('/Ac/ActiveIn/ActiveInput', 0)
    self._dbusservice.add_path('/VebusError',              0)
    self._dbusservice.add_path('/Dc/0/Voltage',           -1)
    self._dbusservice.add_path('/Dc/0/Power',             -1)
    self._dbusservice.add_path('/Dc/0/Current',           -1)
    self._dbusservice.add_path('/Ac/NumberOfPhases',       2)

    # Some attempts at logging consumption. Float of kwhr since driver start (i think)
    self._dbusservice.add_path('/Energy/GridToDc',         0)
    self._dbusservice.add_path('/Energy/GridToAcOut',      0)
    self._dbusservice.add_path('/Energy/DcToAcOut',        0)
    self._dbusservice.add_path('/Energy/AcIn1ToInverter',  0)
    self._dbusservice.add_path('/Energy/AcIn1ToAcOut',     0)
    self._dbusservice.add_path('/Energy/InverterToAcOut',  0)
    self._dbusservice.add_path('/Energy/Time',       timer())

    self._changed = True
#    self._updatevalues()

    # create timers (time in msec)
    gobject.timeout_add(2000, exit_on_error, self._can_bus_txmit_handler)
    gobject.timeout_add(2000, exit_on_error, self._energy_handler)
    gobject.timeout_add(20, exit_on_error, self._parse_can_data_handler)

  def __del__(self):
    if (self._can_bus):
      self._can_bus.shutdown()
      self._can_bus = False
      logger.debug("bus shutdown")

  def run(self):
    # Start and run the mainloop
    logger.info("Starting mainloop, responding only on events")
    self._mainloop = gobject.MainLoop()

    try:
      self._mainloop.run()
    except KeyboardInterrupt:
      self._mainloop.quit()

  def _create_dbus_monitor(self, *args, **kwargs):
    return DbusMonitor(*args, **kwargs)  
	
  def _create_dbus_service(self):
    dbusservice = VeDbusService(driver['connection'])
    dbusservice.add_mandatory_paths(
      processname=__file__,
      processversion=softwareVersion,
      connection='com.victronenergy.vebus.ttyACM0',
      deviceinstance=driver['instance'],
      productid=driver['id'],
      productname=driver['name'],
      firmwareversion=driver['version'],
      hardwareversion=driver['version'],
      connected=1)
    return dbusservice

#  def _updatevalues(self):
#    Soc = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Soc')
#    #print(Soc)  

  # callback that gets called ever time a dbus value has changed
  def _dbus_value_changed(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
		self._changed = True

  # called by timer every 20 msec
  def _parse_can_data_handler(self):

    try:
    
      msg = None
      # read msgs until we get one we want
      while True:
        msg = self._can_bus.recv(1)
        if (msg is None) :
          return True
          
        if (msg.arbitration_id == CANFrames["ExtPwr"] or msg.arbitration_id == CANFrames["InvPwr"] or \
              msg.arbitration_id == CANFrames["LoadPwr"] or msg.arbitration_id == CANFrames["OutputVoltage"] or \
              msg.arbitration_id == CANFrames["ExtVoltage"] or msg.arbitration_id == CANFrames["Battery"] or \
              msg.arbitration_id == CANFrames["Relay"]):
          break
        
#      msg = self._can_bus.recv(1)
#      for msg in self._can_bus:
      if msg is not None:
        if msg.arbitration_id == CANFrames["ExtPwr"]:
          Line1["ExtPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          Line2["ExtPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
          #self._updatedbus()
        elif msg.arbitration_id == CANFrames["InvPwr"]:
          Line1["InvPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          Line2["InvPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
          #calculate_pwr()
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["LoadPwr"]:
          System["Load"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["OutputVoltage"]:
          Line1["OutputVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
          Line2["OutputVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
          Line1["OutputFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["ExtVoltage"]:
          Line1["ExtVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
          Line2["ExtVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
          Line1["ExtFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["Battery"]:
          Battery["Voltage"] = float(msg.data[0] + msg.data[1]*256) / 10
          Battery["Current"] = float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16)) / 10
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["Relay"]:
          if msg.data[6] == 0x58:
            System["ExtRelay"] = 1
          elif msg.data[6] == 0x4e:
            System["ExtRelay"] = 0
          self._updatedbus()

    except (KeyboardInterrupt) as e:
      self._mainloop.quit()
    except (can.CanError) as e:
      logger.error(e)
      pass
#    except socket.error, e:
#      print "ouch"
#    except (Exception) as e:
#      if e.errno != errno.EINTR:
#        raise
    except Exception as e:
      exception_type = type(e).__name__
      logger.error("Exception occured: {0}, {1}".format(exception_type, e))

    return True

  def _updatedbus(self):
    self._dbusservice["/Ac/ActiveIn/L1/P"] = Line1["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/L2/P"] = Line2["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/L1/V"] = Line1["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L2/V"] = Line2["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L1/F"] = Line1["ExtFreq"]
    self._dbusservice["/Ac/ActiveIn/L2/F"] = Line1["ExtFreq"]
    if Line1["ExtVoltage"] != 0:
      self._dbusservice["/Ac/ActiveIn/L1/I"] = int(Line1["ExtPwr"] / Line1["ExtVoltage"])
    if Line2["ExtVoltage"] != 0:
      self._dbusservice["/Ac/ActiveIn/L2/I"] = int(Line2["ExtPwr"] / Line2["ExtVoltage"])
    self._dbusservice["/Ac/ActiveIn/P"] = Line1["ExtPwr"] + Line2["ExtPwr"]
    self._dbusservice["/Dc/0/Voltage"] = Battery["Voltage"]
    self._dbusservice["/Dc/0/Current"] = Battery["Current"] *-1
    self._dbusservice["/Dc/0/Power"] = Battery["Current"] * Battery["Voltage"] *-1
    
    #TODO: jaedog: verify that the sum of external and inverter power is correct
    
    line1_inv_outpwr = Line1["ExtPwr"] + Line1["InvPwr"]
    line2_inv_outpwr = Line2["ExtPwr"] + Line2["InvPwr"] 
    self._dbusservice["/Ac/Out/L1/P"] = line1_inv_outpwr
    self._dbusservice["/Ac/Out/L2/P"] = line2_inv_outpwr
    self._dbusservice["/Ac/Out/P"] =  System["Load"] 
    self._dbusservice["/Ac/Out/L1/F"] = Line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L2/F"] = Line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L1/V"] = Line1["OutputVoltage"]
    self._dbusservice["/Ac/Out/L2/V"] = Line2["OutputVoltage"]
    if Line1["OutputVoltage"] != 0:
      self._dbusservice["/Ac/Out/L1/I"] = int(line1_inv_outpwr / Line1["OutputVoltage"])
    if Line2["OutputVoltage"] != 0:
      self._dbusservice["/Ac/Out/L2/I"] = int(line2_inv_outpwr / Line2["OutputVoltage"])


    if System["ExtRelay"]:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 1
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 0
      self._dbusservice["/State"] = 3
    else:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 0
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 240
      self._dbusservice["/State"] = 9

  def _energy_handler(self):
    energy_sec = timer() - self._dbusservice["/Energy/Time"]
    self._dbusservice["/Energy/Time"] = timer()
    
    if self._dbusservice["/Dc/0/Power"] > 0:
      #Grid to battery
      self._dbusservice["/Energy/GridToAcOut"] = self._dbusservice["/Energy/GridToAcOut"] + \
        ((self._dbusservice["/Ac/Out/P"]) * energy_sec * 0.00000028)

      self._dbusservice["/Energy/GridToDc"] = self._dbusservice["/Energy/GridToDc"] + \
        (self._dbusservice["/Dc/0/Power"]  * energy_sec * 0.00000028)
    else:
      #battery to out
      self._dbusservice["/Energy/DcToAcOut"] = self._dbusservice["/Energy/DcToAcOut"] + \
        ((self._dbusservice["/Ac/Out/P"])  * energy_sec * 0.00000028)
  
    #print(timer() - self._dbusservice["/Energy/Time"], ":", self._dbusservice["/Ac/Out/P"])

    self._dbusservice["/Energy/AcIn1ToAcOut"] = self._dbusservice["/Energy/GridToAcOut"]
    self._dbusservice["/Energy/AcIn1ToInverter"] = self._dbusservice["/Energy/GridToDc"]
    self._dbusservice["/Energy/InverterToAcOut"] = self._dbusservice["/Energy/DcToAcOut"]
    self._dbusservice["/Energy/Time"] = timer()
    return True

# BMS charge logic since SMA is in dumb mode
  def _execute_bms_charge_logic(self):
    now = datetime.now()
    
    # SMA Sunny Island Feature:
    # Setting 232# Grid Control
    # Item 41 GdSocEna - Activate the grid request based on SOC (Default: Disable) = Enable
    #
    # By enabling this setting the SMA will activate grid to charge batteries. To set the ranges:
    # Setting 233# Grid Start
    #
    # Item 01 GdSocTm1Str - SOC limit for switching on utility grid for time 1 = 40%
    # Item 02 GdSocTm1Stp - SOC limit for switching off the utility grid for time 1 = 80%
    # Item 03 GdSocTm2Str - SOC limit for switching on utility grid for time 2 = 40%
    # Item 04 GdSocTm2Stp - SOC limit for switching off the utility grid for time 2 = 80%
    #
    # Note it runs through both timers...more investigation needed.
    

    #no point in running the math below to calculate a new target charge current unless we have an update from the inverters
    #which is slow. Like every 12 seconds. 
    #global SMAupdate  
    #if SMAupdate == True:
    #  SMAupdate = False

    #requested charge current varies by time of day and SoC value
    #for now, some rules to change charge behavior hard coded for my application.
    #Gonna try making these charge current targets inlcuding solar, so we need to subtract solar current later. 
    if now.hour >= 14 and now.hour <=22:
      if now.hour >= 17 and self._bms_data.state_of_charge < 49.0:
        self._bms_data.req_charge_amps = 175.0
      else:
        self._bms_data.req_charge_amps = 100.0
    else:
      self._bms_data.req_charge_amps = 4.0

    if self._bms_data.state_of_charge < 15.0:  #recovering from blackout? Charge fast! 
      self._bms_data.req_charge_amps = 200.0
    
   #subtract any active Solar current from the requested charge current
    self._bms_data.req_charge_amps = self._bms_data.req_charge_amps - self._bms_data.pv_current

    #Poor mans CC-CV charger. Since the SMA charge controler is disabled in Li-ion mode
    # we have to pretend to be one, assuming the inverter has been forced on grid by user. 
    # I need to write a proper CC-CV to float charger state machine, but for now, roll-back current
    if self._bms_data.actual_battery_voltage > 56:  # grab control of requested current from above code.
      if self._bms_data.actual_battery_voltage > 56.6:
        self._bms_data.req_charge_amps = 0;
      elif self._bms_data.actual_battery_voltage > 56.3:
        self._bms_data.req_charge_amps = (Battery["Current"] *-1) - 5  #this works in tenths of Amps at this level remember
      else:
        self._bms_data.req_charge_amps = (Battery["Current"] *-1)
    

    if self._bms_data.req_charge_amps < 0:
      self._bms_data.req_charge_amps = 0;

#    else:
#      self._bms_data.req_charge_amps = 3;
    
   #Low battery safety, if low voltage, pre-empt SoC with minimum value to force grid transfer
    if Line1["ExtVoltage"] > 100 and self._bms_data.actual_battery_voltage < 49.6:
      self._bms_data.state_of_charge = 1.0

    #logger.debug(self._bms_data.req_charge_amps) 
  
  
  
  	# Called on a two second timer to send CAN messages
  def _can_bus_txmit_handler(self):
  
    # log data received from SMA on CAN bus (doing it here since this timer is slower!)
    out_load_msg = "System Load: {0}, Driver runtime: {1}".format(System["Load"], datetime.now() - self.driver_start_time)

    out_ext_msg = "Line 1 Ext Voltage: {0}, Line 2 Ext Voltage: {1}, Line 1 Ext Pwr: {2}, Line 2 Ext Pwr: {3}, Freq: {4}" \
      .format(Line1["ExtVoltage"], Line2["ExtVoltage"], Line1["ExtPwr"], Line2["ExtPwr"], Line1["ExtFreq"])

    out_inv_msg = "Line 1 Inv Voltage: {0}, Line 2 Inv Voltage: {1}, Line 1 Inv Pwr: {2}, Line 2 Inv Pwr: {3}, Freq: {4}" \
      .format(Line1["OutputVoltage"], Line2["OutputVoltage"], Line1["InvPwr"], Line2["InvPwr"], Line1["OutputFreq"])

    out_batt_msg = "Batt Voltage: {0}, Batt Current: {1}" \
      .format(Battery["Voltage"], Battery["Current"])

    logger.info(out_load_msg)
    logger.info(out_ext_msg)
    logger.info(out_inv_msg)
    logger.info(out_batt_msg)

    
    #get some data from the Victron BUS
    self._bms_data.state_of_charge = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Soc')
    self._bms_data.actual_battery_voltage = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Voltage')
    self._bms_data.battery_current = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Current')
    self._bms_data.pv_current = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Pv/Current')

#    logger.debug("SoC: {0:.2f}%, Batt Voltage: {1:.2f}V, Batt Current: {2:.1f}A". \
    logger.info("SoC: {0}%, Batt Voltage: {1}V, Batt Current: {2}A". \
        format(self._bms_data.state_of_charge, self._bms_data.actual_battery_voltage, self._bms_data.battery_current))
    
    req_charge_amps = 0
    req_discharge_amps = 200.0
    
    if System["ExtRelay"] == 1:  #we are grid tied, run charge code. 
      self._execute_bms_charge_logic()


    #breakup some of the values for CAN packing
    SoC_HD = int(self._bms_data.state_of_charge*100)
    SoC_HD_H, SoC_HD_L = bytes(SoC_HD)
    #Req_Charge_HD = int(req_charge_amps*10)
    Req_Charge_H, Req_Charge_L = bytes(int(self._bms_data.req_charge_amps*10))
    #Req_Discharge_HD = int(req_discharge_amps*10)
    Req_Discharge_H, Req_Discharge_L = bytes(int(self._bms_data.req_discharge_amps*10))
    #Max_V_HD = int(Max_V*10)
    Max_V_H, Max_V_L = bytes(int(self._bms_data.max_battery_voltage*10))
    #Min_V_HD = int(Min_V*10)
    Min_V_H, Min_V_L = bytes(int(self._bms_data.min_battery_voltage*10))


    msg = can.Message(arbitration_id = CAN_tx_msg["BatChg"], 
      data=[Max_V_L, Max_V_H, Req_Charge_L, Req_Charge_H, Req_Discharge_L, Req_Discharge_H, Min_V_L, Min_V_H],
      is_extended_id=False)

    msg2 = can.Message(arbitration_id = CAN_tx_msg["BatSoC"],
      data=[int(self._bms_data.state_of_charge), 0x00, 0x64, 0x0, SoC_HD_L, SoC_HD_H],
      is_extended_id=False)

    msg3 = can.Message(arbitration_id = CAN_tx_msg["BatVoltageCurrent"],
      data=[0x00, 0x00, 0x00, 0x0, 0xf0, 0x00],
      is_extended_id=False)

    msg4 = can.Message(arbitration_id = CAN_tx_msg["AlarmWarning"],
      data=[0x00, 0x00, 0x00, 0x0, 0x00, 0x00, 0x00, 0x00],
      is_extended_id=False)

    msg5 = can.Message(arbitration_id = CAN_tx_msg["BMSOem"],
      data=[0x42, 0x41, 0x54, 0x52, 0x49, 0x55, 0x4d, 0x20],
      is_extended_id=False)

    msg6 = can.Message(arbitration_id = CAN_tx_msg["BatData"],
      data=[0x03, 0x04, 0x0a, 0x04, 0x76, 0x02, 0x00, 0x00],
      is_extended_id=False)

    #logger.debug(self._can_bus)

    try :
      self._can_bus.send(msg)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))
      time.sleep(.100)

      self._can_bus.send(msg2)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))
      time.sleep(.100)

      self._can_bus.send(msg3)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))

      time.sleep(.100)

      self._can_bus.send(msg4)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))

      time.sleep(.100)

      self._can_bus.send(msg5)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))

      time.sleep(.100)

      self._can_bus.send(msg6)
      #logger.debug("Message sent on {}".format(self._can_bus.channel_info))

      logger.info("Sent 6 messages on {}".format(self._can_bus.channel_info))
    except (can.CanError) as e:
      logger.error("CAN BUS Transmit error (is controller missing?): %s" % e.message)
    except KeyboardInterrupt:
      pass

    return True  # keep timer running

if __name__ == "__main__":
  # Argument parsing
  parser = argparse.ArgumentParser(description='Converts readings from AC-Sensors connected to a VE.Bus device in a pvinverter ' + 'D-Bus service.')
  parser.add_argument('-s', '--serial', help='tty')
  parser.add_argument("-d", "--debug", help="set logging level to debug",action="store_true")

  args = parser.parse_args()

  print("-------- dbus_SMADriver, v" + softwareVersion + " is starting up --------")
  #logger = setup_logging(args.debug)

  # create SMA Driver
  smadriver = SmaDriver()

  # run driver (starts mainloop and hangs until CTRL+C/SIGINT received)
  smadriver.run()

  # force clean up resources
  smadriver.__del__()
  
  print("-------- dbus_SMADriver, v" + softwareVersion + " is shuting down --------")

  sys.exit(1)


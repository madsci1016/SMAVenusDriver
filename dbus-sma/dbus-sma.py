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
import yaml

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
from settingsdevice import SettingsDevice  # available in the velib_python repository

from bms_state_machine import BMSChargeStateMachine, BMSChargeModel, BMSChargeController


#from settingsdevice import SettingsDevice
#from logger import setup_logging
#import delegates
#from sc_utils import safeadd as _safeadd, safemax as _safemax

# ignore terminal resize signals (keeps exception from being thrown)
signal.signal(signal.SIGWINCH, signal.SIG_IGN)


softwareVersion = '1.1'
#logger = logging.getLogger("dbus-sma")
#logger = logging.getLogger(__name__)

# global logger for all modules imported here
logger = logging.getLogger()

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
# To capture CAN msgs on the bus:
# tcpdump -w capture.pcap -i can5

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
	'connection'  : "com.victronenergy.vebus.smasunnyisland"
}

CAN_tx_msg = {"BatChg": 0x351, "BatSoC": 0x355, "BatVoltageCurrent" : 0x356, "AlarmWarning": 0x35a, "BMSOem": 0x35e, "BatData": 0x35f}
CANFrames = {"ExtPwr": 0x300, "InvPwr": 0x301, "OutputVoltage": 0x304, "Battery": 0x305, "Relay": 0x306, "Bits": 0x307, "LoadPwr": 0x308, "ExtVoltage": 0x309}
sma_line1 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0, "ExtFreq": 0.00, "OutputFreq": 0.00}
sma_line2 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0}
sma_battery = {"Voltage": 0, "Current": 0}
sma_system = {"State": 0, "ExtRelay" : 0, "ExtOk" : 0, "Load" : 0}

settings = 0

#command packets to turn SMAs on or off
SMA_ON_MSG = can.Message(arbitration_id = 0x35C,    #on
      data=[0b00000001,0,0,0],
      is_extended_id=False)

SMA_OFF_MSG = can.Message(arbitration_id = 0x35C,    #off
      data=[0b00000010,0,0,0],
      is_extended_id=False)



def getSignedNumber(number, bitLength):
    mask = (2 ** bitLength) - 1
    if number & (1 << (bitLength - 1)):
        return number | ~mask
    else:
        return number & mask

def bytes(integer):
    return divmod(integer, 0x100)

class BMSData:
  def __init__(self, max_battery_voltage, min_battery_voltage, low_battery_voltage, \
    charge_bulk_amps, max_discharge_amps, charge_absorb_voltage, charge_float_voltage, \
    time_min_absorb, rebulk_voltage):
    
    # settings for BMS

    # max and min battery voltage is used by the SMA as fault values
    # if the voltage goes above max or below min, the SMA will fault OFF
    # the inverter.
    self.max_battery_voltage = max_battery_voltage
    self.min_battery_voltage = min_battery_voltage
    
    # low battery voltage is used to trigger the SMA to connect to grid and 
    # begin charging the batteries. Note, this value must be greater than
    # the min_battery_voltage
    self.low_battery_voltage = low_battery_voltage
    
    self.charge_bulk_amps = charge_bulk_amps
    self.max_discharge_amps = max_discharge_amps
    self.charge_absorb_voltage = charge_absorb_voltage
    self.charge_float_voltage = charge_float_voltage
    self.time_min_absorb = time_min_absorb
    self.rebulk_voltage = rebulk_voltage
    
    # state of BMS
    self.charging_state = "" # state of charge state machine
    self.state_of_charge = 42.0  # sane initial value
    self.actual_battery_voltage = 0.0
    self.req_discharge_amps = max_discharge_amps
    self.battery_current = 0.0
    self.pv_current = 0.0

  def __str__(self):
    return "BMS Data, MaxV: {0}V, MinV: {1}V, LowV: {2}V, BulkA: {3}A, AbsorbV: {4}V, FloatV: {5}V, MinuteAbsorb: {6}, RebulkV: {7}V" \
      .format(self.max_battery_voltage, self.min_battery_voltage, self.low_battery_voltage, self.charge_bulk_amps, \
        self.charge_absorb_voltage, self.charge_float_voltage, self.time_min_absorb, self.rebulk_voltage)

# SMA Driver Class
class SmaDriver:

  def __init__(self):
    self.driver_start_time = datetime.now()

    # data from yaml config file
    self._cfg = self.get_config_data()
    _cfg_bms = self._cfg['BMSData']

    # TODO: use venus settings to define these values
    #Initial BMS values eventually read from settings.
    self._bms_data = BMSData(max_battery_voltage=_cfg_bms['max_battery_voltage'], \
      min_battery_voltage=_cfg_bms['min_battery_voltage'], low_battery_voltage=_cfg_bms['low_battery_voltage'], \
      charge_bulk_amps=_cfg_bms['charge_bulk_amps'], max_discharge_amps=_cfg_bms['max_discharge_amps'], \
      charge_absorb_voltage=_cfg_bms['charge_absorb_voltage'], charge_float_voltage=_cfg_bms['charge_float_voltage'], \
      time_min_absorb=_cfg_bms['time_min_absorb'], rebulk_voltage=_cfg_bms['rebulk_voltage'])

    self.bms_controller = BMSChargeController(charge_bulk_current=self._bms_data.charge_bulk_amps, \
      charge_absorb_voltage=self._bms_data.charge_absorb_voltage, charge_float_voltage=self._bms_data.charge_float_voltage, \
        time_min_absorb=self._bms_data.time_min_absorb, rebulk_voltage=self._bms_data.rebulk_voltage)
    ret = self.bms_controller.start_charging()

    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    self._can_bus = False

    self._safety_off = False   #flag to see if we every shut the inverters off due to low batt. 

    logger.debug("Can bus init")
    try :
      self._can_bus = can.interface.Bus(bustype=canBusType, channel=canBusChannel, bitrate=500000)
    except can.CanError as e:
     logger.error(e)

    logger.debug("Can bus init done")

    # Add the AcInput1 setting if it doesn't exist so that the grid data is reported
    # to the system by dbus-systemcalc-py service
    settings = SettingsDevice(
       bus=dbus.SystemBus(),# if (platform.machine() == 'armv7l') else dbus.SessionBus(),
       supportedSettings={
           'acinput': ['/Settings/SystemSetup/AcInput1', 1, 0, 0],
           'hub4mode': ['/Settings/CGwacs/Hub4Mode', 3, 0, 0], 
           'gridmeter': ['/Settings/CGwacs/RunWithoutGridMeter', 1, 0, 0], 
           'acsetpoint': ['/Settings/CGwacs/AcPowerSetPoint', 0, 0, 0],
           'maxchargepwr': ['/Settings/CGwacs/MaxChargePower', 0, 0, 0],
           'maxdischargepwr': ['/Settings/CGwacs/MaxDischargePower', 0, 0, 0],
           'maxchargepercent': ['/Settings/CGwacs/MaxChargePercentage', 0, 0, 0],
           'maxdischargepercent': ['/Settings/CGwacs/MaxDischargePercentage', 0, 0, 0],
           'essMode': ['/Settings/CGwacs/BatteryLife/State', 0, 0, 0],
           },
       eventCallback=None)


		# Why this dummy? Because DbusMonitor expects these values to be there, even though we don't
		# need them. So just add some dummy data. This can go away when DbusMonitor is more generic.
    dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
    dbus_tree = {'com.victronenergy.system': 
      {'/Dc/Battery/Soc': dummy, '/Dc/Battery/Current': dummy, '/Dc/Battery/Voltage': dummy, \
        '/Dc/Pv/Current': dummy, '/Ac/PvOnOutput/L1/Power': dummy, '/Ac/PvOnOutput/L2/Power': dummy, }}

    self._dbusmonitor = self._create_dbus_monitor(dbus_tree, valueChangedCallback=self._dbus_value_changed)

    self._dbusservice = self._create_dbus_service()

    self._dbusservice.add_path('/Serial',        value=12345)

    # /SystemState/State   ->   0: Off
    #                      ->   1: Low power
    #                      ->   2: VE.Bus Fault condition
    #                      ->   3: Bulk charging
    #                      ->   4: Absorption charging
    #                      ->   5: Float charging
    #                      ->   6: Storage mode
    #                      ->   7: Equalisation charging
    #                      ->   8: Passthru
    #                      ->   9: Inverting
    #                      ->  10: Assisting
    #                      -> 256: Discharging
    #                      -> 257: Sustain
    self._dbusservice.add_path('/State',                   0)
    self._dbusservice.add_path('/Mode',                    3)
    self._dbusservice.add_path('/Ac/PowerMeasurementType', 0)
    self._dbusservice.add_path('/Hub4/AssistantId', 5)
    self._dbusservice.add_path('/Hub4/DisableCharge', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/DisableFeedIn', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/DoNotFeedInOverVoltage', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/L1/AcPowerSetpoint', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/L2/AcPowerSetpoint', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/Sustain', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/L1/MaxFeedInPower', value=0, writeable=True)
    self._dbusservice.add_path('/Hub4/L2/MaxFeedInPower', value=0, writeable=True)


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
    self._dbusservice.add_path('/Alarms/GridLost',         0)

    # /VebusChargeState  <- 1. Bulk
    #                       2. Absorption
    #                       3. Float
    #                       4. Storage
    #                       5. Repeat absorption
    #                       6. Forced absorption
    #                       7. Equalise
    #                       8. Bulk stopped
    self._dbusservice.add_path('/VebusChargeState',        0)

    # Some attempts at logging consumption. Float of kwhr since driver start (i think)
    self._dbusservice.add_path('/Energy/GridToDc',         0)
    self._dbusservice.add_path('/Energy/GridToAcOut',      0)
    self._dbusservice.add_path('/Energy/DcToAcOut',        0)
    self._dbusservice.add_path('/Energy/AcIn1ToInverter',  0)
    self._dbusservice.add_path('/Energy/AcIn1ToAcOut',     0)
    self._dbusservice.add_path('/Energy/InverterToAcOut',  0)
    self._dbusservice.add_path('/Energy/Time',       timer())

    self._changed = True

    # create timers (time in msec)
    gobject.timeout_add(2000, exit_on_error, self._can_bus_txmit_handler)
    gobject.timeout_add(2000, exit_on_error, self._energy_handler)
    gobject.timeout_add(20, exit_on_error, self._parse_can_data_handler)

#----
  def __del__(self):
    if (self._can_bus):
      self._can_bus.shutdown()
      self._can_bus = False
      logger.debug("bus shutdown")

#----
  def run(self):
    # Start and run the mainloop
    logger.info("Starting mainloop, responding only on events")
    self._mainloop = gobject.MainLoop()

    try:
      self._mainloop.run()
    except KeyboardInterrupt:
      self._mainloop.quit()

#----
  def _create_dbus_monitor(self, *args, **kwargs):
    return DbusMonitor(*args, **kwargs)  

#----	
  def _create_dbus_service(self):
    dbusservice = VeDbusService(driver['connection'])
    dbusservice.add_mandatory_paths(
      processname=__file__,
      processversion=softwareVersion,
      connection=driver['connection'],
      deviceinstance=driver['instance'],
      productid=driver['id'],
      productname=driver['name'],
      firmwareversion=driver['version'],
      hardwareversion=driver['version'],
      connected=1)
    return dbusservice

#----
  # callback that gets called ever time a dbus value has changed
  def _dbus_value_changed(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
    self._changed = True

#----
  # called by timer every 20 msec
  def _parse_can_data_handler(self):

    try:
      msg = None
      # read msgs until we get one we want
      while True:
        msg = self._can_bus.recv(1)
        if (msg is None) :
          sma_system["State"] = 0
          #self._dbusservice["/State"] = 0
          logger.info("No Message received from Sunny Island")
          return True
          
        if (msg.arbitration_id == CANFrames["ExtPwr"] or msg.arbitration_id == CANFrames["InvPwr"] or \
              msg.arbitration_id == CANFrames["LoadPwr"] or msg.arbitration_id == CANFrames["OutputVoltage"] or \
              msg.arbitration_id == CANFrames["ExtVoltage"] or msg.arbitration_id == CANFrames["Battery"] or \
              msg.arbitration_id == CANFrames["Relay"] or msg.arbitration_id == CANFrames["Bits"]):
          break
        
      if msg is not None:
        if msg.arbitration_id == CANFrames["ExtPwr"]:
          sma_line1["ExtPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          sma_line2["ExtPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
          #self._updatedbus()
          #print ("Ex Power L1: " + str(sma_line1["ExtPwr"]) + "  Power L2: " + str(sma_line2["ExtPwr"]))
        elif msg.arbitration_id == CANFrames["InvPwr"]:
          sma_line1["InvPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          sma_line2["InvPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
          #calculate_pwr()
          #print ("Power L1: " + str(sma_line1["InvPwr"]) + "  Power L2: " + str(sma_line2["InvPwr"]))
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["LoadPwr"]:
          sma_system["Load"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["OutputVoltage"]:
          sma_line1["OutputVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
          sma_line2["OutputVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
          sma_line1["OutputFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["ExtVoltage"]:
          sma_line1["ExtVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
          sma_line2["ExtVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
          sma_line1["ExtFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
          self._updatedbus()
        elif msg.arbitration_id == CANFrames["Battery"]:
          sma_battery["Voltage"] = float(msg.data[0] + msg.data[1]*256) / 10
          sma_battery["Current"] = float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16)) / 10
          self._updatedbus()   
        elif msg.arbitration_id == CANFrames["Bits"]:
          if msg.data[2]&128:
            sma_system["ExtRelay"] = 1
          else:
            sma_system["ExtRelay"] = 0
          if msg.data[2]&64:
            sma_system["ExtOk"] = 0 
            #print ("Grid OK")
          else:
            #it seems to always report grid down once during relay transfer, so lets wait for two messages to latch. 
            if sma_system["ExtOk"] == 0:
              sma_system["ExtOk"] = 1
            elif sma_system["ExtOk"] == 1:
              sma_system["ExtOk"] = 2
            #print ("Grid Down")
        
          #print ("307 message" )
          #print(msg) 

    except (KeyboardInterrupt) as e:
      self._mainloop.quit()
    except (can.CanError) as e:
      logger.error(e)
      pass
    except Exception as e:
      exception_type = type(e).__name__
      logger.error("Exception occured: {0}, {1}".format(exception_type, e))

    return True

#----
  def _updatedbus(self):
    #self._dbusservice["/State"] = sma_system["State"]
    self._dbusservice["/Ac/ActiveIn/L1/P"] = sma_line1["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/L2/P"] = sma_line2["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/L1/V"] = sma_line1["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L2/V"] = sma_line2["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L1/F"] = sma_line1["ExtFreq"]
    self._dbusservice["/Ac/ActiveIn/L2/F"] = sma_line1["ExtFreq"]
    if sma_system["ExtOk"] == 0 or sma_system["ExtOk"] == 2:
      self._dbusservice["/Alarms/GridLost"] = sma_system["ExtOk"]
    if sma_line1["ExtVoltage"] != 0:
      self._dbusservice["/Ac/ActiveIn/L1/I"] = int(sma_line1["ExtPwr"] / sma_line1["ExtVoltage"])
    if sma_line2["ExtVoltage"] != 0:
      self._dbusservice["/Ac/ActiveIn/L2/I"] = int(sma_line2["ExtPwr"] / sma_line2["ExtVoltage"])
    self._dbusservice["/Ac/ActiveIn/P"] = sma_line1["ExtPwr"] + sma_line2["ExtPwr"]
    self._dbusservice["/Dc/0/Voltage"] = sma_battery["Voltage"]
    self._dbusservice["/Dc/0/Current"] = sma_battery["Current"] *-1
    self._dbusservice["/Dc/0/Power"] = sma_battery["Current"] * sma_battery["Voltage"] *-1
    
    line1_inv_outpwr = sma_line1["ExtPwr"] + sma_line1["InvPwr"]
    line2_inv_outpwr = sma_line2["ExtPwr"] + sma_line2["InvPwr"]


    #print ("After calc Power L1: " + str(line1_inv_outpwr) + "  Power L2: " + str(line2_inv_outpwr))

    #we can gain back a little bit of resolution by compairing total reported load to sum of line loads reported to remove one source of rounding error.
    if (sma_system["Load"] == (line1_inv_outpwr + line2_inv_outpwr + 100)):
      line1_inv_outpwr+=50
      line2_inv_outpwr+=50
    elif (sma_system["Load"] == (line1_inv_outpwr + line2_inv_outpwr - 100)):
      line1_inv_outpwr-=50
      line2_inv_outpwr-=50

    self._dbusservice["/Ac/Out/L1/P"] = line1_inv_outpwr
    self._dbusservice["/Ac/Out/L2/P"] = line2_inv_outpwr
    self._dbusservice["/Ac/Out/P"] =  sma_system["Load"] 
    self._dbusservice["/Ac/Out/L1/F"] = sma_line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L2/F"] = sma_line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L1/V"] = sma_line1["OutputVoltage"]
    self._dbusservice["/Ac/Out/L2/V"] = sma_line2["OutputVoltage"]
    
    inverter_on = 0
    if sma_line1["OutputVoltage"] > 5:
      self._dbusservice["/Ac/Out/L1/I"] = int(line1_inv_outpwr / sma_line1["OutputVoltage"])
      inverter_on += 1
    if sma_line2["OutputVoltage"] > 5:
      self._dbusservice["/Ac/Out/L2/I"] = int(line2_inv_outpwr / sma_line2["OutputVoltage"])
      inverter_on += 1

    if sma_system["ExtRelay"]:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 1
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 0
    else:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 0
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 240

    # state = 3:Bulk, 4:Absorb, 5:Float, 6:Storage, 7:Equalize, 8:Passthrough 9:Inverting 
    # push charging state to dbus
    vebusChargeState = 0
    sma_system["State"] = 0

    #logger.info("SysState: {0}, InvOn: {1}".format(systemState, inverter_on))

    if (inverter_on > 0):
      sma_system["State"] = 9
      # if current is going into the battery  
      if (self._bms_data.battery_current > 0):
        if (self._bms_data.charging_state == "bulk_chg"):
          vebusChargeState = 1
          sma_system["State"] = 3
        elif (self._bms_data.charging_state == "absorb_chg"):
          vebusChargeState = 2
          sma_system["State"] = 4
        elif (self._bms_data.charging_state == "float_chg"):
          vebusChargeState = 3
          sma_system["State"] = 5

    self._dbusservice["/VebusChargeState"] = vebusChargeState
    self._dbusservice["/State"] = sma_system["State"]

#----
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

#----
  # BMS charge logic since SMA is in dumb mode
  def _execute_grid_solar_charge_logic(self):
    charge_amps = None

    # time in UTC
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

    if (sma_system["ExtRelay"] == 1):
      #no point in running the math below to calculate a new target charge current unless we have an update from the inverters
      #which is slow. Like every 12 seconds. 
      #global SMAupdate  
      #if SMAupdate == True:
      #  SMAupdate = False

      _cfg_grid = self._cfg["GridLogic"]
      _cfg_safety = self._cfg["SafetyLogic"]
      #requested charge current varies by time of day and SoC value
      #for now, some rules to change charge behavior hard coded for my application.
      #Gonna try making these charge current targets inlcuding solar, so we need to subtract solar current later. 
      if now.hour >= _cfg_grid["start_hour"] and now.hour <= _cfg_grid["end_hour"]:
        if now.hour >= _cfg_grid["mid_hour"] and self._bms_data.state_of_charge < 49.0:
          charge_amps = _cfg_grid["mid_hour_current"]
        else:
          charge_amps = _cfg_grid["current"]
      else:
        charge_amps = _cfg_grid["offtime_current"]

      #TODO: can this use the same value as default bulk current?
      if self._bms_data.state_of_charge < _cfg_safety["after_blackout_min_soc"]:  #recovering from blackout? Charge fast! 
        charge_amps = _cfg_safety["after_blackout_charge_amps"]

      #subtract any active Solar current from the requested charge current
      charge_amps = charge_amps - self._bms_data.pv_current

      # if pv_current is greater than requested charge amps, don't go negative
      if (charge_amps < 0.0):
        charge_amps = 0.0

    logger.info("Grid Logic: Time: {0}, On Grid: {1} Charge amps: {2}" \
      .format(now, sma_system["ExtRelay"], charge_amps))

    return charge_amps
  
#----
 	# Called on a two second timer to send CAN messages
  def _can_bus_txmit_handler(self):
  
    # log data received from SMA on CAN bus (doing it here since this timer is slower!)
    out_load_msg = "SMA: System Load: {0}, Driver runtime: {1}".format(sma_system["Load"], datetime.now() - self.driver_start_time)

    out_ext_msg = "SMA: External, Line 1: {0}V, Line 2: {1}V, Line 1 Pwr: {2}W, Line 2 Pwr: {3}W, Freq: {4}" \
      .format(sma_line1["ExtVoltage"], sma_line2["ExtVoltage"], sma_line1["ExtPwr"], sma_line2["ExtPwr"], sma_line1["ExtFreq"])

    out_inv_msg = "SMA: Inverter, Line 1: {0}V, Line 2: {1}V, Line 1 Pwr: {2}W, Line 2 Pwr: {3}W, Freq: {4}" \
      .format(sma_line1["OutputVoltage"], sma_line2["OutputVoltage"], sma_line1["InvPwr"], sma_line2["InvPwr"], sma_line1["OutputFreq"])

    out_batt_msg = "SMA: Batt Voltage: {0}, Batt Current: {1}" \
      .format(sma_battery["Voltage"], sma_battery["Current"])

    logger.info(out_load_msg)
    logger.info(out_ext_msg)
    logger.info(out_inv_msg)
    logger.info(out_batt_msg)
    
    #get some data from the Victron BUS, invalid data returns NoneType
    soc = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Soc')
    volt = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Voltage')
    current = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Current')
    pv_current = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Pv/Current')
    if (pv_current == None):
      pv_current = 0.0

    # if we don't have these values, there is nothing to do!
    if (soc == None or volt == None):
      logger.error("DBusMonitor returning None for one or more: SOC: {0}, Volt: {1}, Current: {2}, PVCurrent: {3}" \
          .format(soc, volt, current, pv_current))
      return True

    # update bms state data
    self._bms_data.state_of_charge = soc
    self._bms_data.actual_battery_voltage = volt
    self._bms_data.battery_current = current
    self._bms_data.pv_current = pv_current

    # update the requested bulk current based on the grid solar charge logic
    self.bms_controller.update_req_bulk_current(self._execute_grid_solar_charge_logic())

    # update the battery voltage for the BMS to determine next state or charge current level
    # Note: Positive value for current means it is going INTO the battery. SMA will report as negative
    # so we change signs here
    is_state_changed = self.bms_controller.update_battery_data(self._bms_data.actual_battery_voltage, \
        -(sma_battery["Current"]))

    self._bms_data.charging_state = self.bms_controller.get_state()
    charge_current = self.bms_controller.get_charge_current()
  
    logger.info("BMS Send, SoC: {0:.1f}%, Batt Voltage: {1:.2f}V, Batt Current: {2:.2f}A, Charge State: {3}, Req Charge: {4}A, Req Discharge: {5}A, PV Cur: {6} ". \
        format(self._bms_data.state_of_charge, self._bms_data.actual_battery_voltage, \
        self._bms_data.battery_current, self._bms_data.charging_state, charge_current, 
        self._bms_data.req_discharge_amps, self._bms_data.pv_current))
        
    #**************Low battery safety****************# 
    
    _cfg_safety = self._cfg["SafetyLogic"]

    #if grid is up but battery low voltage, issue with shunt calibration or SMA setting, pre-empt SoC with minimum value to force grid transfer
    if (sma_system["ExtOk"] == 0 and self._bms_data.actual_battery_voltage < self._bms_data.low_battery_voltage):
      self._bms_data.state_of_charge = 1.0

    #if no grid and Soc is low, we are in blackout with dead batteries and need to shut off inverters
    if(self._safety_off == False):
      #normal running, check for grid not ok AND low Soc, send off message till inverters respond
      if(sma_system["ExtOk"] == 2 and  soc < _cfg_safety["min_soc_inv_off"]):   
        self._can_bus.send(SMA_OFF_MSG)
        if(sma_system["State"] == 0):
          self._safety_off = True
        #print("Shut off due to low SoC")
    else:
      #if we saftey shutdown, keep checking for grid restore OR SoC increase, send on message till inverters respond
      if(sma_system["ExtOk"] == 0 or soc >= _cfg_safety["min_soc_inv_off"]):  
        self._can_bus.send(SMA_ON_MSG)
        if(sma_system["State"] != 0): 
          self._safety_off = False
        #print("Start SMA due to grid restore or SoC increase")

    #breakup some of the values for CAN packing
    SoC_HD = int(self._bms_data.state_of_charge*100)
    SoC_HD_H, SoC_HD_L = bytes(SoC_HD)

    Req_Charge_H, Req_Charge_L = bytes(int(charge_current*10))

    Req_Discharge_H, Req_Discharge_L = bytes(int(self._bms_data.req_discharge_amps*10))
    Max_V_H, Max_V_L = bytes(int(self._bms_data.max_battery_voltage*10))
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

      #logger.info("Sent to SI: {0}, {1}, {2}, {3}, {4}". \
      #  format(self._bms_data.req_discharge_amps, self._bms_data.state_of_charge, \
      #  self._bms_data.actual_battery_voltage, self._bms_data.battery_current, \
      #  self._bms_data.pv_current))

      #logger.info("Sent 6 messages on {}".format(self._can_bus.channel_info))
    except (can.CanError) as e:
      logger.error("CAN BUS Transmit error (is controller missing?): %s" % e.message)
    except KeyboardInterrupt:
      pass

    return True  # keep timer running

#----
  def get_config_data(self):
    try :
      dir_path = os.path.dirname(os.path.realpath(__file__))
      with open(dir_path + "/dbus-sma.yaml", "r") as yamlfile:
        config = yaml.load(yamlfile, Loader=yaml.FullLoader)
        return config
    except :
      logger.info("dbus-sma.yaml file not found or correct.")
      sys.exit()

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


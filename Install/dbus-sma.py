from dbus.mainloop.glib import DBusGMainLoop
import dbus
import gobject
import argparse
import sys
import os
import json
import can
from can.bus import BusState
from timeit import default_timer as timer
import time
from itertools import chain

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from vedbus import VeDbusService
from ve_utils import get_vrm_portal_id, exit_on_error
from dbusmonitor import DbusMonitor
#from settingsdevice import SettingsDevice
#from logger import setup_logging
#import delegates
#from sc_utils import safeadd as _safeadd, safemax as _safemax

softwareVersion = '1.0'

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

CANFrames = {"ExtPwr": 0x300, "InvPwr": 0x301, "OutputVoltage": 0x304, "Battery": 0x305, "Relay": 0x306, "ExtVoltage": 0x309}
Line1 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0, "ExtFreq": 0.00, "OutputFreq": 0.00}
Line2 = {"OutputVoltage": 0, "ExtPwr": 0, "InvPwr": 0, "ExtVoltage": 0}
Battery = {"Voltage": 0, "Current": 0}
System = {"ExtRelay" : 0}

bus = can.interface.Bus(bustype='slcan', channel='/dev/ttyACM0', bitrate=500000)

def getSignedNumber(number, bitLength):
    mask = (2 ** bitLength) - 1
    if number & (1 << (bitLength - 1)):
        return number | ~mask
    else:
        return number & mask

class SmaDriver:

  def __init__(self):
		# Why this dummy? Because DbusMonitor expects these values to be there, even though we don't
		# need them. So just add some dummy data. This can go away when DbusMonitor is more generic.
    dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
    dbus_tree = {'com.victronenergy.system': {'/Dc/Battery/Soc': dummy}}

    self._dbusmonitor = self._create_dbus_monitor(dbus_tree, valueChangedCallback=self._dbus_value_changed)

    self._dbusservice = self._create_dbus_service()

    self._dbusservice.add_path('/Serial', value=12345)
    self._dbusservice.add_path('/State',       9)
    self._dbusservice.add_path('/Mode',       3)
    self._dbusservice.add_path('/Ac/PowerMeasurementType',       0)
    self._dbusservice.add_path('/VebusChargeState',       1)

    # Create the inverter/charger paths
    self._dbusservice.add_path('/Ac/Out/L1/P',       -1)
    self._dbusservice.add_path('/Ac/Out/L2/P',       -1)
    self._dbusservice.add_path('/Ac/Out/L1/V',       -1)
    self._dbusservice.add_path('/Ac/Out/L2/V',       -1)
    self._dbusservice.add_path('/Ac/Out/L1/F',       -1)
    self._dbusservice.add_path('/Ac/Out/L2/F',       -1)
    self._dbusservice.add_path('/Ac/Out/P',          -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/P',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/P',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/P',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/V',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/V',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L1/F',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/L2/F',                -1)
    self._dbusservice.add_path('/Ac/ActiveIn/Connected',         1)
    self._dbusservice.add_path('/Ac/ActiveIn/ActiveInput',               0)
    self._dbusservice.add_path('/VebusError',                    0)
    self._dbusservice.add_path('/Dc/0/Voltage',               -1)
    self._dbusservice.add_path('/Dc/0/Power',                 -1)
    self._dbusservice.add_path('/Dc/0/Current',               -1)
    self._dbusservice.add_path('/Ac/NumberOfPhases',             2)

    # Some attempts at logging consumption. Float of kwhr since driver start (i think)
    self._dbusservice.add_path('/Energy/GridToDc',             0)
    self._dbusservice.add_path('/Energy/GridToAcOut',          0)
    self._dbusservice.add_path('/Energy/DcToAcOut',            0)
    self._dbusservice.add_path('/Energy/AcIn1ToInverter',             0)
    self._dbusservice.add_path('/Energy/AcIn1ToAcOut',          0)
    self._dbusservice.add_path('/Energy/InverterToAcOut',            0)
    self._dbusservice.add_path('/Energy/Time',            timer())


    self._changed = True
    self._updatevalues()

    gobject.timeout_add(1000, exit_on_error, self._handletimertick)
    gobject.timeout_add(2000, exit_on_error, self._handlecantx)
    gobject.timeout_add(2000, exit_on_error, self._handleenergy)
    gobject.timeout_add(2, exit_on_error, self._parse_can_data)

  def _create_dbus_monitor(self, *args, **kwargs):
    raise Exception("This function should be overridden")
	
  def _create_dbus_service(self):
		raise Exception("This function should be overridden")


  def _updatevalues(self):
    Soc = self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Soc')
    #print(Soc)  

  def _dbus_value_changed(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
		self._changed = True

  def _parse_can_data(self):

    msg = bus.recv(1)
    if msg is not None:
      if msg.arbitration_id == CANFrames["ExtPwr"]:
        Line1["ExtPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
        Line2["ExtPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
        #print("Line 1 Ext Pwr: ", Line1["ExtPwr"])
        self._updatedbus()
      elif msg.arbitration_id == CANFrames["InvPwr"]:
        Line1["InvPwr"] = (getSignedNumber(msg.data[0] + msg.data[1]*256, 16)*100)
        Line2["InvPwr"] = (getSignedNumber(msg.data[2] + msg.data[3]*256, 16)*100)
        #calculate_pwr()
        self._updatedbus()
      elif msg.arbitration_id == CANFrames["OutputVoltage"]:
        Line1["OutputVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
        Line2["OutputVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
        Line1["OutputFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
        #print(Line1["OutputFreq"])
        self._updatedbus()
      elif msg.arbitration_id == CANFrames["ExtVoltage"]:
        Line1["ExtVoltage"] = (float(getSignedNumber(msg.data[0] + msg.data[1]*256, 16))/10)
        Line2["ExtVoltage"] = (float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16))/10)
        Line1["ExtFreq"] = float(msg.data[6] + msg.data[7]*256) / 100
        self._updatedbus()
      elif msg.arbitration_id == CANFrames["Battery"]:
        Battery["Voltage"] = float(msg.data[0] + msg.data[1]*256) / 10
        Battery["Current"] = float(getSignedNumber(msg.data[2] + msg.data[3]*256, 16)) / 10
        #print(Battery["Current"])	
        self._updatedbus()
      elif msg.arbitration_id == CANFrames["Relay"]:
        #print(msg.data[6])
        if msg.data[6] == 0x58:
          System["ExtRelay"] = 1
        elif msg.data[6] == 0x4e:
          System["ExtRelay"] = 0
        self._updatedbus()
    return True

  def _updatedbus(self):
    self._dbusservice["/Ac/ActiveIn/L1/P"] = Line1["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/L2/P"] = Line2["ExtPwr"]
    self._dbusservice["/Ac/ActiveIn/P"] = Line1["ExtPwr"] + Line2["ExtPwr"]
    self._dbusservice["/Dc/0/Voltage"] = Battery["Voltage"]
    self._dbusservice["/Dc/0/Current"] = Battery["Current"] *-1
    self._dbusservice["/Dc/0/Power"] = Battery["Current"] * Battery["Voltage"] *-1
    self._dbusservice["/Ac/Out/L1/P"] = Line1["ExtPwr"] + Line1["InvPwr"]
    self._dbusservice["/Ac/Out/L2/P"] = Line2["ExtPwr"] + Line2["InvPwr"] 
    self._dbusservice["/Ac/Out/P"] = Line1["InvPwr"] + Line1["InvPwr"] + Line1["ExtPwr"] + Line2["ExtPwr"]
    self._dbusservice["/Ac/Out/L1/F"] = Line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L2/F"] = Line1["OutputFreq"]
    self._dbusservice["/Ac/Out/L1/V"] = Line1["OutputVoltage"]
    self._dbusservice["/Ac/Out/L2/V"] = Line2["OutputVoltage"]
    self._dbusservice["/Ac/ActiveIn/L1/V"] = Line1["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L2/V"] = Line2["ExtVoltage"]
    self._dbusservice["/Ac/ActiveIn/L1/F"] = Line1["ExtFreq"]
    self._dbusservice["/Ac/ActiveIn/L2/F"] = Line1["ExtFreq"]

    if System["ExtRelay"]:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 1
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 0
      self._dbusservice["/State"] = 3
    else:
      self._dbusservice["/Ac/ActiveIn/Connected"] = 0
      self._dbusservice["/Ac/ActiveIn/ActiveInput"] = 240
      self._dbusservice["/State"] = 9


  def _handleenergy(self):
    energy_sec = timer() - self._dbusservice["/Energy/Time"]
    self._dbusservice["/Energy/Time"] = timer()
    
    if self._dbusservice["/Dc/0/Power"] > 0:
      #Grid to battery
      self._dbusservice["/Energy/GridToAcOut"] = self._dbusservice["/Energy/GridToAcOut"] + ((self._dbusservice["/Ac/Out/P"]) * energy_sec * 0.00000028)
      self._dbusservice["/Energy/GridToDc"] = self._dbusservice["/Energy/GridToDc"] + (self._dbusservice["/Dc/0/Power"]  * energy_sec * 0.00000028)
    else:
      #battery to out
      self._dbusservice["/Energy/DcToAcOut"] = self._dbusservice["/Energy/DcToAcOut"] +(self._dbusservice["/Dc/0/Power"]  * energy_sec * 0.00000028 * -1)
  #print(timer() - self._dbusservice["/Energy/Time"], ":", self._dbusservice["/Ac/Out/P"])
    self._dbusservice["/Energy/AcIn1ToAcOut"] = self._dbusservice["/Energy/GridToAcOut"]
    self._dbusservice["/Energy/AcIn1ToInverter"] = self._dbusservice["/Energy/GridToDc"]
    self._dbusservice["/Energy/InverterToAcOut"] = self._dbusservice["/Energy/DcToAcOut"]
    self._dbusservice["/Energy/Time"] = timer()
    return True


  	# Called on a one second timer
  def _handletimertick(self):
    if self._changed:
      self._updatevalues()
    self._changed = False

    return True  # keep timer running

  	# Called on a one second timer
  def _handlecantx(self):
    #print("TX here")
    Soc = int(self._dbusmonitor.get_value('com.victronenergy.system', '/Dc/Battery/Soc'))
    #print(Soc)

    msg = can.Message(arbitration_id=0x351,
                      data=[0x58, 0x02, 0x1e, 0x0, 0x6c, 0x07, 0xa4, 0x01],
                      is_extended_id=False)
    msg2 = can.Message(arbitration_id=0x355,
                      data=[Soc, 0x00, 0x64, 0x0, 0x00, 0x00],
                      is_extended_id=False)
    msg3 = can.Message(arbitration_id=0x356,
                      data=[0x00, 0x00, 0x00, 0x0, 0xf0, 0x00],
                      is_extended_id=False)
    msg4 = can.Message(arbitration_id=0x35a,
                      data=[0x00, 0x00, 0x00, 0x0, 0x01, 0x00, 0x00, 0x00],
                      is_extended_id=False)
    msg5 = can.Message(arbitration_id=0x35e,
                      data=[0x42, 0x41, 0x54, 0x52, 0x49, 0x55, 0x4d, 0x20],
                      is_extended_id=False)
    msg6 = can.Message(arbitration_id=0x35f,
                      data=[0x03, 0x04, 0x0a, 0x04, 0x76, 0x02, 0x00, 0x00],
                      is_extended_id=False)


    try:
        bus.send(msg)
 #       print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")
    time.sleep(.100)
    try:
        bus.send(msg2)
  #      print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")
    time.sleep(.100)
    try:
        bus.send(msg3)
   #     print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")
    time.sleep(.100)
    try:
        bus.send(msg4)
    #    print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")
    time.sleep(.100)
    try:
        bus.send(msg5)
#        print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")
    time.sleep(.100)
    try:
        bus.send(msg6)
 #       print("Message sent on {}".format(bus.channel_info))
    except can.CanError:
        print("Message NOT sent")

    return True  # keep timer running

class DbusSmaDriver(SmaDriver):
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

  

if __name__ == "__main__":
  # Argument parsing
  parser = argparse.ArgumentParser(description='Converts readings from AC-Sensors connected to a VE.Bus device in a pvinverter ' + 'D-Bus service.')

  parser.add_argument("-d", "--debug", help="set logging level to debug",action="store_true")

  args = parser.parse_args()

  print("-------- dbus_SMADriver, v" + "1" + " is starting up --------")
	#logger = setup_logging(args.debug)

	# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
  DBusGMainLoop(set_as_default=True)

  smadriver = DbusSmaDriver()

	# Start and run the mainloop
	#logger.info("Starting mainloop, responding only on events")
  mainloop = gobject.MainLoop()
  mainloop.run()
  quit(1)
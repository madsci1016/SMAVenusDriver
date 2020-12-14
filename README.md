### THIS IS A WORK IN PROGRESS -- YMMV, NO WARRANTY EXPRESSED OR IMPLIED. YOU CAN ENDANGER YOURSELF AND OTHERS.

# SMA Venus Driver
This project integrates the SMA Sunny Island inverters with Victron Venus OS. It supports SMA LiIon_Ext-BMS mode by providing BMS data to the Sunny Island via the CAN bus (directly supported by socketcan devices). The software runs on the Venus OS device as a Venus driver and uses the Venus dbus to read/write data for use with the Venus device.

### Kudos to Victron Energy
Victron Engergy has provided much of the Venus OS architecture under Open Source copyright. This framework allows independent projects like this to exist. Even though we are using non-Victron hardware, we can include it into the Victron ecosystem with other Victron equipment. Victron stated that although they would not assist, they would not shut it down. Send support to Victron Energy by buying their products!

Tested with RPi 3B - v2.60 Build 20200906135923

### Install

The provided install.sh script will copy files download dependencies and should provide a running configuration. It will not setup valid configuration values so don't expect this to be plug and play:

1. from root login on the venus root home directory
2. wget https://github.com/jaedog/SMAVenusDriver/raw/master/install/install.sh
3. chmod +x install.sh
4. ./install.sh
5. answer Y to the install of the driver
6. answer Y to the dependencies (unless they are already installed)

## Victron VenusOS Notes

This project implements a com.victronenergy.vebus inverter/charger (like Multis, Quattros, Inverters) device so that the rest of the eco-system (web-ui, VRM portal, etc) grabs the data and displays/logs it.

Victron is amazing at letting Venus OS be open source AND documenting it VERY well so that hackers can have at it. Yeah, they are understandably not thrilled I'm using a third-party device with their free stuff, but aren't against me doing it and specifically didn't ask me to stop (I offered). So, go buy Victron stuff, even if you already have an SMA inverter. I have 4 of their solar charge controllers and love them!

That said, I'm not sure if I've emulated the Multiplus very well or in every way that I could. I did manage to reverse engineer the energy counting architecture so usage data should appear in the portal. But there are some quarks trying to do a 1-1 map SMA to Victron. For example, the SMA reports inverter power flow and AC-2 (external) power flow, but not output power flow implicitly per line. So I had to do math (inverter power, External power, and output power should always sum to 0, right) to get that value. There can be artifacts in the real time data because of that. SMA also doesn’t report energy at all, so my code is calculating that from power data to the best of it’s ability. 

So the Victron system (or whatever you use this code with) will needs its own battery monitor or device to measure/calculate the SoC at a minimum, plus you have to hard code some voltage limits to makeup the minimum BMS messages the SunnyIsland requires. I recommend the Smart Shunt (https://www.victronenergy.com/battery-monitors/smart-battery-shunt) or the BMV-712 (https://www.victronenergy.com/battery-monitors/bmv-712-smart) connected to the Raspberry Pi with the VE.Direct USB cable. Note: The VE.Direct interface on these devices are 3.3V

### Useful Reading

See https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus for info.

See https://www.victronenergy.com/live/ccgx:root_access#:~:text=Go%20to%20Settings%2C%20General,Access%20Level%20change%20to%20Superuser for the way to gain root access to your Venus OS device. 

See https://github.com/victronenergy/venus/wiki/installing-additional-python-modules to add python modules needed for this driver to work. Like python-can.

## SMA Sunny Island

The Sunny Island was originally designed to use Lead Acid batteries, only. Lithium-ion support was added as a firmware update and does not contain any BMS logic. It requires an external BMS to provide details of the battery SoC, SoH, charge current need, etc. If it does not receive valid BMS data within a period of time, it will shutdown.

### CAN Bus
The Controller Area Network (CAN bus) is used at a rate of 500 kbs.

NOTE: The SMA SI will go into hard shutdown mode if it hasn't received a good BMS message after several minutes. If this happens you will need to power off the DC side of the inverter and wait for 15-30 min capacitors to drain. If the cover is off, you can monitor the red LED located left and down of the center control panel. When it goes off it can be powered on.

SMA SI Manual: https://files.sma.de/downloads/SI4548-6048-US-BE-en-21W.pdf

Page 53, Section 6.4.2 Connecting the Data Cable of the Lithium-Ion Batteries details where to connect the RJ45 CAN cable

#### CAN Adapter
The SMA SI use the CAN bus to communicate between master/slave and other devices. In order to participate on the CAN bus, you must have a CAN adapter. The tested CAN adapter is the open source USB CANable device (https://canable.io/). Either version from https://store.protofusion.org/ will work. The firmware installed from ProtoFusion store is slcan, which emmulates a tty serial device. This project supports the "candlelight" FW by default, which will require a FW flash to the canable device. See: https://github.com/jaedog/SMAVenusDriver/wiki/Canable-Firmware.

##### CAN Pinouts
The SMA SI uses an RJ45 connector for its CAN Bus interface. 

For a T-568B RJ45 pinout, the pins and colors are:
1. White Orange - Sync1 (reserved)
2. Orange - CAN_GND
3. White Green - SYNC_H
4. Blue - CAN_H
5. White Blue - CAN_L
6. Green - SYNC_L
7. White Brown - Sync7 (reserved)
8. Brown - Sync8 (reserved)

The pins of interest are:

* CAN_GND - Pin 2
* CAN_H - Pin 4
* CAN_L - Pin 5

It is worth noting that there is a terminating resistor on both the CAN and SYNC lines as part of the SMA RJ-45 terminator dongle. However, in my experience terminating the CAN bus alone has not caused any issues with Master/Slave comms.

## Final Words
There are still things hard-coded for specific applications. Although, configurability is improving. It supports the SMA as an off-grid (with grid available during low battery) with DC tied solar setup and the begining of support for AC coupled configurations. (See related project: https://github.com/jaedog/EnvoyVenusDriver for Enphase support). Note: The BMS logic is still **very crude** and may not work well depending on battery capacity or settings used. 

In case it wasn’t obvious, one fall back with this hack is if the Raspberry pi crashes or shuts off, the inverters will shut off as well. I recommend you have an offline back-up raspberry pi setup and ready to go to swap out in that event. 

## Todo List

 1)	Proper charge controller state machine <-- IN WORK
 2)	Move configuration values (charge current, voltage thresholds, etc) to the Victron settings structure. 
 3)	Create GUI in WEB_UI to change settings or trigger actions. 
 4)	Convert polling CAN adapter to proper callback when new CAN message arrives.
 5)	Get logging working correctly. 

## Tidbits

###### To determine if the driver is running execute:
> ps | grep dbus-sma
```
  supervise dbus-sma
  multilog t s25000 n4 /var/log/dbus-sma   <-- this will show up if logging is enabled
  python /data/etc/dbus-sma/dbus-sma.py
  grep dbus-sma
```

###### For debugging the script
1. Make sure the service auto start is disabled. Go to the /data/etc/dbus-sma directory.
2. Add the "down" file in ./service directory
```
	touch ./service/down   <-- creates an empty file named "down"
```
3. Stop the service if is running by:
```
	svc -d /service/dbus-sma
	svstat /service/dbus-sma   <-- checks if it is running, you can also do the ps cmd above
```
4. If you are using ssh to remote to the shell, you might want to be able to connect/disconnect the shell without disturbing the process. For that use "screen", a terminal multiplexer.
	1. screen  <-- starts a new screen
	2. CTRL+A,D  <-- disconnects from running screen
	3. screen -r  <-- reattaches to running screen
5. Now run the script: python dbus-sma.py
6. TBD logging... 

###### Venus Service

Venus uses daemontools (https://cr.yp.to/daemontools.html) to supervise and start the driver aka service.

## History

### Hacking the Sunny Island Notes

The SMA SunnyIsland 6048 has two potential communications buses. One is a CAN bus “ComSync” and the other is a RS-485 bus "ComSma" that requires an adapter card to be installed. The CAN bus is used by the SMA’s to communicate from the master to the slaves in a cluster, and to a Battery Management System (BMS) when configure in Lithium Ion mode. The RS-485 bus is required to connect to SMA grid tie inverters and to the WebBox.

It's clear the RS-485 was always the intended bus to connect to logging and telemetry systems such as the discontinued Sunny WebBox that allows you to see system telemetry on the SMA portal. But as they are very expensive now that they are discontinued, it isn’t a good option.

I started dumping the CAN bus to see what was there. There is of course a lot of high frequency messages I’m sure are used to sync up master and slave units, as well as the BMS traffic which is documented on page 10 of this BMS manual: http://www.rec-bms.com/datasheet/UserManual9R_SMA.pdf

BUT I also noticed there were some bytes in some messages moving in ways that appeared to correlate to system metrics. And indeed, they did. However, resolution is rather low, all power metrics are reported in 100s of watts. I realized this matched what is shown on the inverter screen, and then a light went off. You can buy the SMA “SunnyRemote” box which also connects by CAN bus. So these messages must be the system data meant for the “SunnyRemote” which has the same screen and menu as the local screen on the inverters. 

SO what this codes is doing is broken down into to big parts. First, it needs to pretend to be a BMS so the SunnyIslands will ingest battery SoC and charge current commands. Second, it is listening for the traffic intended for the “SunnyRemote” box so we can use it to extract ang log system metrics. All this is done through the CAN bus, so no additional parts need to be ordered. 

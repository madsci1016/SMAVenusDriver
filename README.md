# SMAVenusDriver
A driver to integrate SMA SunnyIsland inverters with Victron Venus OS

THIS IS A WORK IN PROGRESS -- YMMV, NO WARRANTY EXPRESSED OR IMPLIED

Tested with RPi 3B - v2.60 Build 20200906135923

### Install
* dbus-sma directory is the directory that needs to be copied to /data/etc 
* install directory has misc files needed to setup the driver to make it plug and play.

#### Install Script
To make things quicker, you can use the install.sh script:
1. from root login on the venus root home directory
2. wget https://github.com/jaedog/SMAVenusDriver/raw/master/install/install.sh
3. chmod +x install.sh
4. ./install.sh
5. answer Y to the install of the driver
6. answer Y to the dependencies (unless they are already installed)

Depending on the CAN adapter used or how configured, install of driver is slightly different
#### slcan (tty)
This method uses the VE Serial Starter method as described on their wiki below.

Unsupported by default

#### socketcan (socket)
This method uses daemontools (https://cr.yp.to/daemontools.html) to supervise and start the driver aka service.

Note: use/study the install.sh script

1. To bring up the canable CAN adapter automatically, copy the include/99-candlelight.rules to the /etc/udev/rules.d directory
2. Modify the 99-candlelight.rules file with the serial number of your device.
	Execute: usb-devices | grep -A2 canable.io
3. Copy the dbus-sma directory to /data/etc
4. Make a symbolic link in the /service directory to the /data/etc/dbus-sma/service with the name dbus-sma
	> ln -s /data/etc/dbus-sma/service /service/dbus-sma
6. Important: Make sure that there isn't a /data/etc/dbus-sma/service/down file. This keeps the service from starting automatically
7. To enable service logging, remove the /data/etc/dbus-sma/service/down file. To watch logs in real time:
	> tail -F /var/log/dbus-sma/current


## Useful Reading

#### Victron
See https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus for info.

See https://www.victronenergy.com/live/ccgx:root_access#:~:text=Go%20to%20Settings%2C%20General,Access%20Level%20change%20to%20Superuser for the way to gain root access to your Venus OS device. 

See https://github.com/victronenergy/venus/wiki/installing-additional-python-modules to add python modules needed for this driver to work. Like python-can .

#### SMA
SMA SI Manual: https://files.sma.de/downloads/SI4548-6048-US-BE-en-21W.pdf

Page 53, Section 6.4.2 Connecting the Data Cable of the Lithium-Ion Batteries details where to connect the RJ45 CAN cable

## Todo List

 1)	Proper charge controller state machine
 2)	Move configuration values (charge current, voltage thresholds, etc) to the Victron settings structure. 
 3)	Create GUI in WEB_UI to change settings or trigger actions. 
 4)	Convert polling CAN adapter to proper callback when new CAN message arrives.
 5)	Get logging working correctly. 

## CAN Bus
The Controller Area Network (CAN bus) is used at a rate of 500 kbs.

NOTE: The SMA SI will go into hard shutdown mode if it hasn't received a good BMS message after several minutes. If this happens you will need to power off the DC side of the inverter and wait for 15-30 min capacitors to drain. If the cover is off, you can monitor the red LED located left and down of the center control panel. When it goes off it can be powered on.

### CAN Adapter
The SMA SI use the CAN bus to communicate between master/slave and other devices. In order to participate on the CAN bus, you must have a CAN adapter. The CAN adapter I use is the open source USB CANable device (https://canable.io/). I'm using the Pro version since it adds galvanic isolation, but either will work. The firmware installed from ProtoFusion store is slcan, which emmulates a tty serial device. I've had issues with this being stable within this environment. I'm not sure if it is buffer or timing issues. However, by installing the candlelight firmware, the adapter becomes a socketcan device and works like a network adapter. This method is rock solid in my usage.

#### CAN Pinouts
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


## Victron VenusOS Notes

So what I'm doing here is pretending to be a proper Victron Multiplus inverter/charger so that the rest of the eco-system (web-ui, VRM portal, etc) grabs the data and displays/logs it. Victron is amazing at letting Venus OS be open source AND documenting it VERY well so that hackers can have at it. Yeah, they are understandably not thrilled I'm using a third-party device with their free stuff, but aren't against me doing it and specifically didn't ask me to stop (I offered). So, go buy Victron stuff, even if you already have an SMA inverter. I have 4 of their solar charge controllers and love them!

That said, I'm not sure if I've emulated the Multiplus very well or in every way that I could. I did manage to reverse engineer the energy counting architecture so usage data should appear in the portal. But there are some quarks trying to do a 1-1 map SMA to Victron. For example, the SMA reports inverter power flow and AC-2 (external) power flow, but not output power flow implicitly per line. So I had to do math (inverter power, External power, and output power should always sum to 0, right) to get that value. There can be artifacts in the real time data because of that. SMA also doesn’t report energy at all, so my code is calculating that from power data to the best of it’s ability. 


## Hacking the SunnyIsland Notes

The SMA SunnyIsland 6048 has two potential communications buses. One is a CAN bus “ComSync” and the other is a RS-485 bus "ComSma" that requires an adapter card to be installed. The CAN bus is used by the SMA’s to communicate from the master to the slaves in a cluster, and to a Battery Management System (BMS) when configure in Lithium Ion mode. The RS-485 bus is required to connect to SMA grid tie inverters and to the WebBox.

It's clear the RS-485 was always the intended bus to connect to logging and telemetry systems such as the discontinued Sunny WebBox that allows you to see system telemetry on the SMA portal. But as they are very expensive now that they are discontinued, it isn’t a good option.

I started dumping the CAN bus to see what was there. There is of course a lot of high frequency messages I’m sure are used to sync up master and slave units, as well as the BMS traffic which is documented on page 10 of this BMS manual: http://www.rec-bms.com/datasheet/UserManual9R_SMA.pdf

BUT I also noticed there were some bytes in some messages moving in ways that appeared to correlate to system metrics. And indeed, they did. However, resolution is rather low, all power metrics are reported in 100s of watts. I realized this matched what is shown on the inverter screen, and then a light went off. You can buy the SMA “SunnyRemote” box which also connects by CAN bus. So these messages must be the system data meant for the “SunnyRemote” which has the same screen and menu as the local screen on the inverters. 

SO what this codes is doing is broken down into to big parts. First, it needs to pretend to be a BMS so the SunnyIslands will ingest battery SoC and charge current commands. Second, it is listening for the traffic intended for the “SunnyRemote” box so we can use it to extract ang log system metrics. All this is done through the CAN bus, so no additional parts need to be ordered. 

 Speaking of the BMS. The SunnyIsland were originally designed to use Lead Acid batteries, only. Lithium-ion support was added later. And let me be clear, what they say by Lithium-ion “support” means the SunnyIsland turns super dumb and just does what it’s told by the BMS. It disables any and all charger controller systems internally. No CC-CV, absorption phase, float phase, voltage thresholds, nothing. So the ”BMS” (like we are pretending to be) must have all that logic built into it. Since I’m DC-coupled Solar that’s not a huge problem for me, as during normal use the SunnyIsland isn’t involved with charging. However AC-coupled Solar users will need to pay special attention to this.
 
So the Victron system (or whatever you use this code with) will needs its own battery monitor or device to measure/calculate the SoC at a minimum, plus you have to hard code some voltage limits to makeup the minimum BMS messages the SunnyIsland requires. I recommend the Smart Shunt (https://www.victronenergy.com/battery-monitors/smart-battery-shunt) or the BMV-712 (https://www.victronenergy.com/battery-monitors/bmv-712-smart) connected to the Raspberry Pi with the VE.Direct USB cable. Note: The VE.Direct interface on these devices are 3.3V

## Final Words
Also note right now there’s a bunch of stuff hard-coded for my application, which is an off-grid (with grid available during low battery) with DC tied solar setup. I can’t test AC tied solar yet. 

In case it wasn’t obvious, one fall back with this hack is if the Raspberry pi crashes or shuts off, the inverters will shut off as well. I recommend you have an offline back-up raspberry pi setup and ready to go to swap out in that event. 

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

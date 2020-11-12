# SMAVenusDriver
A driver to integrate SMA SunnyIsland inverters with Victron Venus OS
install directory includes the driver folder and everything that is needed. 
include directory has changes to serial-starter as required to make this plug and play. 
See https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus for info.
See https://www.victronenergy.com/live/ccgx:root_access#:~:text=Go%20to%20Settings%2C%20General,Access%20Level%20change%20to%20Superuser for the way to gain root access to your Venus OS device. 

TODO list:
 1)	Proper charge controller state machine
 2)	Move configuration values (charge current, voltage thresholds, etc) to the Victron settings structure. 
 3)	Create GUI in WEB_UI to change settings or trigger actions. 
 4)	Convert polling CAN adapter to proper callback when new CAN message arrives.
 5)	Get logging working correctly. 
 
CAN adapter I used: https://canable.io/

Hacking the SunnyIsland Notes

The SMA SunnyIsland 6048 has two potential communications buses. One is a CAN bus “ComSync” and the other is a RS-485 bus "ComSma" that requires an adapter card to be installed. The CAN bus is used by the SMA’s to communicate from the master to the slaves in a cluster, and to a Battery Management System (BMS) when configure in Lithium Ion mode. The RS-485 bus is required to connect to SMA grid tie inverters and to the WebBox.

It's clear the RS-485 was always the intended bus to connect to logging and telemetry systems such as the discontinued Sunny WebBox that allows you to see system telemetry on the SMA portal. But as they are very expensive now that they are discontinued, it isn’t a good option.

I started dumping the CAN bus to see what was there. There is of course a lot of high frequency messages I’m sure are used to sync up master and slave units, as well as the BMS traffic which is documented on page 10 of this BMS manual: http://www.rec-bms.com/datasheet/UserManual9R_SMA.pdf

BUT I also noticed there were some bytes in some messages moving in ways that appeared to correlate to system metrics. And indeed, they did. However, resolution is rather low, all power metrics are reported in 100s of watts. I realized this matched what is shown on the inverter screen, and then a light went off. You can buy the SMA “SunnyRemote” box which also connects by CAN bus. So these messages must be the system data meant for the “SunnyRemote” which has the same screen and menu as the local screen on the inverters. 

SO what this codes is doing is broken down into to big parts. First, it needs to pretend to be a BMS so the SunnyIslands will ingest battery SoC and charge current commands. Second, it is listening for the traffic intended for the “SunnyRemote” box so we can use it to extract ang log system metrics. All this is done through the CAN bus, so no additional parts need to be ordered. 

 Speaking of the BMS. The SunnyIsland were originally designed to use Lead Acid batteries, only. Lithium-ion support was added later. And let me be clear, what they say by Lithium-ion “support” means the SunnyIsland turns super dumb and just does what it’s told by the BMS. It disables any and all charger controller systems internally. No CC-CV, absorption phase, float phase, voltage thresholds, nothing. So the ”BMS” (like we are pretending to be) must have all that logic built into it. Since I’m DC-coupled Solar that’s not a huge problem for me, as during normal use the SunnyIsland isn’t involved with charging. However AC-coupled Solar users will need to pay special attention to this.
 
So the Victron system (or whatever you use this code with) will needs its own battery monitor or device to measure/calculate the SoC at a minimum, plus you have to hard code some voltage limits to makeup the minimum BMS messages the SunnyIsland requires. I recommend this guy. https://www.victronenergy.com/battery-monitors/smart-battery-shunt 

Also note right now there’s a bunch of stuff hard-coded for my application, which is an off-grid (with grid available during low battery) with DC tied solar setup. I can’t test AC tied solar yet. 

In case it wasn’t obvious, one fall back with this hack is if the Raspberry pi crashes or shuts off, the inverters will shut off as well. I recommend you have an offline back-up raspberry pi setup and ready to go to swap out in that event. 

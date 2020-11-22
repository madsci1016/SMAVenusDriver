#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
from bms_state_machine import BMSChargeStateMachine, BMSChargeModel, BMSChargeController

# 48V 16S LiFePO4 Battery

# Absorption: 58V (56.4 for longer life)
# Float: 54.4V
# Inverter Cut-off: 42.8V-48V (depending on size of load and voltage drop etc)

bms_controller = BMSChargeController(charge_cc=160, charge_cv=58.4, charge_float=54.4, time_hrs_cv=0.008) # or 30 seconds for the simulation
ret = bms_controller.start_charging()

print ("{0}, Start Charging: {1}".format(bms_controller, ret))

# simulated battery voltage
bat_voltage = 42.8

while (True):

  is_state_changed = bms_controller.update_battery_voltage(bat_voltage)
  state = bms_controller.get_state()
  charge_current = bms_controller.get_charge_current()
  
  print ("Battery Voltage: {0}, Charge Current: {1}, Charge State: {2}, State Changed: {3}".format(bat_voltage, charge_current, state, is_state_changed))

  time.sleep(1)
    
  # update simulated values
  if (is_state_changed):
    if (state == "cv_chg"):
      bat_voltage = 58.2
    elif (state == "float_chg"):
      bat_voltage = 56.1

  if (state == "cc_chg"):
    bat_voltage += 1.8
  elif (state == "cv_chg"):
    if (charge_current > 0):
      bat_voltage += charge_current * 0.1
    elif (charge_current == 0):
      bat_voltage -= 0.01
  elif (state == "float_chg"):
    if (charge_current > 0):
      bat_voltage += charge_current * 0.1
    elif (charge_current == 0):
      bat_voltage -= 0.03
    



#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
from bms_state_machine import BMSChargeStateMachine, BMSChargeModel, BMSChargeController

# 48V 16S LiFePO4 Battery

# Absorption: 58V (56.4 for longer life)
# Float: 54.4V
# Restart bulk voltage: Float-0.8 (max of 54V)
# Inverter Cut-off: 42.8V-48V (depending on size of load and voltage drop etc)

bms_controller = BMSChargeController(charge_bulk_current=160, charge_absorb_voltage=58.4, \
  charge_float_voltage=54.4, time_min_absorb=0.5, rebulk_voltage=53.6) # or 30 seconds for the simulation
ret = bms_controller.start_charging()

print ("{0}, Start Charging: {1}".format(bms_controller, ret))

# simulated battery voltage
bat_voltage = 42.8
counter = 0

while (True):

  charge_current = 0.0
  is_state_changed = bms_controller.update_battery_data(bat_voltage, charge_current)
  state = bms_controller.get_state()
  charge_current = bms_controller.get_charge_current()
  
  print ("Battery Voltage: {0}, Charge Current: {1}, Charge State: {2}, State Changed: {3}".format(bat_voltage, charge_current, state, is_state_changed))

  time.sleep(1)
  
    
  # update simulated values
  if (is_state_changed):
    if (state == "absorb_chg"):
      bat_voltage = 58.2
    elif (state == "float_chg"):
      bat_voltage = 56.1

  if (state == "bulk_chg"):
    bat_voltage += 1.8
  elif (state == "absorb_chg"):
    if (charge_current > 0):
      bat_voltage += charge_current * 0.1
    elif (charge_current == 0):
      bat_voltage -= 0.01
    if (counter > 5):
      counter += 1
    if (counter > 15):
      bat_voltage = 54
      counter = 0
  elif (state == "float_chg"):
    counter += 1
    if (counter > 5) :
      bat_voltage = 53
      
    if (charge_current > 0):
      bat_voltage += charge_current * 0.1
    elif (charge_current == 0):
      bat_voltage -= 0.03
    



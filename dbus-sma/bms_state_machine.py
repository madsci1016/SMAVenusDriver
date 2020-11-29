#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""bms_state_machine.py: Statemachine to control the charge profile of the
                SMA SunnyIsland inverters with Victron Venus OS. """

__author__      = "github usernames: jaedog"
__copyright__   = "Copyright 2020"
__license__     = "MIT"
__version__     = "0.1"

from statemachine import StateMachine, State
from datetime import datetime, timedelta

# State machine class, handles state changes as uses 
# https://github.com/rschrader/python-statemachine
# install with pip:
# $ pip install python-statemachine
#

class BMSChargeStateMachine(StateMachine):
  idle = State("Idle", initial=True)#, value=1)
  bulk_chg = State("ConstCurChg")#, value=2)
  absorb_chg = State("ConstVoltChg")#, value=3)
  float_chg = State("FloatChg")#, value=4)
  canceled = State("CancelChg")#, value=5)

  bulk = idle.to(bulk_chg)
  absorb = bulk_chg.to(absorb_chg)
  floating = absorb_chg.to(float_chg)
  rebulk = bulk_chg.from_(absorb_chg, float_chg)
  cancel = canceled.from_(bulk_chg, absorb_chg, float_chg)

  # canceled is the final state and cannot be cycled back to idle
  # create a new state machine to restart the charge cycle
  cycle = bulk | absorb | floating
  
  def on_enter_idle(self):
    if (getattr(self.model, "on_enter_idle", None) != None):
      self.model.on_enter_idle()

  def on_enter_bulk_chg(self):
    if (getattr(self.model, "on_enter_bulk_chg", None) != None):
      self.model.on_enter_bulk_chg()

  def on_enter_absorb_chg(self):
    if (getattr(self.model, "on_enter_absorb_chg", None) != None):
      self.model.on_enter_absorb_chg()
      
  def on_enter_float_chg(self):
    if (getattr(self.model, "on_enter_float_chg", None) != None):
      self.model.on_enter_float_chg()

# Charge Model, contains the model of the bms charger
class BMSChargeModel(object):
  def __init__(self, charge_bulk_current, charge_absorb_voltage, \
     charge_float_voltage, time_min_absorb, rebulk_voltage):
    self.charge_absorb_voltage = charge_absorb_voltage
    self.charge_bulk_current = charge_bulk_current
    self.original_bulk_current = charge_bulk_current
    self.charge_float_voltage = charge_float_voltage
    self.time_min_absorb = time_min_absorb
    self.rebulk_voltage = rebulk_voltage

    self.actual_current = 0.0
    self.actual_voltage = 0.0
    
    # init callback
    self.check_state = self.check_idle_state

  # event callbacks when entering different states    
  def on_enter_idle(self):
    self.check_state = self.check_idle_state
    
  def on_enter_bulk_chg(self):
    self.check_state = self.check_bulk_chg_state

  def on_enter_absorb_chg(self):
    self.check_state = self.check_absorb_chg_state
    self.start_of_absorb_chg = datetime.now()
        
  def on_enter_float_chg(self):
    self.check_state = self.check_float_chg_state

  # functions used for logic on various states
  def check_idle_state(self):
    print("check_idle_state")

  def check_bulk_chg_state(self):
    #print("check_bulk_chg_state")
    self.actual_current = self.charge_bulk_current
    if (self.actual_voltage >= self.charge_absorb_voltage):
      self.actual_current = 0
      # move to next state
      return 1
    return 0

  def check_absorb_chg_state(self):
    #print("check_absorb_chg_state")

    # if voltage falls below float voltage, go back to bulk
    if (self.actual_voltage < self.charge_float_voltage):
      return -1

    if (datetime.now() - self.start_of_absorb_chg > timedelta(minutes=self.time_min_absorb)):
      return 1

    if (self.actual_voltage >= self.charge_absorb_voltage):
      self.actual_current = 0.6
    elif (self.actual_voltage < self.charge_absorb_voltage):
      self.actual_current += 0.1
      if (self.actual_current > 4.0):
        self.actual_current = 4.0

    # cap charge current to the bulk_chg state
    if (self.actual_current > self.charge_bulk_current):
      self.actual_current = self.charge_bulk_current

    return 0
    
  def check_float_chg_state(self):
    #print("check_float_chg_state")

    # if voltage falls below rebulk voltage, go back to bulk
    if (self.actual_voltage < self.rebulk_voltage):
      return -1

    if (self.actual_voltage >= self.charge_float_voltage):
      self.actual_current = 0.6
    elif (self.actual_voltage < self.charge_float_voltage):
      self.actual_current += 0.1
    return 0
    
# Charge controller, external interface to the bms state machine charger
class BMSChargeController(object):
  def __init__(self, charge_bulk_current, charge_absorb_voltage, \
    charge_float_voltage, time_min_absorb, rebulk_voltage):
    self.model = BMSChargeModel(charge_bulk_current, charge_absorb_voltage, \
      charge_float_voltage, time_min_absorb, rebulk_voltage)
    self.state_machine = BMSChargeStateMachine(self.model)
    
  def __str__(self):
    return "BMS Charge Config, CC: {0}A, CV: {1}V, CV Time: {2} hrs, Float: {3}V" \
      .format(self.model.charge_bulk_current, self.model.charge_absorb_voltage, \
        self.model.time_min_absorb, self.model.charge_float_voltage)
    
  def update_battery_voltage(self, voltage):
    self.model.actual_voltage = voltage
    return self.check_state()

  def update_req_bulk_current(self, current):
    if (current == None):
      self.model.charge_bulk_current  = self.model.original_bulk_current
    else:
      self.model.charge_bulk_current = current
  
  def start_charging(self):
    if (self.state_machine.current_state == self.state_machine.idle):
      self.state_machine.cycle()
      return True
    return False
    
  def is_charging(self):
    if ((self.state_machine.current_state == self.state_machine.bulk_chg) or
        (self.state_machine.current_state == self.state_machine.absorb_chg) or
        (self.state_machine.current_state == self.state_machine.float_chg)):
      return True
    return False
      
  def stop_charging(self):
    print ("stop_charging")
    self.state_machine.cancel()
    
  def check_state(self):
    #print ("check_state")
    val = self.model.check_state()
    if (val == 1):
      self.state_machine.cycle()
    elif (val == -1):
      # rebulk
      self.state_machine.rebulk()

    return val
    
  def get_charge_current(self):
    return self.model.actual_current
    
  def get_state(self):
    return self.state_machine.current_state.value
    
    
    
    
    
    
    
    
    
    
    
    

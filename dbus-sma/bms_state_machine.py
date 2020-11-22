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
  cc_chg = State("ConstCurChg")#, value=2)
  cv_chg = State("ConstVoltChg")#, value=3)
  float_chg = State("FloatChg")#, value=4)
  canceled = State("CancelChg")#, value=5)

  bulk = idle.to(cc_chg)
  absorb = cc_chg.to(cv_chg)
  floating = cv_chg.to(float_chg)
  cancel = canceled.from_(cc_chg, cv_chg, float_chg)

  # canceled is the final state and cannot be cycled back to idle
  # create a new state machine to restart the charge cycle
  cycle = bulk | absorb | floating
  
  def on_enter_idle(self):
    if (getattr(self.model, "on_enter_idle", None) != None):
      self.model.on_enter_idle()

  def on_enter_cc_chg(self):
    if (getattr(self.model, "on_enter_cc_chg", None) != None):
      self.model.on_enter_cc_chg()

  def on_enter_cv_chg(self):
    if (getattr(self.model, "on_enter_cv_chg", None) != None):
      self.model.on_enter_cv_chg()
      
  def on_enter_float_chg(self):
    if (getattr(self.model, "on_enter_float_chg", None) != None):
      self.model.on_enter_float_chg()

# Charge Model, contains the model of the bms charger
class BMSChargeModel(object):
  def __init__(self, charge_cc, charge_cv, charge_float, time_hrs_cv):
    self.charge_cv = charge_cv
    self.charge_cc = charge_cc
    self.charge_float = charge_float
    self.time_hrs_cv = time_hrs_cv

    self.actual_current = 0.0
    self.actual_voltage = 0.0
    
    # init callback
    self.check_state = self.check_idle_state

  # event callbacks when entering different states    
  def on_enter_idle(self):
    self.check_state = self.check_idle_state
    
  def on_enter_cc_chg(self):
    self.check_state = self.check_cc_chg_state

  def on_enter_cv_chg(self):
    self.check_state = self.check_cv_chg_state
    self.start_of_cv_chg = datetime.now()
        
  def on_enter_float_chg(self):
    self.check_state = self.check_float_chg_state

  # functions used for logic on various states
  def check_idle_state(self):
    print("check_idle_state")

  def check_cc_chg_state(self):
    #print("check_cc_chg_state")
    self.actual_current = self.charge_cc
    if (self.actual_voltage >= self.charge_cv):
      self.actual_current = 0
      # move to next state
      return True
    return False

  def check_cv_chg_state(self):
    #print("check_cv_chg_state")
    if (datetime.now() - self.start_of_cv_chg > timedelta(hours=self.time_hrs_cv)):
      return True

    if (self.actual_voltage >= self.charge_cv):
      self.actual_current = 0.0
    elif (self.actual_voltage < self.charge_cv):
      self.actual_current += 0.1
    return False
    
  def check_float_chg_state(self):
    #print("check_float_chg_state")
    if (self.actual_voltage >= self.charge_float):
      self.actual_current = 0.0
    elif (self.actual_voltage < self.charge_float):
      self.actual_current += 0.1
    return False
    
# Charge controller, external interface to the bms state machine charger
class BMSChargeController(object):
  def __init__(self, charge_cc, charge_cv, charge_float, time_hrs_cv):
    self.model = BMSChargeModel(charge_cc, charge_cv, charge_float, time_hrs_cv)
    self.state_machine = BMSChargeStateMachine(self.model)
    
  def __str__(self):
    return "BMS Charge Config, CC: {0}A, CV: {1}V, CV Time: {2} hrs, Float: {3}V" \
      .format(self.model.charge_cc, self.model.charge_cv, self.model.time_hrs_cv, \
      self.model.charge_float)
    
  def update_battery_voltage(self, voltage):
    self.model.actual_voltage = voltage
    return self.check_state()
  
  def start_charging(self):
    if (self.state_machine.current_state == self.state_machine.idle):
      self.state_machine.cycle()
      return True
    return False
    
  def is_charging(self):
    if ((self.state_machine.current_state == self.state_machine.cc_chg) or
        (self.state_machine.current_state == self.state_machine.cv_chg)):
      return True
    return False
      
  def stop_charging(self):
    print ("stop_charging")
    self.state_machine.cancel()
    
  def check_state(self):
    #print ("check_state")
    val = self.model.check_state()
    if (val):
      self.state_machine.cycle()
    return val
    
  def get_charge_current(self):
    return self.model.actual_current
    
  def get_state(self):
    return self.state_machine.current_state.value
    
    
    
    
    
    
    
    
    
    
    
    

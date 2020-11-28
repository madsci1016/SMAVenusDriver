from signal import signal, SIGINT
from sys import exit
import can
import serial
import time

#
# when board configured as socketcan, bring up link first:
# sudo ip link set can0 up type can bitrate 500000
#
#

def getSignedNumber(number, bitLength):
    mask = (2 ** bitLength) - 1
    if number & (1 << (bitLength - 1)):
        return number | ~mask
    else:
        return number & mask

def getbytes(integer):
    return divmod(integer, 0x100)
    
def handler(signal_received, frame):
    # Handle any cleanup here
    print('SIGINT or CTRL-C detected. Exiting gracefully')
    exit(0)

if __name__ == "__main__":
  # Tell Python to run the handler() function when SIGINT is recieved
  signal(SIGINT, handler)

  print('Running. Press CTRL-C to exit.')

  print ("Can bus init")
  canBusTTY = "/dev/ttyACM0"
  canBus = can.interface.Bus(bustype='socketcan', channel="can5", bitrate=500000)

  print ("Can bus init done")

  while True:
    # Do nothing and hog CPU forever until SIGINT received.
    
    SoC_HD = 99.5
    Soc = int(SoC_HD)
    Req_Charge_HD = 100
    Req_Charge_A = 100.0
    Req_Discharge_HD = 30
    Req_Discharge_A = 200.0
    Max_V_HD = 56
    Max_V = 60.0
    Min_V = 46.0
 
    
   #breakup some of the values for CAN packing
    SoC_HD = int(SoC_HD*100)
    SoC_HD_H, SoC_HD_L = getbytes(SoC_HD)
    Req_Charge_HD = int(Req_Charge_A*10)
    Req_Charge_H, Req_Charge_L = getbytes(Req_Charge_HD)
    Req_Discharge_HD = int(Req_Discharge_A*10)
    Req_Discharge_H, Req_Discharge_L = getbytes(Req_Discharge_HD)
    Max_V_HD = int(Max_V*10)
    Max_V_H, Max_V_L = getbytes(Max_V_HD)
    Min_V_HD = int(Min_V*10)
    Min_V_H, Min_V_L = getbytes(Min_V_HD)


    msg = can.Message(arbitration_id=0x351, 
      data=[Max_V_L, Max_V_H, Req_Charge_L, Req_Charge_H, Req_Discharge_L, Req_Discharge_H, Min_V_L, Min_V_H],
      is_extended_id=False)
    msg2 = can.Message(arbitration_id=0x355,
      data=[Soc, 0x00, 0x64, 0x0, SoC_HD_L, SoC_HD_H],
      is_extended_id=False)
    msg3 = can.Message(arbitration_id=0x356,
      data=[0x00, 0x00, 0x00, 0x0, 0xf0, 0x00],
      is_extended_id=False)
    msg4 = can.Message(arbitration_id=0x35a,
      data=[0x00, 0x00, 0x00, 0x0, 0x00, 0x00, 0x00, 0x00],
      is_extended_id=False)
    msg5 = can.Message(arbitration_id=0x35e,
      data=[0x42, 0x41, 0x54, 0x52, 0x49, 0x55, 0x4d, 0x20],
      is_extended_id=False)
    msg6 = can.Message(arbitration_id=0x35f,
      data=[0x03, 0x04, 0x0a, 0x04, 0x76, 0x02, 0x00, 0x00],
      is_extended_id=False)
    
    try:
        canBus.send(msg)
        #print("Message 0x351 sent on {}".format(canBus.channel_info))
        time.sleep(.100)

        canBus.send(msg2)
        #print("Message 0x355 sent on {}".format(canBus.channel_info))
        time.sleep(.100)

        canBus.send(msg3)
        #print("Message 0x356 sent on {}".format(canBus.channel_info))
        time.sleep(.100)

        canBus.send(msg4)
        #print("Message 0x35a sent on {}".format(canBus.channel_info))
        time.sleep(.100)

        canBus.send(msg5)
        #print("Message 0x35e sent on {}".format(canBus.channel_info))
        time.sleep(.100)
        
        canBus.send(msg6)
        #print("Message 0x35f sent on {}".format(canBus.channel_info))
        
        print("Sent 6 frames on {}".format(canBus.channel_info))
    
    except (can.CanError) as e:
      print("CAN BUS Transmit error (is controller missing?): %s" % e.message)

    time.sleep(2)    
    pass




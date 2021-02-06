#!/bin/bash

# for testing purposes
#ROOT_DIR="/tmp/venus"
#mkdir -p ${ROOT_DIR}/data/etc
#mkdir -p ${ROOT_DIR}/var/log
#mkdir -p ${ROOT_DIR}/service
#mkdir -p ${ROOT_DIR}/etc/udev/rules.d
ROOT_DIR=""

# download this script:
# wget https://github.com/madsci1016/SMAVenusDriver/raw/master/install/install.sh

echo
echo "Please ensure your socketcan enable canable USB adapter is plugged into the Venus"
echo "If your canable.io adapter is using factory default slcan firmware, exit this"
echo "script and install the \"candlelight\" firmware"
echo
echo "This script requires internet access to install dependencies and software."
echo
echo "Install SMA Sunny Island driver (w/ virtual BMS) on Venus OS at your own risk?"
read -p "[Y to proceed] " -n 1 -r

echo    # (optional) move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
  echo "Install dependencies (pip and python libs)?"
  read -p "[Y to proceed] " -n 1 -r
  echo    # (optional) move to a new line
  if [[ $REPLY =~ ^[Yy]$ ]]
  then

    echo "==== Download and install dependencies ===="
    opkg update
    opkg install python-misc python-distutils python-numbers python-html python-ctypes python-pkgutil
    opkg install python-unittest python-difflib python-compile gcc binutils python-dev python-unixadmin python-xmlrpc

    wget https://bootstrap.pypa.io/2.7/get-pip.py
    python get-pip.py
    rm get-pip.py

    pip install python-can
    pip install python-statemachine
    pip install pyyaml
  fi

	echo "==== Download driver and library ===="

	wget https://github.com/madsci1016/SMAVenusDriver/archive/master.zip
	unzip -qo master.zip
	rm master.zip

	echo "==== Add canable device to udev ===="

  error_msg="WARNING: Was unable to modify 99-candlelight.rules with device serial number automatically"

  template_udev_file="SMAVenusDriver-master/install/99-candlelight.rules"
  udev_file="${ROOT_DIR}/etc/udev/rules.d/99-candlelight.rules"
  cp $template_udev_file $udev_file

  # grab the details of the canable usb adapter
  value=`usb-devices | grep -A2 canable.io`

  # get the serial number of the canable usb adapter
  var="$(cut -d'=' -f 2 <<< $value)"
  set -- $var
  serial=$4 
  
  # the serial number is 24 digits long, if the parse doesn't meet that 
  # requirement toss the results and bail on task.
  if [ ${#serial} -eq 24 ]; then

    echo "Found canable.io serial number: $serial"
    # replace the template serial number with the actual device serial number
    sed -i "s/000000000000000000000000/$serial/g" "$udev_file"

    diff $template_udev_file $udev_file > /dev/null 2>&1
    error=$?

    if [ $error -eq 0 ]; then
      echo $error_msg
    fi
	else
    echo $error_msg
  fi
  
	echo "==== Install SMA SI driver ===="
	DBUS_NAME="dbus-sma"
	DBUS_SMA_DIR="${ROOT_DIR}/data/etc/${DBUS_NAME}"

	mkdir -p ${ROOT_DIR}/var/log/${DBUS_NAME}
	mkdir -p ${DBUS_SMA_DIR}
	cp -R  SMAVenusDriver-master/dbus-sma/* ${ROOT_DIR}/data/etc/${DBUS_NAME}

  # replace inverter svg with custom yellow sunny island svg
  cp SMAVenusDriver-master/assets/overview-inverter.svg ${ROOT_DIR}/opt/victronenergy/themes/ccgx/images

	chmod +x ${ROOT_DIR}/data/etc/${DBUS_NAME}/dbus-sma.py
	chmod +x ${ROOT_DIR}/data/etc/${DBUS_NAME}/service/run
	chmod +x ${ROOT_DIR}/data/etc/${DBUS_NAME}//service/log/run
	ln -s ${ROOT_DIR}/opt/victronenergy/vrmlogger/ext/ ${DBUS_SMA_DIR}/ext 
	ln -s ${DBUS_SMA_DIR}/service ${ROOT_DIR}/service/${DBUS_NAME}

  # remove archive files
  rm -rf SMAVenusDriver-master/

  echo
	echo "To finish, reboot the Venus OS device"
fi

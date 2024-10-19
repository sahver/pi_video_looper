#!/bin/bash

#
# https://gist.github.com/itapai/a18e58c04c5b67e936c8ad6fc374eb14
#

[[ $EUID -ne 0 ]] && echo "This script must be run as root." && exit 1

OMXPLAYER='omxplayer_20190723+gitf543a0d-1+bullseye_armhf.deb'

wget -P /tmp https://archive.raspberrypi.org/debian/pool/main/o/omxplayer/${OMXPLAYER}
dpkg -i /tmp/${OMXPLAYER}
apt install -y --fix-broken
rm /tmp/${OMXPLAYER}

# These do not seem to be problematic
#cd /usr/lib/arm-linux-gnueabihf
#ln -s libmmal_core.so.0 libmmal_core.so
#ln -s libmmal_util.so.0 libmmal_util.so
#ln -s libmmal_vc_client.so.0 libmmal_vc_client.so
#ln -s libbcm_host.so.0 libbcm_host.so
#ln -s libvcsm.so.0 libvcsm.so
#ln -s libvchiq_arm.so.0 libvchiq_arm.so
#ln -s libvcos.so.0 libvcos.so

#wget 'https://github.com/raspberrypi/firmware/raw/oldstable/opt/vc/lib/libbrcmEGL.so'
#wget 'https://github.com/raspberrypi/firmware/raw/oldstable/opt/vc/lib/libbrcmGLESv2.so'
#wget 'https://github.com/raspberrypi/firmware/raw/oldstable/opt/vc/lib/libopenmaxil.so'

# Should install required libraries
apt -y install libpcre3 fonts-freefont-ttf fbset libssh-4 python3-dbus
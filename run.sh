#!/bin/bash

echo
echo 'Käivitame videmängija..'
echo

#
# Moondume
#

source ~/.profile

# Tmuxile teadmiseks
export SHELL=/bin/bash


#
# Jaam
#

session='looper'

tmux has-session -t $session 2>/dev/null

if [ $? == 0 ]
then
	echo 'Videomängija töötab.'
else
	tmux new -d -s $session
	tmux send -t $session 'cd ~/pi_video_looper' ENTER
	tmux send -t $session 'sudo python3 -u -m Adafruit_Video_Looper.video_looper' ENTER
	echo 'Videomängija käivitatud..'
fi

#
# valma
#

echo
echo 'Valma.'
echo
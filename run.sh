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

	tmux send -t $session 'while true' ENTER
	tmux send -t $session 'do' ENTER
	
	tmux send -t $session '' ENTER

	tmux send -t $session 'FILE=.cloud_pull' ENTER
	tmux send -t $session 'if test -f "$FILE"' ENTER
	tmux send -t $session 'then' ENTER
	tmux send -t $session 'echo' ENTER
	tmux send -t $session 'echo "*** Uuendame koodi.. ***"' ENTER
	tmux send -t $session 'echo' ENTER
	tmux send -t $session 'git pull' ENTER
	tmux send -t $session 'sudo rm "$FILE"' ENTER
	tmux send -t $session 'fi' ENTER

	tmux send -t $session '' ENTER

	tmux send -t $session 'echo' ENTER
	tmux send -t $session 'echo "*** Käivitame.. ***"' ENTER
	tmux send -t $session 'echo' ENTER
	tmux send -t $session 'sudo python3 -u -m Adafruit_Video_Looper.video_looper' ENTER
	
	tmux send -t $session '' ENTER
	
	tmux send -t $session 'echo' ENTER
	tmux send -t $session 'echo "*** Miskit juhtus, taaskäivitame.. ***"' ENTER
	tmux send -t $session 'echo' ENTER
	tmux send -t $session '' ENTER
	tmux send -t $session 'sleep 5' ENTER

	tmux send -t $session '' ENTER
	
	tmux send -t $session 'done' ENTER
	
	echo 'Videomängija käivitatud..'
fi

#
# valma
#

echo
echo 'Valma.'
echo
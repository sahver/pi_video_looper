#!/bin/sh

# Error out if anything fails.
set -e

# Make sure script is run as root.
if [ "$(id -u)" != "0" ]; then
  echo "Must be run as root with sudo! Try: sudo ./run.sh"
  exit 1
fi

echo "Running video_looper program..."
echo "=================================="

python3 -u -m Adafruit_Video_Looper.video_looper

echo "Finished!"

#!/bin/bash

# Run from the folder of this script, so that config.py and select_mic.py are found
# no matter where it was started from.
cd "$(dirname "$0")" || exit 1

# stop all possible Threads from previous run. The pattern only matches a python
# process, so an editor that happens to have the file open is left alone.
pkill -f "[p]ython.*voice_typing\.py"

exec /usr/bin/env python3 voice_typing.py

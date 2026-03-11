#!/usr/bin/env python3

# Prints the names to put in config.priority_mic_names. It shares input_devices() with
# select_mic.py on purpose, so that what is listed here is what can be selected there.
from select_mic import input_devices

for device in input_devices():
    print(f"{device['index']}: {device['name']}")

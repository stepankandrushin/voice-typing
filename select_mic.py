#!/usr/bin/env python3

import sounddevice as sd
import config
import json

def input_devices():
    """
    Devices that can record. A speaker often carries the same name as the microphone
    next to it, so the two must not be confused. list_devices.py shows this same list,
    which is what makes the names it prints usable in config.priority_mic_names.
    """
    return [device for device in sd.query_devices() if device['max_input_channels'] > 0]

def select_mic():
    """
    Returns the index and name of the desired microphone in JSON format based on availability.
    Prioritize the microphones in config.priority_mic_names in order, if available.
    If none are available, fallback to config.fallback_mic_index
    """
    mic_info = {"index": config.fallback_mic_index, "name": "Default"}
    try:
        devices = input_devices()

        for preferred_mic in config.priority_mic_names:
            for device in devices:
                if device['name'] == preferred_mic:
                    mic_info = {"index": device["index"], "name": device["name"]}
                    return {"status": "success", **mic_info}

        return {"status": "success", **mic_info}

    except Exception as e:
        return {"status": "error", **mic_info, "message": str(e)}

if __name__ == "__main__":
    mic_selected = select_mic()
    print(json.dumps(mic_selected))

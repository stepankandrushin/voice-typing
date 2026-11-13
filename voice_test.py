#!/usr/bin/env python3

# In Mac OS Before run allow Settings-Microphone and Accessibility for iTerm and VSCode
# https://support.apple.com/en-us/102071
import os
import sys
import requests
from datetime import datetime
import json
import config

# Usage: python3 voice_test.py [recording.wav]
filename = sys.argv[1] if len(sys.argv) > 1 else 'sample.wav'

def printt(text):
    current_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Format current time
    print(f"{current_time} {text}")

def transcribe_audio_to_text(fname):
    printt(f"Transcribing {fname}...")
    with open(fname, 'rb') as f:
        response = requests.post(config.transcribe_server, files={'file': f})
    return json.loads(response.text)

if __name__ == '__main__':
    start_time = datetime.now()
    printt(f"Started main. transcribe_server={config.transcribe_server}")
    data = transcribe_audio_to_text(filename)
    text = data['text']
    language = data['language']
    printt(f"Language: {language} Got result: {text} Total time: {datetime.now()-start_time}")

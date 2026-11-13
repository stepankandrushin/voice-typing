#!/usr/bin/env python3

# In Mac OS Before run allow Settings-Microphone and Accessibility for iTerm and VSCode
# https://support.apple.com/en-us/102071

import io
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime

import pyautogui
import pyclip
import requests
import sounddevice as sd
from pynput import keyboard

import config
from select_mic import select_mic

app = 'voice-typing'
ver = '0.260806'

SAMPLERATE = 16000
BLOCKSIZE = 1000
SAMPLE_WIDTH = 2  # bytes per sample, because dtype is int16

# Disable the Fail-Safe, because we don't neet to stop a pyautogui script when mouse is moved to a corner of the screen
pyautogui.FAILSAFE = False


def option(name, default):
    """Read an optional config value, so an older config.py keeps working."""
    return getattr(config, name, default)


transcribe_timeout_s = option('transcribe_timeout_s', 60)
max_record_duration_s = option('max_record_duration_s', 300)
save_recordings = option('save_recordings', True)
keep_recordings_days = option('keep_recordings_days', 0)  # 0 - keep forever
send_token = option('send_token', False)


class MillisecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(MillisecondFormatter('%(asctime)s [%(threadName)s] %(message)s'))
log = logging.getLogger(app)
log.setLevel(logging.INFO)
log.addHandler(handler)


def redacted(url):
    return re.sub(r'(token=)[^&]*', r'\1***', url)


def build_server_url():
    query = f"languages={config.languages}&app={app}-{ver}"
    if send_token:
        # A config switch rather than an edit of this file, so that turning the token
        # on can't be confused with turning it off.
        token = option('token', '')
        if token:
            query += f"&token={token}"
        else:
            log.warning('send_token is set, but config.token is empty')
    return f"{config.transcribe_server}?{query}"


transcribe_server_url = build_server_url()


def loggable(text):
    """save_recordings is a promise about keeping no copy of the dictation, so it has
    to cover the log as well - not just the .wav file."""
    return text if save_recordings else f'<{len(text)} characters, not logged>'


# --- text substitutions -----------------------------------------------------

def compile_literal(substitutions):
    """The config keys are literal text, not regular expressions: escape them so
    that "Гид.Див" does not also match "ГидXДив". The replacement is returned by
    a function so that a "\\1" inside it is not read as a group reference."""
    return [(re.compile(re.escape(pattern), re.IGNORECASE),
             replacement if callable(replacement) else (lambda m, r=replacement: r))
            for pattern, replacement in substitutions.items()]


def warn_about_backreferences(substitutions):
    """The keys are matched literally now, so there are no groups left to reference.
    An old config that relied on that pastes a bare \\1, which is worth saying out loud."""
    for pattern, replacement in substitutions.items():
        if isinstance(replacement, str) and re.search(r'\\(\d|g<)', replacement):
            log.warning(f'Replacement for "{pattern}" looks like it expects a regular '
                        f'expression group: "{replacement}". It is pasted as is now - '
                        f'move the entry to substitutions_regex.')


warn_about_backreferences(config.substitutions_case_insensitive)
substitutions_case_insensitive = compile_literal(config.substitutions_case_insensitive)
substitutions_regex = [(re.compile(pattern), replacement)
                       for pattern, replacement in config.substitutions_regex.items()]


def apply_substitutions(text):
    for old, new in config.substitutions_case_sensitive.items():
        text = text.replace(old, new)
    for pattern, replacement in substitutions_case_insensitive:
        text = pattern.sub(replacement, text)
    for pattern, replacement in substitutions_regex:
        text = pattern.sub(replacement, text)
    return text


# --- pasting ----------------------------------------------------------------

def applescript_string(value):
    """Quote a Python string as an AppleScript string literal."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


mac_menu_as = '{' + ', '.join(
    '{' + applescript_string(item['edit']) + ', ' + applescript_string(item['paste']) + '}'
    for item in config.mac_menu) + '}'

# The text is never interpolated into this script - it is put on the clipboard from
# Python instead. Interpolating it would break on any quote in the dictation and
# would let the transcription server run arbitrary AppleScript.
paste_menu_script = f"""
tell application "System Events"
    set frontmostApp to name of the first application process whose frontmost is true
    tell process frontmostApp
        set menuItems to {mac_menu_as}
        set didPaste to false
        repeat with menuItem in menuItems
            try
                click menu item (item 2 of menuItem) of menu 1 of menu bar item (item 1 of menuItem) of menu bar 1
                set didPaste to true
                exit repeat
            on error
                -- continue to next iteration
            end try
        end repeat
        if didPaste then
            return "success"
        else
            return "fail"
        end if
    end tell
end tell
"""


def paste(text):
    saved_clipboard = None
    try:
        # As text: on Windows pyclip.paste() returns UTF-8 bytes, and restoring bytes
        # puts them back as a DIB instead of text, losing the previous clipboard.
        saved_clipboard = pyclip.paste(text=True)
    except Exception as e:
        log.warning(f'Could not read the clipboard: {e}')

    try:
        paste_from_clipboard(text)
    finally:
        # In a finally, because leaving the dictation on the clipboard would destroy
        # whatever the user had copied before.
        if saved_clipboard is not None:
            try:
                pyclip.copy(saved_clipboard)
            except Exception as e:
                log.warning(f'Could not restore the clipboard: {e}')


def paste_from_clipboard(text):
    pyclip.copy(text)

    # Try to paste using AppleScript. If it fails, use pyautogui. AppleScript by clicking menu Edit->Paste is more reliable, then presing Cmd+V
    if sys.platform == 'darwin':  # This checks if the OS is MacOS
        result = subprocess.run(["osascript", "-e", paste_menu_script], capture_output=True, text=True)
        pasted = result.returncode == 0 and result.stdout.strip() == 'success'
        log.info(f"AppleScript {result.stdout.strip() or result.stderr.strip()}")
        # We don't use AppleScript to Paste ```keystroke "v" using {{command down}}``` because it's only working in english layout
        if not pasted:
            log.info('AppleScript failed to click menu Paste. Trying pyautogui Cmd+V')
            if config.v_delay > 0:
                time.sleep(config.v_delay)
            with pyautogui.hold(['command']):
                time.sleep(1)
                pyautogui.press('v')
        time.sleep(config.restore_clipborad_delay)
    else:
        if config.v_delay > 0:
            time.sleep(config.v_delay)
        pyautogui.hotkey('ctrl', 'v')
        # Give the focused app time to read the clipboard before paste() restores
        # the previous contents, otherwise it pastes the old text.
        time.sleep(config.restore_clipborad_delay)


# --- recordings on disk -----------------------------------------------------

def build_wav(audio):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLERATE)
        wf.writeframes(audio)
    return buffer.getvalue()


# Only files written by save_audio_to_file are ever deleted by prune_recordings,
# so pointing recordings_dir at a folder that holds other .wav files is harmless.
recording_name = re.compile(r'record_\d{8}-\d{6}(-\d+)?\.wav$')


def save_audio_to_file(wav_bytes):
    # One timestamp for both, otherwise a recording that ends at midnight is filed
    # under the previous day.
    now = datetime.now()
    # Store year/month/day in separate folders
    recordings_dir = os.path.join(config.recordings_dir, now.strftime('%Y/%m/%d'))
    os.makedirs(recordings_dir, exist_ok=True)
    # Microseconds in the name, so two recordings in the same second don't overwrite each other
    filename = os.path.join(recordings_dir, now.strftime('record_%Y%m%d-%H%M%S-%f.wav'))
    with open(filename, 'wb') as f:
        f.write(wav_bytes)
    return filename


last_prune = None


def prune_recordings():
    """Delete recordings older than config.keep_recordings_days. They are recordings
    of everything ever dictated, so keeping them forever is rarely what you want."""
    global last_prune
    if keep_recordings_days <= 0 or not os.path.isdir(config.recordings_dir):
        return
    if last_prune is not None and time.monotonic() - last_prune < 3600:  # at most once an hour
        return
    last_prune = time.monotonic()
    cutoff = time.time() - keep_recordings_days * 86400
    root_dir = os.path.abspath(config.recordings_dir)
    removed = 0
    for dirpath, _, filenames in os.walk(root_dir, topdown=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if recording_name.match(name) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError as e:
                log.warning(f'Could not delete {path}: {e}')
        if os.path.abspath(dirpath) != root_dir:
            try:
                os.rmdir(dirpath)  # only succeeds while the folder is empty
            except OSError:
                pass
    if removed:
        log.info(f'Deleted {removed} recordings older than {keep_recordings_days} days')


# --- transcription ----------------------------------------------------------

def transcribe_audio_to_text(wav_bytes, filename):
    log.info(f"Transcribing {filename}...")
    try:
        response = requests.post(transcribe_server_url,
                                 files={'file': (filename, wav_bytes, 'audio/wav')},
                                 timeout=transcribe_timeout_s)
        log.info(f"Transcription response: {loggable(response.text)}")
        if not response.ok:
            # Otherwise a 401 or a 500 with a json body is reported as "No text detected"
            log.error(f'Transcription server returned HTTP {response.status_code}')
            return '', ''
        data = response.json()
    except requests.RequestException as e:
        log.error(f"Transcription request failed: {e}")
        return '', ''
    except ValueError as e:  # includes json.JSONDecodeError
        log.error(f"Error decoding JSON response: {e}")
        return '', ''
    if not isinstance(data, dict):
        log.error(f'Unexpected transcription response: {data!r}')
        return '', ''
    return data.get('text') or '', data.get('language') or ''


# --- microphone -------------------------------------------------------------

# Held while PortAudio is torn down and rebuilt, and while a stream is being opened.
# Those two must never overlap: re-initialising underneath a live stream crashes.
audio_lock = threading.Lock()


def reinitialise_portaudio():
    """sounddevice caches the device list, so a microphone plugged in after start is
    invisible to it. Re-initialising PortAudio drops that cache in process - the app
    used to restart itself instead. The caller must hold audio_lock."""
    try:
        sd._terminate()
    except Exception as e:
        log.warning(f'Could not shut down the audio library: {e}')
    try:
        # Separate from the terminate above: skipping this would leave PortAudio down
        # for the rest of the process, and no recording would work again.
        sd._initialize()
    except Exception as e:
        log.error(f'Could not re-initialise the audio library: {e}')


def refresh_audio_devices():
    with audio_lock:
        reinitialise_portaudio()


def pick_microphone():
    mic = select_mic()
    if mic.get('status') == 'error':
        log.warning(f"Could not query microphones: {mic.get('message')}")
    return mic


# --- recorder ---------------------------------------------------------------

class Recorder:
    """Records while the hotkey is held, then transcribes and pastes the result."""

    def __init__(self):
        self.lock = threading.Lock()
        self.audio_queue = queue.Queue()
        self.stream = None
        self.recording = False  # the hotkey is still held down
        self.busy = False       # a dictation is in flight
        self.trigger = None     # which hotkey started this recording
        self.mic = None

    def start(self, trigger):
        with self.lock:  # This will prevent multiple threads from starting at the same time.
            if self.busy:
                return
            self.busy = True
            # Set before the thread starts, otherwise a quick tap releases the key
            # before the thread runs and nothing ever stops the recording.
            self.recording = True
            self.trigger = trigger
        # Not a daemon: Ctrl+C during transcription should still finish the paste and
        # put the clipboard back, the way it did before.
        try:
            threading.Thread(target=self.run, name='record').start()
        except Exception:
            # run() and its finally never happen, so release the state here instead.
            log.exception('Could not start the recording thread')
            self.reset()

    def stop(self):
        self.recording = False  # This will terminate the recording loop.

    def callback(self, indata, frames, time_info, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            log.warning(f'Audio input status: {status}')
        self.audio_queue.put(bytes(indata))

    def run(self):
        try:
            self.record_and_type()
        except Exception:
            log.exception('Dictation failed')
        finally:
            # Always released, so one failure can't wedge the app for good.
            self.close_stream()
            # Released before the refresh below, so the hotkey answers again straight
            # away instead of being dead for as long as CoreAudio takes to come back.
            self.reset()
            self.refresh_devices_when_idle()

    def refresh_devices_when_idle(self):
        """Pick up microphones plugged in since the last dictation. Skipped when the
        next one has already started - that thread is about to open a stream, and
        tearing PortAudio down underneath it would crash. It cannot open that stream
        while this holds audio_lock, so the two orderings are both safe."""
        with audio_lock:
            if self.busy:
                return
            reinitialise_portaudio()

    def reset(self):
        self.recording = False
        self.trigger = None
        self.busy = False

    def record_and_type(self):
        self.drain_queue()
        start_time = datetime.now()
        self.stream = self.open_stream()
        log.info('Started recording audio...')

        deadline = time.monotonic() + max_record_duration_s
        while self.recording:
            if time.monotonic() > deadline:
                log.warning(f'Recording is longer than {max_record_duration_s} s, stopping it.')
                break
            time.sleep(0.05)

        record_duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        self.close_stream()

        audio = b''.join(self.drain_queue())
        audio_duration_ms = len(audio) / (SAMPLE_WIDTH * SAMPLERATE) * 1000
        log.info(f'Finished recording audio. Got {len(audio)} bytes. '
                 f'Audio duration: {audio_duration_ms:.0f} ms. Record duration: {record_duration_ms:.0f} ms.')

        if audio_duration_ms < config.min_audio_duration_ms:
            log.info('Audio duration is too short. Exiting...')
            return

        wav_bytes = build_wav(audio)
        filename = save_audio_to_file(wav_bytes) if save_recordings else None

        text, language = transcribe_audio_to_text(
            wav_bytes, os.path.basename(filename) if filename else 'record.wav')
        log.info(f"Language: {language} Got result: {loggable(text)}")

        if len(text.strip()) < 2:
            log.info('No text detected. Exiting...')
            return

        text = apply_substitutions(text)
        log.info(f"After substitutions: {loggable(text)}")
        paste(text)
        log.info('Finished processing audio...')
        prune_recordings()  # after the paste, it is housekeeping the user isn't waiting for

    def open_stream(self):
        try:
            return self.raw_stream(self.choose_mic()['index'])
        except Exception as e:
            # The device list is stale, or the microphone was unplugged mid-session.
            log.warning(f'Could not open the microphone: {e}. Refreshing the device list.')
        refresh_audio_devices()
        try:
            return self.raw_stream(self.choose_mic()['index'])
        except Exception as e:
            log.warning(f'Still could not open the microphone: {e}. Using the default device.')
            self.mic = None
            return self.raw_stream(None)

    def choose_mic(self):
        mic = pick_microphone()
        if mic != self.mic:
            log.info(f"Microphone selected: index {mic['index']}, name {mic['name']}")
            self.mic = mic
        return mic

    def raw_stream(self, device):
        with audio_lock:  # never while PortAudio is being rebuilt
            stream = sd.RawInputStream(samplerate=SAMPLERATE, blocksize=BLOCKSIZE, device=device,
                                       dtype="int16", channels=1, callback=self.callback)
            try:
                stream.start()
            except Exception:
                stream.close()  # don't leak the device when only start() failed
                raise
        return stream

    def close_stream(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            log.warning(f'Could not close the audio stream: {e}')

    def drain_queue(self):
        blocks = []
        while not self.audio_queue.empty():
            blocks.append(self.audio_queue.get())
        return blocks


# --- hotkeys ----------------------------------------------------------------

HOTKEY_SINGLE = 'hotkey_1'
HOTKEY_COMBO = 'hotkey_2'

keys_pressed = set()
recorder = Recorder()


def check_keys_combination():
    # all() over an empty set is True, which would turn every keystroke into a
    # trigger for anyone who disables hotkey_2 by setting it to an empty set.
    return bool(config.hotkey_2) and all(k in keys_pressed for k in config.hotkey_2)


def on_press(key):
    if key in config.hotkey_2:
        keys_pressed.add(key)

    if key == config.hotkey_1:
        recorder.start(HOTKEY_SINGLE)
    elif check_keys_combination():
        recorder.start(HOTKEY_COMBO)


def on_release(key):
    keys_pressed.discard(key)

    # Only the hotkey that started the recording may stop it. Otherwise any other
    # keystroke during dictation ended it, because the hotkey_2 combination is
    # never fully held when the recording was started by hotkey_1.
    trigger = recorder.trigger
    if trigger == HOTKEY_SINGLE and key == config.hotkey_1:
        recorder.stop()
    elif trigger == HOTKEY_COMBO:
        if not check_keys_combination():
            recorder.stop()
        elif key == config.hotkey_1:
            # Escape hatch. If a release event for the combination is swallowed - the
            # menu click steals focus, or the combination fires an OS shortcut - the
            # combination looks held forever and nothing else can stop the recording.
            log.info('Combination looks stuck, forgetting the keys held down')
            keys_pressed.clear()
            recorder.stop()


if __name__ == '__main__':
    log.info(f'Started {app} {ver}. Press Ctrl+C to exit.')
    log.info(f'Command line: {[sys.executable] + sys.argv}')
    log.info(f'Transcribe server: {redacted(transcribe_server_url)}')
    prune_recordings()

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        log.info('Program was interrupted by the user.')
        listener.stop()

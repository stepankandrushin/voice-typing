# voice-typing

Dictation app for voice typing using private whisper or any other transcription server

  

## Installation

```
# In Mac OS open Terminal and install HomeBrew, git and python3 (if was not installed before):
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git
brew install python3

# On Linux, Pyperclip needs xclip:
sudo apt-get install -y xclip python3 python3-pip

# copy project
git clone https://github.com/stepankandrushin/voice-typing.git

cd voice-typing
/usr/bin/env python3 -m pip install -r requirements.txt
cp config.py.sample config.py

# Print devices with microphone and their names to use in config file (only need for custom microphones):
/usr/bin/env python3 list_devices.py

# edit config using your preferred editor:
vim config.py

# Start application (It also restarts the app if it was running before)
./start.sh

```

## Windows

Requires Python 3.12 (the `py` launcher). Run from the project folder:

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.py.sample config.py

# optional: list input devices to pick a specific microphone in config.py
.venv\Scripts\python.exe list_devices.py
```

Edit `config.py`:

- `hotkey_1 = keyboard.Key.ctrl_r` (right Ctrl; it must be a single key, not a set)
- `transcribe_server`, `languages` and, if the server asks for one, `send_token = True` with `token`
- leave `priority_mic_names = []` to record from the Windows default input device

Start the application (it stops the previous instance first):

```
start.cmd
```

Windows must be allowed to use the microphone: **Settings → Privacy & security → Microphone → Let desktop apps access your microphone**.

Notes:

- The text is pasted with Ctrl+V, so the target window has to be focused. A non-elevated app cannot paste into an elevated window, so run `start.cmd` as administrator if you dictate into such windows.
- If a paste shows the previous clipboard instead of the dictation, increase `restore_clipborad_delay` in `config.py`.

In Mac OS after running you will need to allow your application to monitor key strokes from another apps, access to Microphone, access to control other apps (to paste data), to do this after application was started:

### Allow access to monitor Hotkeys
1. Choose Apple menu  > System Settings, then click **Privacy & Security**
2. Click on the right: **Input Monitoring**
3. Turn on the setting for Terminal app.

### Allow access to Microphone
1. Choose Apple menu  > System Settings, then click **Privacy & Security**
2. Click on the right: **Microphone**
3. Turn on the setting for Terminal app. (If you don't see Terminal, start the application ./start.sh and press your hotkey so the app asks for the microphone)

### Allow access to Accessibility
1. Choose Apple menu  > System Settings, then click **Privacy & Security**
2. Click on the right: **Accessibility**
3. Turn on the setting for Terminal app.

### Allow access to Automation
1. Choose Apple menu  > System Settings, then click **Privacy & Security**
2. Click on the right: **Automation**
3. Turn on the setting **System Events** for Terminal app.

### Screenshots for options above
You need to approve Terminal app in all 4 sections: 
![You need to approve Terminal app in all 4 sections](img/privacy_security_all.jpeg)
 
Example of approving Terminal app in Accessibility section:  
![Terminal app approved in the Accessibility section](img/privacy_security_accessibility.jpeg)


## Update application
```
# Stop application using Ctrl+C
git pull
# Read in update.txt for possible updates needed to be made for config.py
# Start the application:
./start.sh
```

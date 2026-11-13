@echo off
setlocal
cd /d "%~dp0"

rem UTF-8 keeps Russian log lines intact when the output is redirected.
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found. Create it first:
    echo   py -3.12 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Stop the previous instance, as start.sh does on macOS. Only a python.exe whose
rem command line mentions voice_typing.py is matched, so this shell is not affected.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter 'Name=''python.exe''' | Where-Object { $_.CommandLine -like '*voice_typing.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

".venv\Scripts\python.exe" "voice_typing.py"
endlocal

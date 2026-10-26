@echo off
setlocal
cd /d "%~dp0"
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\GOTWiC-Scanner-Chrome"
py -3.13 gotwic_gui.py
if errorlevel 1 pause

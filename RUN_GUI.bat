@echo off
setlocal
cd /d "%~dp0"
if not exist "GOTWiC Scanner.lnk" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CREATE_SHORTCUT.ps1" >nul 2>&1
py -3.13 startup.py
if errorlevel 1 pause

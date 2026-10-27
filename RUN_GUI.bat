@echo off
setlocal
cd /d "%~dp0"
py -3.13 startup.py
if errorlevel 1 pause

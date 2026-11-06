@echo off
cd /d "%~dp0"
py desktop_launcher.py --update "%~1"
if errorlevel 1 pause

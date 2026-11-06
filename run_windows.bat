@echo off
cd /d "%~dp0"
py desktop_launcher.py
if errorlevel 1 pause

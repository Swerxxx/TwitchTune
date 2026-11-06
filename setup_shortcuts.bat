@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_shortcuts.ps1"
if errorlevel 1 pause

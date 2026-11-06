@echo off
cd /d "%~dp0"
TwitchTune.exe
echo.
echo Exit code: %errorlevel%
pause

@echo off
setlocal
cd /d "%~dp0"
echo Installing dependencies...
py -m pip install -r requirements.txt "pyinstaller>=6,<7"
if errorlevel 1 goto :error
py -m pip install --upgrade "yt-dlp[default]>=2026.3.17"
if errorlevel 1 goto :error
echo Building TwitchTune.exe...
py -m PyInstaller --noconfirm --clean --windowed --onedir --name TwitchTune ^
  --contents-directory _internal ^
  --additional-hooks-dir hooks ^
  --icon assets/twitchtune.ico ^
  --add-data "assets;assets" ^
  --add-data "apply_binary_update.ps1;." ^
  --collect-all PySide6 ^
  --collect-all yt_dlp ^
  --collect-submodules yt_dlp_plugins ^
  --copy-metadata yt-dlp-yandex-music ^
  --collect-submodules keyring.backends ^
  --hidden-import websocket ^
  --hidden-import requests app.py
if errorlevel 1 goto :error
copy /Y run_debug.bat dist\TwitchTune\run_debug.bat >nul
echo.
echo Done. Launch: dist\TwitchTune\TwitchTune.exe
start "" "dist\TwitchTune\TwitchTune.exe"
exit /b 0
:error
echo.
echo Build failed. Check the error above.
pause
exit /b 1

param([string]$Python='py')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& $Python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $Python -m pip install --upgrade "yt-dlp[default]>=2026.3.17"
if ($LASTEXITCODE -ne 0) { throw 'yt-dlp update failed.' }
& $Python -m PyInstaller --noconfirm --clean --name TwitchTune --windowed --onedir --contents-directory _internal `
  --additional-hooks-dir hooks `
  --icon assets/twitchtune.ico `
  --add-data "assets;assets" `
  --add-data "apply_binary_update.ps1;." `
  --collect-all PySide6 `
  --collect-all yt_dlp `
  --collect-submodules yt_dlp_plugins `
  --copy-metadata yt-dlp-yandex-music `
  --collect-submodules keyring.backends `
  --hidden-import websocket `
  --hidden-import requests app.py
if ($LASTEXITCODE -ne 0) { throw 'TwitchTune build failed.' }
Copy-Item run_debug.bat dist\TwitchTune\run_debug.bat -Force
Write-Host "Готово: dist\TwitchTune\TwitchTune.exe"

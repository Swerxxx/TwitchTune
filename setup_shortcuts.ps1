$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$python = (& py -c 'import sys; print(sys.executable)')
if ($LASTEXITCODE -ne 0) { throw 'Python is required. Install Python with the py launcher.' }
$pythonw = Join-Path (Split-Path $python.Trim()) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw 'pythonw.exe was not found next to Python.' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$launcher = Join-Path $PSScriptRoot 'desktop_launcher.py'
foreach ($entry in @(@('TwitchTune',''),@('TwitchTune Update',' --update'))) {
    $shortcut = $shell.CreateShortcut((Join-Path $desktop ($entry[0]+'.lnk')))
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"'+$launcher+'"'+$entry[1]
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $PSScriptRoot 'assets\twitchtune.ico')
    $shortcut.Save()
}
Start-Process -FilePath $pythonw -ArgumentList ('"'+$launcher+'"') -WorkingDirectory $PSScriptRoot

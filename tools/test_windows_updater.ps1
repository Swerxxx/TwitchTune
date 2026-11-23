param([string]$BuildDir='dist/TwitchTune',[Parameter(Mandatory=$true)][string]$Version,[string]$InstalledBuildDir=$BuildDir)
$ErrorActionPreference='Stop'
$testArea=[IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$root=Join-Path $testArea ('TwitchTune-updater-'+[Guid]::NewGuid().ToString('N'))
$desktop=Join-Path $root 'Desktop'
$install=Join-Path $desktop 'TwitchTune'
$testProfileDir=Join-Path $root 'profile'
[IO.Directory]::CreateDirectory($install) | Out-Null
[IO.Directory]::CreateDirectory($testProfileDir) | Out-Null
Copy-Item -Path (Join-Path $InstalledBuildDir '*') -Destination $install -Recurse
$exe=Join-Path $install 'TwitchTune.exe'
$oldHash=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
$shell=New-Object -ComObject WScript.Shell
$shortcutPath=Join-Path $desktop 'TwitchTune.lnk'
$shortcut=$shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath=$exe
$shortcut.WorkingDirectory=$install
$shortcut.Save()
$shortcutHash=(Get-FileHash -LiteralPath $shortcutPath -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $install 'settings.json'),'keep this local file')
[IO.File]::WriteAllText((Join-Path $testProfileDir 'settings.json'),'{"theme":"purple"}')
$database=Join-Path $testProfileDir 'queue.sqlite3'
python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('CREATE TABLE updater_sentinel(value TEXT)'); c.execute('INSERT INTO updater_sentinel VALUES (?)',('keep',)); c.commit()" "$database"
if ($LASTEXITCODE -ne 0) {throw 'Could not create test profile.'}
$helper=Join-Path (Split-Path $PSScriptRoot) 'apply_binary_update.ps1'
$env:QT_QPA_PLATFORM='offscreen'
function Wait-App {
    Get-Process -Name TwitchTune -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq (Join-Path $install 'TwitchTune.exe')} | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue
}
function Check-Data {
    if ([IO.File]::ReadAllText((Join-Path $install 'settings.json')) -ne 'keep this local file') {throw 'An installation-local user file was changed.'}
    python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute('SELECT value FROM updater_sentinel').fetchone()[0]=='keep'" "$database"
    if ($LASTEXITCODE -ne 0) {throw 'Profile database was changed.'}
    if ((Get-Content (Join-Path $testProfileDir 'settings.json') | ConvertFrom-Json).theme -ne 'purple') {throw 'Profile settings were changed.'}
    if ((Get-FileHash -LiteralPath $shortcutPath -Algorithm SHA256).Hash -ne $shortcutHash -or -not (Test-Path -LiteralPath $exe)) {throw 'The desktop shortcut no longer points to the installed EXE.'}
    if (Get-ChildItem -LiteralPath $desktop -Force -Directory -Filter '.TwitchTune-backup-*') {throw 'A backup was created on the desktop.'}
    if (Get-ChildItem -LiteralPath $desktop -Force -Directory -Filter '.TwitchTune-update-*') {throw 'A finished update left a staging folder on the desktop.'}
}
function Check-Backup($status, $hash) {
    $result=Get-Content -LiteralPath (Join-Path $testProfileDir 'updates/last-result.json') | ConvertFrom-Json
    if ($result.status -ne $status) {throw "Expected update status: $status"}
    if ((Split-Path -Parent $result.backup) -ne (Join-Path $testProfileDir 'update-backups')) {throw 'Backup is outside the profile backup folder.'}
    if ((Get-FileHash -LiteralPath (Join-Path $result.backup 'TwitchTune.exe') -Algorithm SHA256).Hash -ne $hash) {throw 'The backup does not contain the previous EXE.'}
}
try {
    $stage=Join-Path $desktop ('.TwitchTune-update-'+[Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    Copy-Item -Path (Join-Path $BuildDir '*') -Destination $stage -Recurse
    & powershell -NoProfile -ExecutionPolicy Bypass -File $helper -ParentProcessId 2147483647 -InstallDir $install -StageDir $stage -DataDir $testProfileDir -Version $Version -SmokeTest
    if ($LASTEXITCODE -ne 0) {throw 'Windows updater did not install the valid build.'}
    Wait-App
    Check-Backup 'installed' $oldHash
    Check-Data
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath (Join-Path $BuildDir 'TwitchTune.exe') -Algorithm SHA256).Hash) {throw 'The new EXE was not installed at the shortcut target.'}
    Write-Output 'Valid update started, kept the desktop shortcut and saved a backup in the profile.'
    $expected=(Get-FileHash (Join-Path $install 'TwitchTune.exe') -Algorithm SHA256).Hash
    $badStage=Join-Path $desktop ('.TwitchTune-update-'+[Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory((Join-Path $badStage '_internal')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $badStage 'TwitchTune.exe'),'MZ-not-an-executable')
    [IO.File]::WriteAllText((Join-Path $badStage 'release-info.json'),('{"version":"'+$Version+'","updater":1}'))
    & powershell -NoProfile -ExecutionPolicy Bypass -File $helper -ParentProcessId 2147483647 -InstallDir $install -StageDir $badStage -DataDir $testProfileDir -Version $Version -SmokeTest
    if ($LASTEXITCODE -eq 0) {throw 'Broken build was incorrectly accepted.'}
    Wait-App
    if ((Get-FileHash (Join-Path $install 'TwitchTune.exe') -Algorithm SHA256).Hash -ne $expected) {throw 'The original EXE was not restored.'}
    Check-Backup 'failed' $expected
    Check-Data
    Write-Output 'Broken update rolled back to the same EXE path and preserved the profile and backup.'
} finally {
    Wait-App
    $testRoot=Get-Item -LiteralPath $root -Force -ErrorAction SilentlyContinue
    if ($testRoot -and $testRoot.Parent.FullName -eq $testArea -and $testRoot.Name -match '^TwitchTune-updater-[0-9a-f]{32}$' -and -not ($testRoot.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $testRoot.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

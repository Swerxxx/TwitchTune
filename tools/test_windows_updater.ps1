param([string]$BuildDir='dist/TwitchTune',[Parameter(Mandatory=$true)][string]$Version)
$ErrorActionPreference='Stop'
$root=Join-Path $env:RUNNER_TEMP ('TwitchTune-updater-'+[Guid]::NewGuid().ToString('N'))
$install=Join-Path $root 'installed'
$testProfileDir=Join-Path $root 'profile'
[IO.Directory]::CreateDirectory($install) | Out-Null
[IO.Directory]::CreateDirectory($testProfileDir) | Out-Null
Copy-Item -Path (Join-Path $BuildDir '*') -Destination $install -Recurse
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
}
try {
    $stage=Join-Path $root ('.TwitchTune-update-'+[Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    Copy-Item -Path (Join-Path $BuildDir '*') -Destination $stage -Recurse
    & powershell -NoProfile -ExecutionPolicy Bypass -File $helper -ParentProcessId 2147483647 -InstallDir $install -StageDir $stage -DataDir $testProfileDir -Version $Version -SmokeTest
    if ($LASTEXITCODE -ne 0) {throw 'Windows updater did not install the valid build.'}
    Wait-App
    $result=Get-Content (Join-Path $testProfileDir 'updates/last-result.json') | ConvertFrom-Json
    if ($result.status -ne 'installed') {throw 'Startup confirmation was not recorded.'}
    Check-Data
    $expected=(Get-FileHash (Join-Path $install 'TwitchTune.exe') -Algorithm SHA256).Hash
    $badStage=Join-Path $root ('.TwitchTune-update-'+[Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory((Join-Path $badStage '_internal')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $badStage 'TwitchTune.exe'),'MZ-not-an-executable')
    [IO.File]::WriteAllText((Join-Path $badStage 'release-info.json'),('{"version":"'+$Version+'","updater":1}'))
    & powershell -NoProfile -ExecutionPolicy Bypass -File $helper -ParentProcessId 2147483647 -InstallDir $install -StageDir $badStage -DataDir $testProfileDir -Version $Version -SmokeTest
    if ($LASTEXITCODE -eq 0) {throw 'Broken build was incorrectly accepted.'}
    Wait-App
    if ((Get-FileHash (Join-Path $install 'TwitchTune.exe') -Algorithm SHA256).Hash -ne $expected) {throw 'The original EXE was not restored.'}
    Check-Data
} finally {
    Wait-App
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

param(
    [Parameter(Mandatory=$true)][int]$ParentProcessId,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$StageDir,
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$ReadyFile='',
    [switch]$SmokeTest
)
$ErrorActionPreference = 'Stop'
$lockStream = $null
$child = $null
$canRestart = $false
$changed = New-Object System.Collections.Generic.List[string]
$units = @('TwitchTune.exe','_internal','release-info.json','run_debug.bat')
$install = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$stage = [IO.Path]::GetFullPath($StageDir).TrimEnd('\')
$parent = [IO.Path]::GetDirectoryName($install)
$backup = Join-Path $parent ('.TwitchTune-backup-'+[Guid]::NewGuid().ToString('N'))
$updates = Join-Path $DataDir 'updates'
[IO.Directory]::CreateDirectory($updates) | Out-Null
$resultPath = Join-Path $updates 'last-result.json'
$health = Join-Path $updates ([Guid]::NewGuid().ToString('N')+'.ready')
function Write-Result($status,$message) {
    $result = @{status=$status; message=$message; version=$Version; backup=$backup}
    [IO.File]::WriteAllText($resultPath,($result | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
}
function Release-Lock {
    if ($null -ne $script:lockStream) {
        try {$script:lockStream.Unlock(0,1)} catch {}
        $script:lockStream.Dispose();$script:lockStream=$null
    }
}
try {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {throw 'Invalid version.'}
    if ([IO.Path]::GetDirectoryName($stage) -ne $parent -or [IO.Path]::GetFileName($stage) -notlike '.TwitchTune-update-*') {throw 'Invalid staging directory.'}
    if (-not (Test-Path (Join-Path $stage 'TwitchTune.exe'))) {throw 'The staged application is missing.'}
    if (-not (Test-Path (Join-Path $install 'TwitchTune.exe'))) {throw 'The installed application is missing.'}
    foreach ($name in $units) {
        $path=Join-Path $install $name
        if ((Test-Path $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Installation links cannot be safely replaced.'}
    }
    if ($ReadyFile) {[IO.File]::WriteAllText($ReadyFile,'ready')}
    $old = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
    if ($null -ne $old) {
        try {Wait-Process -Id $ParentProcessId -Timeout 120}
        catch {if ($null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {throw}}
    }
    $lockStream=[IO.File]::Open((Join-Path $DataDir 'runtime.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    if ($lockStream.Length -eq 0) {$lockStream.WriteByte(0);$lockStream.Flush()}
    $lockStream.Lock(0,1)
    $canRestart=$true
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    foreach ($name in $units) {
        $new=Join-Path $stage $name
        if (-not (Test-Path $new)) {continue}
        $destination=Join-Path $install $name
        if (Test-Path $destination) {Move-Item -LiteralPath $destination -Destination (Join-Path $backup $name)}
        $changed.Add($name)
        Move-Item -LiteralPath $new -Destination $destination
    }
    Release-Lock
    $env:TWITCHTUNE_PROFILE_DIR=$DataDir
    $arguments='--update-health-file "'+$health+'"'
    if ($SmokeTest) {$arguments+=' --smoke-test'}
    $child=Start-Process -FilePath (Join-Path $install 'TwitchTune.exe') -ArgumentList $arguments -WorkingDirectory $install -PassThru
    $deadline=[DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ((Test-Path $health) -and [IO.File]::ReadAllText($health).Trim() -eq $Version) {
            Write-Result 'installed' 'Update installed and application started.'
            Remove-Item -LiteralPath $health -Force
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
            $job=[IO.Path]::GetDirectoryName($PSCommandPath)
            if ([IO.Path]::GetDirectoryName($job) -eq $updates -and [IO.Path]::GetFileName($job) -match '^[a-f0-9]{32}$') {
                Remove-Item -LiteralPath (Join-Path $job 'package.zip') -Force -ErrorAction SilentlyContinue
            }
            exit 0
        }
        if ($child.HasExited) {break}
        Start-Sleep -Milliseconds 200
    }
    throw 'The updated application did not confirm startup.'
} catch {
    $failure=$_.Exception.Message
    try {
        if ($null -ne $child -and -not $child.HasExited) {
            $child.CloseMainWindow() | Out-Null
            if (-not $child.WaitForExit(10000)) {$child.Kill();$child.WaitForExit()}
        }
        if ($changed.Count -gt 0) {
            if ($null -eq $lockStream) {
                $lockStream=[IO.File]::Open((Join-Path $DataDir 'runtime.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
                $lockStream.Lock(0,1)
            }
            for ($index=$changed.Count-1;$index -ge 0;$index--) {
                $name=$changed[$index];$destination=Join-Path $install $name;$saved=Join-Path $backup $name
                if (Test-Path $destination) {Remove-Item -LiteralPath $destination -Recurse -Force}
                if (Test-Path $saved) {Move-Item -LiteralPath $saved -Destination $destination}
            }
        }
        Write-Result 'failed' ($failure+' Previous application files restored.')
    } catch {
        Write-Result 'failed' ($failure+' Automatic restoration failed; backup: '+$backup)
    } finally {Release-Lock}
    if ($canRestart -and (Test-Path (Join-Path $install 'TwitchTune.exe'))) {
        if ($SmokeTest) {Start-Process -FilePath (Join-Path $install 'TwitchTune.exe') -ArgumentList '--smoke-test' -WorkingDirectory $install}
        else {Start-Process -FilePath (Join-Path $install 'TwitchTune.exe') -WorkingDirectory $install}
    }
    exit 1
} finally {Release-Lock}

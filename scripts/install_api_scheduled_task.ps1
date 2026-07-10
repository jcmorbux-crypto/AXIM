# Registers a Windows Scheduled Task that runs the FastAPI control UI
# (api/main.py via uvicorn) at logon and restarts it automatically if it
# stops - the API-process counterpart to install_scheduled_task.ps1
# (which covers core/telegram_listener.py only). Run both to have all of
# AXIM come up automatically after a reboot. See DEPLOYMENT.md "Process
# supervision".
#
# This is a genuine system-level change (registers a persistent Scheduled
# Task under your Windows user account) - review before running.
#
# Usage: powershell -File scripts\install_api_scheduled_task.ps1
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM API"

# Bind host/port come from .env's API_BIND_HOST/API_BIND_PORT (same keys
# config/settings.py reads) rather than being hardcoded here, so opting
# into remote access (docs/AXIM_REMOTE_ACCESS.md) is a single .env edit
# followed by re-running this script, not a code change. Defaults match
# settings.py's own local-only defaults exactly.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = (Resolve-Path "$ProjectRoot\venv\Scripts\python.exe").Path
$TaskName = "AXIM API"

$ApiBindHost = "127.0.0.1"
$ApiBindPort = "8090"
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*API_BIND_HOST\s*=\s*(.+?)\s*$') { $ApiBindHost = $Matches[1] }
        if ($_ -match '^\s*API_BIND_PORT\s*=\s*(.+?)\s*$') { $ApiBindPort = $Matches[1] }
    }
}

$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "-m uvicorn api.main:app --host $ApiBindHost --port $ApiBindPort" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -DontStopOnIdleEnd `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Runs AXIM's control UI (${ApiBindHost}:${ApiBindPort}) at logon, restarts on failure (up to 999 times, 1 min apart)." `
    -Force

Write-Host "Registered Scheduled Task '$TaskName'."
Write-Host "Start it now with:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check status with:   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it with:      Unregister-ScheduledTask -TaskName '$TaskName'"

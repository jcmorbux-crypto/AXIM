# Registers a Windows Scheduled Task that runs the FastAPI control UI
# (api/main.py via uvicorn, via scripts/run_api_supervised.ps1) at logon
# and restarts it automatically if it stops - the API-process counterpart
# to install_scheduled_task.ps1 (which covers core/telegram_listener.py
# only). Run both to have all of AXIM come up automatically after a
# reboot. See DEPLOYMENT.md "Process supervision".
#
# The task's action is the supervisor wrapper script, not python.exe
# directly - same reasoning as install_scheduled_task.ps1: Task
# Scheduler's own RestartOnFailure setting does NOT trigger on a
# forcibly-terminated process (an OOM-kill, a crash, Stop-Process -Force)
# - Task Scheduler logs that as a "successful completion," not a
# failure, so the restart never engages for exactly the scenarios this
# exists to cover. This was found and fixed for the listener via
# live-fire testing but never carried over to this task, which called
# uvicorn directly - the same silent-non-restart gap existed here too,
# for the process that IS the entire control plane / Remote Client
# access point. RestartCount/RestartInterval are kept below as a second,
# independent layer (covers the case where the supervisor script process
# itself dies, which the inner while-loop obviously can't retry).
#
# This is a genuine system-level change (registers a persistent Scheduled
# Task under your Windows user account) - review before running.
#
# Usage: powershell -File scripts\install_api_scheduled_task.ps1
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM API"

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PowerShellExe = (Get-Command powershell.exe).Source
$SupervisorScript = Join-Path $ProjectRoot "scripts\run_api_supervised.ps1"
$TaskName = "AXIM API"

# Bind host/port are resolved from .env inside run_api_supervised.ps1
# itself (same keys config/settings.py reads), only used here for the
# task's own description text.
$ApiBindHost = "127.0.0.1"
$ApiBindPort = "8090"
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*API_BIND_HOST\s*=\s*(.+?)\s*$') { $ApiBindHost = $Matches[1] }
        if ($_ -match '^\s*API_BIND_PORT\s*=\s*(.+?)\s*$') { $ApiBindPort = $Matches[1] }
    }
}

$action = New-ScheduledTaskAction -Execute $PowerShellExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$SupervisorScript`"" `
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
    -Description "Runs AXIM's control UI (${ApiBindHost}:${ApiBindPort}) at logon via a supervisor loop that restarts it on any exit (crash or clean), plus Task Scheduler's own restart-on-failure as a second layer." `
    -Force

Write-Host "Registered Scheduled Task '$TaskName'."
Write-Host "Start it now with:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check status with:   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it with:      Unregister-ScheduledTask -TaskName '$TaskName'"

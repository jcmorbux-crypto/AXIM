# Registers a Windows Scheduled Task that runs scripts\soak_snapshot.py
# every 15 minutes for a bounded window, appending to
# logs\soak_test_log.csv - the actual evidence for the "genuine
# multi-hour soak test" line in docs\AXIM_LIVE_READINESS_CHECKLIST.md.
#
# This only monitors - it reads ui_listener_heartbeat and the signals
# table, never touches the browser or places a trade. The listener itself
# (core/telegram_listener.py) runs independently and is unaffected by
# this task existing, not existing, or being re-armed.
#
# Usage: powershell -File scripts\install_soak_snapshot_task.ps1 [-DurationHours 168]
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM Soak Snapshot"

param(
    [int]$DurationHours = 168   # 7 days by default - re-run this script to extend further
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = (Resolve-Path "$ProjectRoot\venv\Scripts\python.exe").Path
$TaskName = "AXIM Soak Snapshot"

$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "scripts\soak_snapshot.py" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Hours $DurationHours)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Appends a soak-test health snapshot to logs\soak_test_log.csv every 15 min for $DurationHours hours. Monitoring only - never touches the browser or trading engine." `
    -Force

Write-Host "Registered Scheduled Task '$TaskName' - snapshots every 15 min for $DurationHours hours."
Write-Host "Check progress with:  Get-Content logs\soak_test_log.csv -Tail 5"
Write-Host "Re-run this script any time to extend the window further."

# Registers a Windows Scheduled Task that runs core/telegram_listener.py
# at logon and restarts it automatically if it stops - the "OS process
# itself dies" layer that run_forever()'s own in-process recovery cannot
# cover (a reboot, an OOM kill, a segfault in a native dependency).
# See DEPLOYMENT.md "Process supervision".
#
# This is a genuine system-level change (registers a persistent Scheduled
# Task under your Windows user account) - review before running.
#
# Usage: powershell -File scripts\install_scheduled_task.ps1
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM Listener"

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = (Resolve-Path "$ProjectRoot\venv\Scripts\python.exe").Path
$TaskName = "AXIM Listener"

$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "core\telegram_listener.py" `
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
    -Description "Runs AXIM's Telegram listener at logon, restarts on failure (up to 999 times, 1 min apart)." `
    -Force

Write-Host "Registered Scheduled Task '$TaskName'."
Write-Host "Start it now with:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check status with:   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it with:      Unregister-ScheduledTask -TaskName '$TaskName'"

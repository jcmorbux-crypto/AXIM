# Registers a Windows Scheduled Task that runs core/telegram_listener.py
# (via scripts/run_listener_supervised.ps1) at logon and restarts it
# automatically if it stops - the "OS process itself dies" layer that
# run_forever()'s own in-process recovery cannot cover (a reboot, an OOM
# kill, a segfault in a native dependency). See DEPLOYMENT.md "Process
# supervision".
#
# The task's action is the supervisor wrapper script, not python.exe
# directly. Found via live-fire testing: Task Scheduler's own
# RestartOnFailure setting does NOT trigger on a forcibly-terminated
# process (TerminateProcess - what an OOM-kill or native crash actually
# looks like) - Task Scheduler logs that as a "successful completion",
# not a failure, so the restart never engages for the crash scenarios
# this exists to cover. RestartCount/RestartInterval are kept below as a
# second, independent layer (covers the case where the supervisor script
# process itself dies, which the inner while-loop obviously can't retry).
#
# This is a genuine system-level change (registers a persistent Scheduled
# Task under your Windows user account) - review before running.
#
# Usage: powershell -File scripts\install_scheduled_task.ps1
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM Listener"

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PowerShellExe = (Get-Command powershell.exe).Source
$SupervisorScript = Join-Path $ProjectRoot "scripts\run_listener_supervised.ps1"
$TaskName = "AXIM Listener"

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
    -Description "Runs AXIM's Telegram listener at logon via a supervisor loop that restarts it on any exit (crash or clean), plus Task Scheduler's own restart-on-failure as a second layer." `
    -Force

Write-Host "Registered Scheduled Task '$TaskName'."
Write-Host "Start it now with:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check status with:   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it with:      Unregister-ScheduledTask -TaskName '$TaskName'"

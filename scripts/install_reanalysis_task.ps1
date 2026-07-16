# Registers a Windows Scheduled Task that runs
# scripts\reanalyze_all_providers.py once a day - Phase 2 Priority #4's
# "every provider should automatically re-evaluate itself on a
# scheduled basis." Re-runs backtests/win-rate/strategy-ranking/
# drawdown/allocation recommendations for every provider that has both
# an existing recommendation and a real, currently-synced Telegram
# channel to refresh history from (a static historical dump - the OPT
# SIGNALS research providers - has nothing new to find on re-analysis,
# so those are skipped with a clear note rather than silently repeated).
# Notifies the owner (visible in the Notification Center) only when a
# provider's recommendation meaningfully changed.
#
# Requires a live authenticated Telegram session (same requirement
# POST /api/channels/sync already has) - if the UI session has expired,
# individual providers will show as failed re-analyses in the log
# rather than crashing the whole run.
#
# Usage: powershell -File scripts\install_reanalysis_task.ps1
# Remove later with: Unregister-ScheduledTask -TaskName "AXIM Provider Reanalysis"

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = (Resolve-Path "$ProjectRoot\venv\Scripts\python.exe").Path
$TaskName = "AXIM Provider Reanalysis"

$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "scripts\reanalyze_all_providers.py" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Re-analyzes every provider with a real synced Telegram channel once a day - refreshes backtests/recommendations and notifies the owner of meaningful changes. Analysis only, never touches live trading." `
    -Force

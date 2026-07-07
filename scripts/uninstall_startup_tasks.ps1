# Removes both AXIM Scheduled Tasks (see install_scheduled_task.ps1 and
# install_api_scheduled_task.ps1) - does not touch data/axim.db, .env,
# or any other AXIM state, only the Windows Task Scheduler entries.
#
# Usage: powershell -File scripts\uninstall_startup_tasks.ps1

foreach ($name in @("AXIM Listener", "AXIM API")) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed Scheduled Task '$name'."
    } else {
        Write-Host "Scheduled Task '$name' was not registered - nothing to do."
    }
}

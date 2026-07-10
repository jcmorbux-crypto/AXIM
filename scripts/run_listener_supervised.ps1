# Supervises core/telegram_listener.py with an unconditional restart loop.
#
# Why this exists (found via live-fire testing, not theory): Windows Task
# Scheduler's own RestartOnFailure setting does NOT trigger when a process
# is forcibly terminated (TerminateProcess - what Stop-Process -Force, a
# real OOM-kill, or a native crash all produce). Task Scheduler logs that
# exit as "successfully completed ... with return code 4294967295" (event
# ID 201, Information level, not a failure) - so RestartOnFailure, which
# only fires on a recognized failure exit, silently never engages for
# exactly the crash scenarios this supervision was built for. Confirmed
# twice: killed the Task-Scheduler-launched process directly, waited well
# past the configured 1-minute restart interval both times, no relaunch.
#
# This script is now the Scheduled Task's action instead of python.exe
# directly - a plain loop restarts the child on ANY exit (clean or
# forced), independent of how Task Scheduler classifies the exit code.
# run_forever() (core/telegram_listener.py) already handles in-process
# recovery (browser crash, worker pool rebuild, resumed trades); this is
# only the outer layer for when the whole Python process itself dies.

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "logs\supervisor.log"

function Write-SupervisorLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

Write-SupervisorLog "supervisor: started, watching core\telegram_listener.py"

while ($true) {
    Write-SupervisorLog "supervisor: launching telegram_listener.py"
    $proc = Start-Process -FilePath $PythonExe -ArgumentList "core\telegram_listener.py" `
        -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru -Wait
    Write-SupervisorLog "supervisor: telegram_listener.py exited with code $($proc.ExitCode) - restarting in 60s"
    Start-Sleep -Seconds 60
}

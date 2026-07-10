# Supervises the FastAPI control UI (api/main.py via uvicorn) with an
# unconditional restart loop - the API-process counterpart to
# run_listener_supervised.ps1, using the exact same reasoning.
#
# Why this exists (same live-fire finding as the listener's supervisor,
# not re-derived theory): Windows Task Scheduler's own RestartOnFailure
# setting does NOT trigger when a process is forcibly terminated
# (TerminateProcess - what Stop-Process -Force, a real OOM-kill, or a
# native crash all produce). Task Scheduler logs that exit as
# "successfully completed ... with return code 4294967295" (event ID
# 201, Information level, not a failure) - so RestartOnFailure silently
# never engages for exactly the crash scenarios this supervision is
# built for. This applies identically to the API process as it does to
# the listener - it's a Task Scheduler behavior, not something specific
# to what the supervised process happens to be.
#
# This script is the Scheduled Task's action instead of python.exe
# directly - a plain loop restarts uvicorn on ANY exit (clean or
# forced), independent of how Task Scheduler classifies the exit code.
#
# Bind host/port come from .env's API_BIND_HOST/API_BIND_PORT (same keys
# config/settings.py reads and install_api_scheduled_task.ps1 resolves),
# not hardcoded here, so opting into remote access
# (docs/AXIM_REMOTE_ACCESS.md) via a single .env edit doesn't require
# re-registering the task with different arguments.

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "logs\api_supervisor.log"

$ApiBindHost = "127.0.0.1"
$ApiBindPort = "8090"
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*API_BIND_HOST\s*=\s*(.+?)\s*$') { $ApiBindHost = $Matches[1] }
        if ($_ -match '^\s*API_BIND_PORT\s*=\s*(.+?)\s*$') { $ApiBindPort = $Matches[1] }
    }
}

function Write-SupervisorLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

Write-SupervisorLog "supervisor: started, watching api.main:app (${ApiBindHost}:${ApiBindPort})"

while ($true) {
    Write-SupervisorLog "supervisor: launching uvicorn"
    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList "-m uvicorn api.main:app --host $ApiBindHost --port $ApiBindPort" `
        -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru -Wait
    Write-SupervisorLog "supervisor: uvicorn exited with code $($proc.ExitCode) - restarting in 60s"
    Start-Sleep -Seconds 60
}

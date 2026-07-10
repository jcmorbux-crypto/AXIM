"""
Starts/stops/inspects the core/telegram_listener.py process on Windows.

Matches processes precisely (python.exe whose command line contains
"telegram_listener.py"), the same discriminating approach
scripts/cleanup_axim_chrome.ps1 uses for Chrome - never touches an
unrelated python.exe process on the machine.
"""
import subprocess

SCHEDULED_TASK_NAME = "AXIM Listener"


def _run_powershell(command, timeout=30):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def find_listener_pids():
    stdout, _, _ = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like '*telegram_listener.py*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    return [int(pid) for pid in stdout.splitlines() if pid.strip().isdigit()]


def is_listener_running():
    return len(find_listener_pids()) > 0


def start_listener():
    """Starts the listener via the registered Scheduled Task (survives
    logoff/reboot, auto-restarts on failure) rather than spawning a bare
    subprocess from within the API - matches the one documented, tested
    startup path (see scripts/install_scheduled_task.ps1,
    DEPLOYMENT.md)."""
    if is_listener_running():
        return {"status": "already_running", "pids": find_listener_pids()}
    stdout, stderr, code = _run_powershell(f"Start-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}'")
    if code != 0:
        return {"status": "error", "detail": stderr or stdout}
    return {"status": "started"}


def stop_listener():
    """Force-stops the listener process(es) found. This is NOT the
    graceful Ctrl+C shutdown path (run_forever()'s KeyboardInterrupt
    handler, which closes browser tabs cleanly) - a plain process kill
    from outside can't deliver that signal reliably to a Scheduled-Task-
    launched process. Always followed by the same targeted Chrome cleanup
    scripts/cleanup_axim_chrome.ps1 uses, so this never leaves orphaned
    tabs behind for the operator to find later.

    Stops the Scheduled Task FIRST, before killing any python.exe pid -
    found live: the task's action is scripts/run_listener_supervised.ps1,
    an unconditional restart-on-any-exit loop (see that script's own
    docstring for why Task Scheduler's own RestartOnFailure doesn't work
    for forced termination). Killing only the python child left that
    supervisor loop running, which relaunched a fresh listener ~60s later
    - a deliberate Stop silently un-stopping itself. Stopping the task
    kills the supervisor's root process first, so there's nothing left to
    do the relaunching; only then is any remaining/orphaned python.exe
    (e.g. one started outside the task, or a child that outlived a
    supervisor killed some other way) force-stopped directly."""
    _run_powershell(f"Stop-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -ErrorAction SilentlyContinue")
    pids = find_listener_pids()
    if not pids:
        return {"status": "not_running"}
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
    _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like '*sessions\\pocket_browser*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    return {"status": "stopped", "pids": pids}


def get_status():
    pids = find_listener_pids()
    return {"running": len(pids) > 0, "pids": pids}

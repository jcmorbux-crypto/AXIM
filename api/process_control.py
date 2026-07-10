"""
Starts/stops/inspects the core/telegram_listener.py process on Windows.

Matches processes precisely (python.exe whose command line contains
"telegram_listener.py"), the same discriminating approach
scripts/cleanup_axim_chrome.ps1 uses for Chrome - never touches an
unrelated python.exe process on the machine.
"""
import subprocess
import threading
import time

SCHEDULED_TASK_NAME = "AXIM Listener"

# find_listener_pids() spawns a real powershell.exe process and runs a
# WMI query (Get-CimInstance Win32_Process) - measured at 1.4-1.65s per
# call, purely from process-spawn + WMI overhead, not anything AXIM
# controls. web/dashboard.html's refreshGlobal() calls two endpoints
# that each hit this independently (get_status(), used by /api/status
# AND /api/pocket-option/status) as part of one Promise.all - found live
# (100% reproducible across 5 attempts) that this made Mission Control
# sit on "Loading..." for 5-8s on every single page load. A few seconds
# of staleness on "is the listener running" is an acceptable trade-off
# for a background status display (the same tolerance the app already
# extends to heartbeat staleness) - cached for CACHE_TTL_SECONDS by
# default. start_listener()/stop_listener() explicitly bypass the cache
# for their own action-gating checks (immediately before actually
# starting/stopping), where a stale read matters more than a few
# seconds of display staleness would.
CACHE_TTL_SECONDS = 3
_cache = {"pids": None, "at": 0.0}
# Single-flight lock: /api/status and /api/pocket-option/status both
# call get_status(), and web/dashboard.html's refreshGlobal() fires them
# concurrently in the same Promise.all - on a cold cache, both threadpool
# threads could otherwise independently spawn their own powershell.exe,
# paying the ~1.4s cost twice in parallel instead of once. This lock
# makes the second caller wait for the first's result and reuse it
# (a fresh cache write from the same moment) rather than doing its own
# redundant, expensive lookup.
_lock = threading.Lock()


def _run_powershell(command, timeout=30):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _find_listener_pids_uncached():
    stdout, _, _ = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like '*telegram_listener.py*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    return [int(pid) for pid in stdout.splitlines() if pid.strip().isdigit()]


def find_listener_pids(use_cache=True):
    if use_cache and _cache["pids"] is not None and (time.monotonic() - _cache["at"]) < CACHE_TTL_SECONDS:
        return _cache["pids"]
    with _lock:
        # Re-check inside the lock (double-checked locking) - another
        # thread may have just finished refreshing it while this one was
        # waiting to acquire the lock, in which case reuse that result
        # instead of doing a second redundant powershell spawn.
        if use_cache and _cache["pids"] is not None and (time.monotonic() - _cache["at"]) < CACHE_TTL_SECONDS:
            return _cache["pids"]
        pids = _find_listener_pids_uncached()
        _cache["pids"] = pids
        _cache["at"] = time.monotonic()
        return pids


def is_listener_running(use_cache=True):
    return len(find_listener_pids(use_cache=use_cache)) > 0


def start_listener():
    """Starts the listener via the registered Scheduled Task (survives
    logoff/reboot, auto-restarts on failure) rather than spawning a bare
    subprocess from within the API - matches the one documented, tested
    startup path (see scripts/install_scheduled_task.ps1,
    DEPLOYMENT.md)."""
    if is_listener_running(use_cache=False):
        return {"status": "already_running", "pids": find_listener_pids(use_cache=False)}
    stdout, stderr, code = _run_powershell(f"Start-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}'")
    _cache["pids"] = None  # force a fresh read on the next status check either way
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
    pids = find_listener_pids(use_cache=False)
    if not pids:
        _cache["pids"] = None
        return {"status": "not_running"}
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
    _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like '*sessions\\pocket_browser*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    _cache["pids"] = None  # force a fresh read on the next status check
    return {"status": "stopped", "pids": pids}


def get_status():
    pids = find_listener_pids()
    return {"running": len(pids) > 0, "pids": pids}

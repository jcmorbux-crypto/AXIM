# Finds (and optionally kills) Chrome processes that belong SPECIFICALLY
# to AXIM's own persistent browser profile - never touches any other
# chrome.exe, including the operator's own regular browser or another
# project's Playwright-driven Chrome, since those use a different
# executable path and/or --user-data-dir.
#
# Why this exists: force-killing core/telegram_listener.py (taskkill /F,
# a real Ctrl-C is always preferred - see USER_GUIDE.md) skips the graceful
# shutdown path that closes each browser tab, leaving orphaned Chrome
# processes behind. Repeated force-kills without cleanup measurably
# degraded the next startup's worker-pool build time during this project's
# own testing (see docs/AXIM_PRODUCTION_READINESS_REPORT.md section 4.4).
# Run this before restarting if you had to force-kill the listener.
#
# Usage:
#   powershell -File scripts\cleanup_axim_chrome.ps1            # report only (dry run)
#   powershell -File scripts\cleanup_axim_chrome.ps1 -Kill       # actually terminate them

param(
    [switch]$Kill
)

# Matches AXIM's own persistent context - the literal --user-data-dir path
# it launches with (execution/browser_session.py's USER_DATA_DIR), not
# just "any chrome.exe" or even "any Playwright-bundled chromium".
$ProfileMarker = "sessions\pocket_browser"

$matches = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ProfileMarker*" }

if (-not $matches) {
    Write-Host "No chrome.exe processes found using the AXIM profile ($ProfileMarker) - nothing to do."
    exit 0
}

Write-Host "Found $($matches.Count) chrome.exe process(es) using the AXIM profile:"
foreach ($p in $matches) {
    Write-Host "  PID $($p.ProcessId)"
}

if (-not $Kill) {
    Write-Host "`nDry run - no processes were terminated. Re-run with -Kill to actually stop them."
    exit 0
}

foreach ($p in $matches) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "Terminated PID $($p.ProcessId)"
    } catch {
        Write-Warning "Could not terminate PID $($p.ProcessId): $($_.Exception.Message)"
    }
}

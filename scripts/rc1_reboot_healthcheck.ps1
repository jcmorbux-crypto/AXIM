# RC1 Phase 4 post-reboot validation (2026-07-29 reboot-survival audit).
# Run this AFTER an interactive logon following the controlled reboot, once
# the AtLogon-triggered Scheduled Tasks have had a little time to come up.
# Writes a timestamped report to logs\rc1_reboot_healthcheck_<ts>.log and
# also prints it, so it's inspectable both live and after the fact (e.g.
# from a reconnected VS Code Tunnel session that wasn't present for the
# actual reboot).
#
# Read-only - checks state, does not modify anything.

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$ReportFile = Join-Path $ProjectRoot "logs\rc1_reboot_healthcheck_$ts.log"
$lines = New-Object System.Collections.Generic.List[string]
function Add-Line($s) { $lines.Add($s); Write-Host $s }

Add-Line "=== AXIM RC1 reboot health check - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Add-Line "Last boot time: $((Get-CimInstance Win32_OperatingSystem).LastBootUpTime)"
Add-Line ""

# --- Scheduled Tasks ---
foreach ($t in @("AXIM API", "AXIM Listener")) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    $info = Get-ScheduledTaskInfo -TaskName $t -ErrorAction SilentlyContinue
    Add-Line "[Task] $t : State=$($task.State) LastResult=$($info.LastTaskResult) LastRun=$($info.LastRunTime)"
}
Add-Line ""

# --- Processes: duplicate detection ---
$listenerProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*telegram_listener.py*' }
$apiProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*api.main*' }
Add-Line "[Process] telegram_listener.py instances: $($listenerProcs.Count) $(if ($listenerProcs.Count -gt 1) {'*** DUPLICATE ***'})"
$listenerProcs | ForEach-Object { Add-Line "    pid=$($_.ProcessId)" }
Add-Line "[Process] uvicorn api.main instances: $($apiProcs.Count) $(if ($apiProcs.Count -gt 1) {'*** DUPLICATE ***'})"
$apiProcs | ForEach-Object { Add-Line "    pid=$($_.ProcessId)" }
Add-Line ""

# --- Lock files (single-instance guard) ---
Add-Line "[Locks] $(Join-Path $ProjectRoot 'data')"
Get-ChildItem (Join-Path $ProjectRoot "data") -Filter "*.lock" -ErrorAction SilentlyContinue | ForEach-Object {
    Add-Line "    $($_.Name) last written $($_.LastWriteTime)"
}
Add-Line ""

# --- Chrome processes (orphan detection) ---
$chromeProcs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue
$aximChrome = $chromeProcs | Where-Object { $_.CommandLine -like '*AXIM\sessions*' }
Add-Line "[Chrome] total chrome.exe processes: $($chromeProcs.Count); AXIM session profile: $($aximChrome.Count)"
Add-Line ""

# --- API health ---
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8090/" -UseBasicParsing -TimeoutSec 5
    Add-Line "[API] http://127.0.0.1:8090/ -> HTTP $($r.StatusCode)"
} catch {
    Add-Line "[API] http://127.0.0.1:8090/ -> FAILED: $($_.Exception.Message)"
}
Add-Line ""

# --- VS Code Tunnel ---
$tunnelProcs = Get-CimInstance Win32_Process -Filter "Name='code-tunnel.exe'" -ErrorAction SilentlyContinue
Add-Line "[Tunnel] code-tunnel.exe processes: $($tunnelProcs.Count)"
$svc = Get-Service -Name "vscode*" -ErrorAction SilentlyContinue
if ($svc) { Add-Line "[Tunnel] service: $($svc.Name) status=$($svc.Status)" } else { Add-Line "[Tunnel] no Windows service registered (manual/app-launched tunnel)" }
Add-Line ""

# --- Database: unresolved/reconciliation-required series ---
$py = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$dbCheck = & $py -c @"
import sys
sys.path.insert(0, r'$ProjectRoot\core')
sys.path.insert(0, r'$ProjectRoot\config')
import database
conn = database.get_connection()
rows = conn.execute("SELECT id, status FROM trade_series WHERE status IN ('active','reconciliation_required')").fetchall()
print(len(rows))
for r in rows:
    print(f'  id={r[\"id\"]} status={r[\"status\"]}')
conn.close()
"@ 2>&1
Add-Line "[DB] unresolved (active/reconciliation_required) trade series:"
$dbCheck -split "`n" | ForEach-Object { Add-Line "    $_" }
Add-Line ""

# --- Demo mode confirmation (from most recent lifecycle.log line mentioning it) ---
$lifecycleLog = Join-Path $ProjectRoot "logs\lifecycle.log"
if (Test-Path $lifecycleLog) {
    $demoLine = Get-Content $lifecycleLog -Tail 500 | Select-String "demo mode verified" | Select-Object -Last 1
    Add-Line "[Demo mode] $(if ($demoLine) { $demoLine.Line } else { 'no recent demo-mode-verified line found in last 500 lifecycle.log lines' })"
}

Add-Line ""
Add-Line "=== end of report - saved to $ReportFile ==="

$lines | Out-File -FilePath $ReportFile -Encoding utf8

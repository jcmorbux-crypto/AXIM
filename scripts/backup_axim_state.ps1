# AXIM state backup - copies the persistent, non-regenerable state
# (database, Telegram session, Pocket Option browser profile) to a
# timestamped folder. Can run while AXIM is live: the database and session
# files copy cleanly, but a handful of files inside the Chrome profile
# (e.g. Cookies) are held open by the running browser and will be SKIPPED
# with a warning rather than aborting the whole backup - confirmed live,
# this is real Chrome behavior, not a hypothetical. For a guaranteed-
# complete profile snapshot, stop AXIM first.
#
# Usage: powershell -File scripts\backup_axim_state.ps1 [-BackupRoot <path>] [-KeepLast N]

param(
    [string]$BackupRoot = "$PSScriptRoot\..\backups",
    [int]$KeepLast = 14
)

$ProjectRoot = Resolve-Path "$PSScriptRoot\.."
$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$Dest = Join-Path $BackupRoot $Timestamp
$WarningCount = 0

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# Small, single files - a locked file here is unexpected, so a failure is
# reported as a real warning, not silently swallowed.
$SingleFileTargets = @(
    "data\axim.db",
    "axim_session.session",
    "axim_observer_session.session"
)

foreach ($target in $SingleFileTargets) {
    $src = Join-Path $ProjectRoot $target
    if (Test-Path $src) {
        try {
            Copy-Item -Path $src -Destination (Join-Path $Dest (Split-Path $target -Leaf)) -Force -ErrorAction Stop
            Write-Host "Backed up: $target"
        } catch {
            Write-Warning "Could not back up $target : $($_.Exception.Message)"
            $WarningCount++
        }
    } else {
        Write-Host "Skipped (not present): $target"
    }
}

# The Chrome profile directory - use robocopy, which skips locked files
# with a warning and continues, instead of Copy-Item -Recurse, which
# aborts the ENTIRE tree copy on the first locked file it hits.
$sessionsSrc = Join-Path $ProjectRoot "sessions"
if (Test-Path $sessionsSrc) {
    $sessionsDest = Join-Path $Dest "sessions"
    $robocopyLog = robocopy $sessionsSrc $sessionsDest /E /R:0 /W:0 /NFL /NDL /NJH
    # robocopy exit codes 0-7 are all "success" (8+ means real failure);
    # locked files are skipped and counted, not treated as a hard error.
    if ($LASTEXITCODE -ge 8) {
        Write-Warning "robocopy reported errors backing up sessions/ (exit code $LASTEXITCODE)"
        $WarningCount++
    } else {
        Write-Host "Backed up: sessions (robocopy exit code $LASTEXITCODE - locked files, if any, were skipped)"
    }
} else {
    Write-Host "Skipped (not present): sessions"
}

Write-Host "`nBackup written to: $Dest"
if ($WarningCount -gt 0) {
    Write-Host "Completed with $WarningCount warning(s) - see above." -ForegroundColor Yellow
}

# Retention: keep only the most recent $KeepLast backups.
$allBackups = Get-ChildItem -Path $BackupRoot -Directory | Sort-Object Name -Descending
if ($allBackups.Count -gt $KeepLast) {
    $toRemove = $allBackups | Select-Object -Skip $KeepLast
    foreach ($old in $toRemove) {
        Remove-Item -Path $old.FullName -Recurse -Force
        Write-Host "Removed old backup: $($old.Name)"
    }
}

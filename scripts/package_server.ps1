# Packages the AXIM Core Server into a versioned, distributable zip for a
# fresh Windows machine - the source tree plus install scripts and docs,
# minus everything that's local/secret/generated (venv, logs, data,
# sessions, .env, __pycache__, backups, the desktop client, this
# machine's git history).
#
# This is deliberately NOT a PyInstaller/frozen-binary bundle: AXIM drives
# a real, persistent, visible Chromium profile via Playwright, which is a
# poor fit for single-exe freezing (large, platform-fragile, breaks
# `playwright install chromium`'s own download step). A source package +
# documented setup (docs/AXIM_SETUP_GUIDE.md) is the honest, maintainable
# answer for a private, single-operator deployment.
#
# Usage: powershell -File scripts\package_server.ps1
# Output: dist\AXIM-Core-Server-v<version>-<timestamp>.zip

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $ProjectRoot

# Version comes from .env if present (falls back to a default), same
# source of truth as the running app itself.
$Version = "1.0.0"
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*VERSION\s*=\s*(.+?)\s*$') { $Version = $Matches[1] }
    }
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$PackageName = "AXIM-Core-Server-v$Version-$Timestamp"
$DistDir = Join-Path $ProjectRoot "dist"
$StageDir = Join-Path $DistDir $PackageName
$ZipPath = Join-Path $DistDir "$PackageName.zip"

if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

# Directories that make up the actual running server.
$IncludeDirs = @("core", "api", "execution", "parsers", "providers", "config", "web", "scripts", "docs", "tests")
foreach ($dir in $IncludeDirs) {
    $src = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $StageDir $dir
    robocopy $src $dst /E /XD __pycache__ target node_modules /XF *.pyc *.session *.session-journal *.session-wal *.session-shm | Out-Null
}

# Root-level files a fresh install needs.
$IncludeFiles = @(
    "requirements.txt", ".env.example",
    "INSTALL.md", "QUICK_START.md", "FIRST_TRADE.md", "DEMO_CHECKLIST.md",
    "LIVE_CHECKLIST.md", "TROUBLESHOOTING.md",
    "USER_GUIDE.md", "DEPLOYMENT.md", "README.md",
    "docs\AXIM_SETUP_GUIDE.md", "docs\AXIM_DEMO_VALIDATION_CHECKLIST.md",
    "docs\AXIM_LIVE_READINESS_CHECKLIST.md"
)
foreach ($f in $IncludeFiles) {
    $src = Join-Path $ProjectRoot $f
    if (Test-Path $src) {
        Copy-Item $src -Destination (Join-Path $StageDir (Split-Path $f -Leaf)) -Force
    }
}

# Empty directories the app expects to exist on first run.
foreach ($dir in @("data", "logs", "sessions", "backups")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $StageDir $dir) | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $StageDir "$dir\.gitkeep") | Out-Null
}

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

# Compress-Archive can transiently fail with "file in use" right after a
# robocopy of many small files (observed live: real-time AV scanning the
# freshly-written tests\*.py files a beat before compression reaches
# them, a different file each time) - retry a few times with a short
# backoff rather than failing the whole package over a race that clears
# itself within a second or two.
$maxAttempts = 5
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    try {
        Compress-Archive -Path "$StageDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq $maxAttempts) { throw }
        Write-Host "Compress-Archive attempt $attempt/$maxAttempts failed ($($_.Exception.Message)) - retrying..."
        Start-Sleep -Seconds 2
    }
}
Remove-Item -Recurse -Force $StageDir

Write-Host "Packaged: $ZipPath"
Write-Host "Contains: source tree (no venv/.env/logs/data/sessions/backups), requirements.txt, .env.example, setup docs."
Write-Host "Fresh-machine setup: unzip, then follow docs\AXIM_SETUP_GUIDE.md"

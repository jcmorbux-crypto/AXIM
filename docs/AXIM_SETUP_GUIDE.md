# AXIM Setup Guide

The current, authoritative path from a fresh Windows machine to a running
AXIM Core Server plus (optionally) a Remote Client on a second device.
`INSTALL.md` and `USER_GUIDE.md` still have useful low-level detail but
predate the multi-user/multi-Fund/Setup-Wizard architecture described here -
this guide reflects what the app actually walks you through today.

## What you need before starting

- A Windows machine to act as the **AXIM Server** (the "Mini PC" in
  `docs/AXIM_REMOTE_ACCESS.md`) - this is the machine that runs the
  Telegram listener and the real Pocket Option browser session.
- Python 3.12+ installed on that machine.
- A Telegram account, with API credentials from
  [my.telegram.org](https://my.telegram.org) → API development tools
  (`api_id` + `api_hash`).
- A Pocket Option account. **Use the demo account** unless you have already
  been through `docs/AXIM_LIVE_READINESS_CHECKLIST.md` and made a
  deliberate decision to go live.

## 1. Get the server code onto the machine

Either clone the repository, or unzip a package produced by
`scripts\package_server.ps1` (see "Packaging" below) - both land you at the
same directory layout.

```powershell
cd C:\AXIM
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` downloads the actual browser binary AXIM
drives - easy to miss, and required.

## 2. Create `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
`TELEGRAM_PHONE`. Leave `ACCOUNT=DEMO` and `ARMED=false` - both are
re-confirmed inside the app in step 4 below, and `ARMED` in particular
should stay `false` in the checked-in `.env` (see the Live Readiness
Checklist for why). Everything else in `.env.example` has a safe default;
`WATCH_CHANNELS` and your Pocket Option connection are both set up from the
UI in step 4, not by hand-editing this file.

## 3. Start the AXIM Core Server

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Open `http://127.0.0.1:8090` in a browser on the same machine. Leave this
process running - it's the server. (For it to survive logoff/reboot, see
"Running unattended" below - come back to that after the guided setup.)

## 4. Guided first-run setup (the Setup Wizard)

The first time nobody has logged in, the app shows a bootstrap/owner-account
screen, then walks through an 8-step wizard:

1. **Owner Account** - create your login (the very first account on this
   server is automatically the Owner, with full admin rights).
2. **Telegram** - link your Telegram account in-app (send-code/verify-code,
   right there in the browser - no separate terminal script needed). Uses
   its own session, separate from the listener's, so linking doesn't
   interrupt anything already running.
3. **Pocket Option** - create a broker account and click Connect. A real,
   visible browser window opens against that account's own dedicated
   profile; log in by hand in that window (there is no automated-login
   path). The wizard polls until it shows `connected`.
4. **Risk Profile** - choose/duplicate a starting risk profile (trade size,
   max trades/hour, consecutive-loss and daily-loss limits, etc.).
5. **Channels** - pick which Telegram chat(s) AXIM is allowed to act on
   (this is `WATCH_CHANNELS`, managed from the UI now - fail-closed by
   default, same as before).
6. **Session** - creates your first Fund and starts a trading session
   against the broker account from step 3.
7. **Demo Test** - optionally fire one real test trade through the full
   pipeline against the demo account, to confirm everything is wired
   correctly end to end before relying on live signals.
8. **Ready** - done. Mission Control is the landing dashboard from here on.

## 5. Verify with the automated test suite

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Non-browser regression suite (parser, risk rules, orchestration, worker
pool, multi-Fund logic). Should all pass (1 test is intentionally skipped
depending on config). This does not touch Pocket Option - for that, use
`docs/AXIM_DEMO_VALIDATION_CHECKLIST.md` after setup.

## 6. Running unattended (survives logoff/reboot)

Two Scheduled Tasks - one per process, matching how you started them above:

```powershell
powershell -File scripts\install_scheduled_task.ps1       # the Telegram listener
powershell -File scripts\install_api_scheduled_task.ps1   # the API/web UI
```

Both are genuine system-level changes (persistent Scheduled Tasks under
your Windows account) - read a script before running it. Remove either with
`scripts\uninstall_startup_tasks.ps1`. Once the API's Scheduled Task is
registered, you can also start/stop the listener itself from inside the
web UI rather than a terminal - `api/process_control.py` drives the same
Scheduled Task under the hood.

Full detail (backups, resource sizing, monitoring, rollback) is in
`DEPLOYMENT.md`.

## 7. Adding a Remote Client (optional)

To monitor/control AXIM from a second device (laptop, etc.) over a private
Tailscale mesh - no public port, no public IP, no domain - follow
`docs/AXIM_REMOTE_ACCESS.md` end to end. Short version: install Tailscale on
both devices, set `API_BIND_HOST`/`API_BIND_PORT`/`ALLOWED_ORIGINS` in
`.env` on the server, re-run `install_api_scheduled_task.ps1`, then point
the AXIM desktop app on the second device at the server's Tailscale
hostname during its first-run "Connect to a remote AXIM Server" picker.

## Packaging

**Server**: `scripts\package_server.ps1` produces a versioned zip
(`dist\AXIM-Core-Server-v<version>-<timestamp>.zip`) containing the source
tree, `requirements.txt`, `.env.example`, and this guide - everything a
fresh machine needs, with no secrets, logs, database, or browser-profile
state included. Deliberately a source package rather than a frozen
single-exe build: AXIM drives a real, persistent Playwright/Chromium
profile, which doesn't freeze well into a single binary, and a documented
source package is more maintainable for a private, single-operator
deployment.

**Remote Client (desktop app)**: from `axim-desktop\`,
`npm run tauri build` produces a Windows installer (MSI and NSIS `.exe`,
per `src-tauri/tauri.conf.json`'s `bundle.targets: "all"`) under
`axim-desktop\src-tauri\target\release\bundle\`. Requires Rust
(`rustup`/`cargo`) and Node.js installed on the build machine - not
required on a machine that only *runs* the resulting installer. The
installer is a normal Windows app: it does not bundle Python or a copy of
the server - "Run locally" mode expects an existing AXIM Server checkout
on the same machine (via `AXIM_PROJECT_ROOT`, defaults to `C:\AXIM`);
"Connect to a remote server" mode (the common case for a second device)
needs nothing else at all.

## If something doesn't come up right

- **Wizard step 2 (Pocket Option) never reaches "connected"**: the login
  window may need you to click through it manually - check for a visible
  Chrome window that may be behind your other windows.
- **`WATCH_CHANNELS`/Channels step shows nothing**: your Telegram account
  needs to already be a member of/have visibility into the source chat
  before AXIM can list it.
- **Nothing else works**: check `logs/axim.log` (the unified log) and
  `logs/lifecycle.log` (trade-pipeline-specific) first - both are rotating
  files under `logs/`.

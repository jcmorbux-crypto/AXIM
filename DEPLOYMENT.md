# AXIM Trader Deployment Guide

This document covers running AXIM unattended / long-term, beyond the
single-terminal usage covered in `USER_GUIDE.md`. Read
`docs/AXIM_PRODUCTION_READINESS_REPORT.md` first - it documents exactly
what has and hasn't been validated, and the known limitations referenced
below.

## Process supervision / Windows startup

`core/telegram_listener.py`'s `run_forever()` already handles in-process
recovery: a crashed browser, a dropped Telegram connection, or an unhandled
exception all trigger an automatic restart of the affected layer with
exponential backoff, without the OS process itself dying. What it does
**not** handle is the OS process being killed outright (a reboot, an OOM
kill, a segfault in a native dependency) - for that, wrap it in an actual
process supervisor.

Two ready-to-run scripts register Windows Scheduled Tasks (one per
process, matching how they're run manually today) that start at logon
and restart automatically on failure:

```powershell
powershell -File scripts\install_scheduled_task.ps1       # core/telegram_listener.py
powershell -File scripts\install_api_scheduled_task.ps1   # api/main.py (the control UI, 127.0.0.1:8090)
```

Both are genuine system-level changes (a persistent Scheduled Task under
your Windows user account, running on every future login) - read the
script before running it. Remove either or both later with:

```powershell
powershell -File scripts\uninstall_startup_tasks.ps1
```

or manage them individually via `Get-ScheduledTask -TaskName "AXIM Listener"` /
`"AXIM API"` and `Unregister-ScheduledTask`. **AXIM is not registered
to start automatically by default** - these scripts only take effect
when you explicitly run them.

If you'd rather run it as a true Windows service (restarts even if no
user is logged in), use **NSSM** (Non-Sucking Service Manager) pointed
at the same `venv\Scripts\python.exe` + arguments the scripts above use.

Whatever you use, make sure it sends a clean stop signal (equivalent to
Ctrl+C) rather than force-killing, per the note in `USER_GUIDE.md` - this
matters for avoiding orphaned Chrome tabs across restarts.

## Remote access (Remote Client / Tailscale)

By default the control API binds `127.0.0.1` only - nothing outside the
Mini PC itself can reach it. Enabling a Remote Client (another PC/laptop
running AXIM's desktop app in Remote mode) over a private Tailscale mesh
VPN is fully opt-in and documented step by step, for a non-technical
user, in `docs/AXIM_REMOTE_ACCESS.md`. In short: set `API_BIND_HOST` /
`API_BIND_PORT` / `ALLOWED_ORIGINS` in `.env`, re-run
`install_api_scheduled_task.ps1` so the Scheduled Task picks up the new
bind address, then point the Remote Client at your Mini PC's Tailscale
hostname. No public port, public IP, or domain is ever required or
opened - trades always execute on the server, never the client.

## Persistent state that must survive restarts/redeploys

| Path | What it is | Back it up? |
|---|---|---|
| `data/axim.db` | Every signal, trade, outcome, and recovery event ever recorded | Yes |
| `axim_session.session` | Your logged-in Telegram session (avoids re-authenticating) | Yes, and keep it private - it's equivalent to your Telegram login |
| `sessions/pocket_browser/` | The persistent Chromium profile, including your logged-in Pocket Option session | Yes |
| `.env` | All configuration and credentials | Yes, but **never commit it to git** |
| `logs/` | Rotating log files (`core/logger.py` handles rotation automatically, default 5MB × 5 backups per logger) | Optional, useful for post-incident review |

`scripts/backup_axim_state.ps1` copies all of the above (except `.env` and
`logs/`, which are handled separately) into a timestamped folder under
`backups/`, and prunes anything beyond the most recent 14 by default:

```powershell
powershell -File scripts\backup_axim_state.ps1
```

Safe to run while AXIM is live - the database and session files copy
cleanly, and a handful of files inside the Chrome profile that are held
open by the running browser (e.g. `Cookies`) are skipped with a warning
rather than aborting the whole backup (confirmed live, not hypothetical).
For a guaranteed-complete profile snapshot, stop AXIM first. Schedule this
alongside your process supervisor (e.g. a second Task Scheduler entry,
daily) for unattended operation.

## Secrets

`.env` contains your Telegram API credentials, Pocket Option login, and
Telegram phone number. It is already gitignored in this repo - keep it that
way. Never commit `axim_session.session` or `axim_observer_session.session`
either (they're live, usable Telegram login tokens).

## Resource sizing

Based on the real measurements in `docs/AXIM_PRODUCTION_READINESS_REPORT.md`:

- Chrome's working set during active multi-worker trading was observed
  between ~1.3GB-1.7GB with `MAX_CONCURRENT_WORKERS=6`. Size the host with
  meaningful headroom above that, especially if also running the dashboard
  or other processes.
- CPU: sustained 75-80% system-wide was measured at
  `MAX_CONCURRENT_WORKERS=10` on the development machine, which is *why*
  production configuration was dialed back to 6. If deploying to a
  different/more powerful host, re-measure before assuming a higher worker
  count is safe - this is a real, machine-dependent limit, not a fixed
  constant.
- Disk: `data/axim.db` and `logs/` both grow over time; log rotation is
  automatic, the database is not currently pruned (a future maintenance
  task if it becomes large enough to matter).

## Known limitations relevant to deployment decisions

(Full detail in the Production Readiness Report §4 - summarized here for
deployment planning.)

- **True-simultaneous signal bursts** (multiple signals arriving within the
  same instant, not just close together) have a measured real failure rate
  under heavy concurrent DOM contention. Real Telegram-paced traffic
  observed in production had natural spacing and did not trigger this.
- **A browser crash landing exactly during a trade's outcome-read window**
  can cause that one trade's result to fail to record (fails safe as an
  `error` status - never records a *wrong* result, just sometimes fails to
  record one at all).
- **Two trades on the same asset+direction closing within the same
  clock-minute** have a small residual chance of outcome-matching ambiguity
  (the site only renders minute-resolution timestamps). Pre-existing,
  unchanged by recent work.

None of these have ever produced an incorrect financial outcome in testing
- they are all fail-safe (reject/error) failure modes, not silent
correctness bugs. Still worth knowing about before scaling signal volume up
significantly.

## Monitoring in production

- `core/timeline_report.py` for latency trends over time.
- `core/dashboard_server.py` for a live read-only view.
- The `recovery_events` table in `data/axim.db` records every recovery
  attempt (browser reconnect, worker pool rebuild, process restart, resume
  open trade) with succeeded/failed outcomes - a quick health signal for
  "has this been recovering from real faults, and how often."
- Watch for orphaned `chrome.exe` processes after any restart, especially
  if the restart wasn't a clean Ctrl+C (see the note above).

## Rollback

Since all state lives in `data/axim.db` and the two session directories,
rolling back to a previous code version is just a matter of restoring the
previous commit/build - no database migration is currently required (the
schema has been additive-only throughout this project's history).

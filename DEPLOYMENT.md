# AXIM Deployment Guide

This document covers running AXIM unattended / long-term, beyond the
single-terminal usage covered in `USER_GUIDE.md`. Read
`docs/AXIM_PRODUCTION_READINESS_REPORT.md` first - it documents exactly
what has and hasn't been validated, and the known limitations referenced
below.

## Process supervision

`core/telegram_listener.py`'s `run_forever()` already handles in-process
recovery: a crashed browser, a dropped Telegram connection, or an unhandled
exception all trigger an automatic restart of the affected layer with
exponential backoff, without the OS process itself dying. What it does
**not** handle is the OS process being killed outright (a reboot, an OOM
kill, a segfault in a native dependency) - for that, wrap it in an actual
process supervisor:

- **Windows Task Scheduler**: create a task that runs
  `python core/telegram_listener.py`, with "restart on failure" configured
  and triggered at logon/on a schedule. This is the simplest option for a
  single Windows host.
- **NSSM** (Non-Sucking Service Manager) if you want it running as a true
  Windows service.

Whatever you use, make sure it sends a clean stop signal (equivalent to
Ctrl+C) rather than force-killing, per the note in `USER_GUIDE.md` - this
matters for avoiding orphaned Chrome tabs across restarts.

## Persistent state that must survive restarts/redeploys

| Path | What it is | Back it up? |
|---|---|---|
| `data/axim.db` | Every signal, trade, outcome, and recovery event ever recorded | Yes |
| `axim_session.session` | Your logged-in Telegram session (avoids re-authenticating) | Yes, and keep it private - it's equivalent to your Telegram login |
| `sessions/pocket_browser/` | The persistent Chromium profile, including your logged-in Pocket Option session | Yes |
| `.env` | All configuration and credentials | Yes, but **never commit it to git** |
| `logs/` | Rotating log files (`core/logger.py` handles rotation automatically, default 5MB × 5 backups per logger) | Optional, useful for post-incident review |

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

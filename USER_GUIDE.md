# AXIM User Guide

## What AXIM does

AXIM watches a Telegram chat for trading signals, parses them, runs them
through a risk-rule pipeline, and (if `ARMED=true` and the risk checks pass)
places the trade on Pocket Option via a real, visible browser, then tracks
the outcome (win/loss/draw) once the trade closes.

```
Telegram message → parse_signal() → risk_manager checks → pocket_executor
   (select asset/expiry/amount, click) → track_outcome (reads the Closed
   trades list once the trade's expiry has passed) → recorded in data/axim.db
```

## Starting AXIM

```powershell
python core/telegram_listener.py
```

Runs until you stop it (Ctrl+C for a clean shutdown - see "Stopping AXIM
correctly" below). It automatically:
- launches and logs into a persistent Chromium browser against the Pocket
  Option demo account
- verifies demo mode before doing anything else (refuses to start if it
  can't confirm this)
- scans the current tradeable-asset list
- builds a pool of `MAX_CONCURRENT_WORKERS` browser tabs for placing trades
- connects to Telegram and starts watching `WATCH_CHANNELS`
- resumes tracking for any trade left open from a previous run

## Configuring which chat AXIM listens to

`WATCH_CHANNELS` in `.env` is a comma-separated allow-list. Each entry
matches either:
- an exact (case-insensitive) `@username`, or
- a case-insensitive substring of the chat's display title

Empty `WATCH_CHANNELS` means **no chat is trusted** (fail-closed) - AXIM
will log a warning and process nothing until you set it.

If you're not sure a message is reaching AXIM or being filtered correctly,
set `TELEGRAM_DEBUG_LOG=true` (see "Debug logging" below) - it logs every
incoming message's routing decision from every chat your account can see,
before any filtering.

## Control UI (channel manager, start/stop/pause)

A local web UI is available instead of editing `.env`/restarting for
channel changes. It's a separate process from the listener - start it
alongside:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Then open `http://127.0.0.1:8090`. First time only: the channel manager
needs its own, separate Telegram login (`axim_ui_session` - a second
session under your same account, so it can list dialogs live even while
the listener holds its own session file open):

```powershell
python core/telegram_channels.py
```

From the UI you can: see every channel/group/bot your account can see,
search/filter them, enable or disable which ones AXIM follows (takes
effect on the listener's next incoming message - no restart needed), see
when each one last produced a signal, and Start/Stop/Pause/Resume/
Emergency-Stop the listener. `WATCH_CHANNELS` in `.env` still works too -
either source enables a channel, so nothing already configured stops
working.

Pause and Emergency Stop take effect immediately (the running listener
checks this before executing anything, on every incoming message) - Start/
Stop control the actual process via the Scheduled Task
(`scripts/install_scheduled_task.ps1`).

## The PREVIEW_ONLY / ARMED / AUTO_EXECUTE gate

Three separate switches, in the order they're checked:

| Variable | Effect when `false`/unset |
|---|---|
| `AUTO_EXECUTE` | If false, every signal stops at `status: "preview"` right after risk checks - never touches the browser. |
| `PREVIEW_ONLY` | Same as above - either one being true/false-in-the-blocking-direction stops execution at the same point. |
| `ARMED` | If false, the browser *does* select the asset/expiry/amount and read the live payout (so you can see everything AXIM would have done), but stops just before clicking Buy/Sell: `"Status: ARMED=false, trade NOT clicked"`. |

This means you can safely run AXIM with `PREVIEW_ONLY=false, ARMED=false`
to watch it prepare real trades (with real, live payout data) without ever
risking a click - a useful middle ground between full preview and full
execution.

**To actually execute trades**, both `PREVIEW_ONLY=false` and `ARMED=true`
must be set. Do this deliberately, understanding the risk-rule
configuration below first.

## Risk rules

Configured in `.env`, enforced in `core/risk_manager.py`, all fail-closed
(if a check can't determine the answer - e.g. payout couldn't be read - it
rejects rather than allows):

| Variable | Default | What it does |
|---|---|---|
| `MAX_TRADE_AMOUNT` | 50 | Rejects a signal if the configured `TRADE_AMOUNT` exceeds this. |
| `MAX_TRADES_PER_HOUR` | 10 | Rejects new signals once this many have been placed in the trailing hour. |
| `MAX_CONSECUTIVE_LOSSES` | 3 | Rejects new signals once this many trades in a row have lost. |
| `COOLDOWN_AFTER_LOSS_SECONDS` | 300 | Rejects new signals for this many seconds after any loss. |
| `DUPLICATE_SIGNAL_WINDOW_SECONDS` | 120 | Rejects an identical (asset+direction+expiry) signal received within this window of a prior one. |
| `MINIMUM_PAYOUT` | 90 | Rejects a trade if the live-read payout percentage is below this. |

Every one of these can be relaxed or disabled (set to a very high/zero
value) if your trading strategy calls for it - there's nothing structurally
special about the defaults, they're just conservative starting points. See
`.env`'s own comments for the values this project's own operator has chosen
for the "trade every signal as directed" mode of operation.

## Watching AXIM run

The process prints to stdout as it works. Key lines to look for:

- `Connected to Telegram` - startup finished successfully
- `[TELEGRAM_DEBUG] ...` (if `TELEGRAM_DEBUG_LOG=true`) - every incoming
  message's chat/sender/filter/parser decision
- `AXIM SIGNAL PARSED` / `Execution Status: ...` - what happened to a signal
  that passed the filter
- `AXIM TRADE PREPARED` - the asset/expiry/amount/payout right before the
  ARMED check
- `Status: TRADE BUTTON CLICKED` - a real trade was placed
- `trade_id=N status=result_win/result_loss/result_draw` - a trade's outcome
  was recorded

For structured after-the-fact analysis, `core/timeline_report.py` reads
every trade's persisted stage-by-stage timeline and produces P50/P95/P99
latency statistics:

```powershell
python core/timeline_report.py --limit 100
```

## The dashboard

A read-only, local web view over the same database:

```powershell
python core/dashboard_server.py
```

Then open `http://127.0.0.1:8080` (or whatever `DASHBOARD_PORT` is set to).
It only reads `data/axim.db` - it never executes anything and can run
alongside the listener safely.

## Stopping AXIM correctly

**Use Ctrl+C**, not Task Manager / `taskkill /F`, whenever possible.
`run_forever()` catches `KeyboardInterrupt` and runs a clean shutdown that
closes every browser tab it opened. Force-killing the process skips this -
the underlying Chromium tabs can be left running, and they accumulate
across repeated force-kills, measurably slowing down the next startup (this
was observed directly during this project's own stress testing - see
`docs/AXIM_PRODUCTION_READINESS_REPORT.md` section 4.4). If you do have to
force-kill it, run the cleanup helper before restarting:

```powershell
powershell -File scripts\cleanup_axim_chrome.ps1          # reports what it would stop (dry run)
powershell -File scripts\cleanup_axim_chrome.ps1 -Kill     # actually stops them
```

It only ever targets Chrome processes launched with AXIM's own
`--user-data-dir` (its persistent profile path) - it cannot affect your
regular browser or another program's Chrome/Playwright session.

## Checking the database directly

```powershell
python -c "import sqlite3; c = sqlite3.connect('data/axim.db'); c.row_factory = sqlite3.Row; [print(dict(r)) for r in c.execute('SELECT id, asset, direction, execution_status, result, profit_loss FROM signals ORDER BY id DESC LIMIT 20')]"
```

## Recovering from a restart

If AXIM is restarted while a trade is still open, `core/recovery.py` runs
automatically at startup and re-attaches outcome tracking to it (computing
remaining time until its expiry from `opened_at`), then continues watching
for the same original closed-list result. This has been exercised
repeatedly and confirmed working, including immediately after a real
browser crash.

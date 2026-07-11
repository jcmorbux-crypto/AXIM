# AXIM Demo Validation Checklist

Run this after `docs/AXIM_SETUP_GUIDE.md` and before trusting AXIM with
real signal volume - even on the demo account. Each item names how to
check it and what a pass looks like. This validates *this specific
install* end to end; it does not replace `docs/AXIM_LIVE_READINESS_CHECKLIST.md`,
which is about whether to ever flip to real money.

## 1. Automated regression suite

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

**Pass:** all tests pass (one is intentionally skipped depending on
`COOLDOWN_AFTER_LOSS_SECONDS`). As of this session: 503 tests, OK
(1 skipped). No browser involved in this step.

## 2. `ACCOUNT=DEMO` and `ARMED=false` at rest

```powershell
Select-String -Path .env -Pattern "^ACCOUNT=|^ARMED="
```

**Pass:** `ACCOUNT=DEMO`, `ARMED=false`. Confirm this *every time* before a
validation session, not just once - a one-line regression here is the
single most consequential mistake this checklist exists to catch.

## 3. Listener starts clean and verifies demo mode

Start the listener (directly, or via the Setup Wizard's session step) and
watch the console/`logs/axim.log` for, in order:

```
AXIM: starting persistent Pocket Option session...
browser_warmup: demo mode verified (is-chart-demo present)
AXIM: starting browser worker pool (N worker(s))...
AXIM: worker pool ready, running startup recovery...
Connected to Telegram
```

**Pass:** all five lines appear, no `LiveModeNotConfiguredError` or
`refusing to proceed` messages, a visible Chromium window opens showing
the Pocket Option demo cabinet. **Fail-closed is correct**: if
`browser_warmup` cannot confirm demo mode, it should refuse to start, not
proceed anyway.

## 4. `WATCH_CHANNELS` allow-list is actually filtering

With `TELEGRAM_DEBUG_LOG=true`, send (or wait for) a message in a chat
*not* on your allow-list, then one that is.

**Pass:** `logs/axim.log` shows `filter_decision: BLOCKED` for the
off-list chat and `filter_decision: ALLOWED` (proceeding to
`parser_decision`) for the on-list one. An empty `WATCH_CHANNELS` should
block everything - confirm this fails closed, not open.

## 5. One real end-to-end demo trade

From the web UI: **Broker → Test Trade** (or Wizard step 6, "Demo Test").
This queues a real trade through the actual coordinator/worker pool, not a
simulation.

**Pass:** the trade appears in Mission Control's recent-signals list with
a real `execution_status` progressing through `trade_prepared` →
`trade_clicked` → `trade_opened` → `trade_closed` → `win`/`loss`/`draw`,
and the corresponding balance change is visible in the Pocket Option demo
tab itself. Screenshots (if `SAVE_SCREENSHOTS=true`) appear under
`logs/trades/<trade_id>/`.

## 6. Risk rules actually reject, not just log

Temporarily set an unreachable threshold for one rule (e.g.
`MAX_TRADE_AMOUNT=0.01`) for a single test signal, or use the existing
`tests/test_risk_manager.py` suite as a stand-in if you don't want to
touch `.env`.

**Pass:** the signal is recorded with `result=rejected:<rule>` and no
trade is clicked. Restore the real threshold afterward.

## 7. Process recovery survives a restart with a trade open

Start a trade with a expiry of a minute or more, then (once you can
confirm it reached `trade_opened`) stop the listener with Ctrl+C
(graceful) and restart it.

**Pass:** startup logs show `run_recovery()` finding the open trade and
re-attaching outcome tracking; the trade eventually closes with a real
win/loss/draw, not stuck at `trade_opened` or `error:abandoned_on_restart`.

## 8. Dashboard/UI reflects reality

Open Mission Control (or `core/dashboard_server.py`'s read-only dashboard
if running standalone).

**Pass:** today/week stats, recovery health, and the recent-signals table
match what you just did in steps 5-7 - no stale/zero data where real
activity just happened.

## 9. Clean shutdown leaves no orphaned Chrome

```powershell
# after Ctrl+C on the listener:
powershell -File scripts\cleanup_axim_chrome.ps1
```

**Pass:** dry-run output shows zero AXIM-owned `chrome.exe` processes
remaining (it only ever targets AXIM's own `--user-data-dir`, never other
Chrome windows). If a graceful shutdown consistently leaves orphans on
your machine, that's a real regression worth investigating before
scaling usage.

## Sign-off

Only move on to volume/live consideration once every item above has
actually been exercised *on this install*, not assumed from a previous
one - browser DOM behavior and Telegram/Pocket Option account state are
both real-world dependencies that can shift between machines and over
time.

- [ ] All 9 items passed on: ______ (date) on this machine's own install.

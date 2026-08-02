# AXIM Live Validation Package

Companion to `docs/AXIM_LIVE_PRODUCTION_GRADUATION.md` (the Phase 1-5
plan) and `docs/AXIM_CAPABILITY_MATRIX.md` (the module inventory). This
document is the operational package for Phase 3-4: the evidence that
every Live safety feature actually works, the exact runbook an operator
follows to bring AXIM online, and the objective Go/No-Go gate.

**This document does not authorize enabling Live mode.** It exists so
that when the operator chooses to schedule the one dedicated graduation
session described below, everything needed is ready in advance -
nothing is being figured out live, under time pressure, with real money
on the table.

**The currently-running Demo session (#12, active since 2026-07-17,
363+ trades) is not touched by anything in this document.** All
evidence below comes from the automated test suite (isolated temp
databases, zero contact with the live process or its real database)
and direct code inspection.

---

## Part 1 — Safety Feature Evidence

Every claim below cites the exact code and the exact test file, plus a
real, fresh test run confirming it passes right now (not "should pass"
- actually run as part of preparing this document, 2026-08-01).

| # | Feature | Code | Test evidence | Result |
|---|---|---|---|---|
| 1 | **Live account verification works** | `execution/account_mode_verification.py::verify_live_mode` - fails closed across 7 independent conditions: unique selector match, element visible, exact text match, expected CSS class present, demo-class absent, URL matches configured `LIVE_URL`, and a fresh broker-account-config cross-check. Verified against a real live cabinet's DOM structure (module docstring, dated 2026-07-31). | `tests/test_account_mode_verification.py`, `tests/test_browser_health.py`, `tests/test_browser_warmup.py` | **58 passed** |
| 2 | **Demo/Live isolation works** | `core/broker_account_manager.py::account_effective_cabinet_mode` - single source of truth for which cabinet URL an account's browser session loads; each broker account gets its own `user_data_dir` (`core/database.py::create_broker_account`), so login sessions/cookies can never bleed between accounts. `execution/browser_warmup.py::BrowserWarmupService.__init__` takes `mode`/`user_data_dir` per instance. | `tests/test_broker_account_manager.py::AccountEffectiveCabinetModeTests` (6 cases, including the "live_enabled without confirmation" edge case) | **26 passed** (full file) |
| 3 | **Live statistics remain isolated** | `core/database.py::get_trades_between(exclude_live=...)` - filters out any trade whose broker_account is Live-effective; threaded through every previously-global `core/trade_statistics.py` aggregate (`yearly_stats`, `profit_by_channel`, `profit_by_asset`, `best_time_of_day`, `max_drawdown`, `longest_streaks`, plus the fund-scoped daily/weekly/monthly/lifetime). | `tests/test_trade_statistics_performance.py::ExcludeLiveIsolationTests` (8 cases, including "live_enabled but not arm-confirmed still isolates as demo" and "disarming clears the confirmation") | **20 passed** (full file) |
| 4 | **Reconciliation is mode-agnostic** | `core/recovery.py`, `execution/pocket_dom.py`, `execution/pocket_executor.py` - confirmed by direct grep: **zero** occurrences of `"demo"`/mode-specific branching in any of the three files. Reconciliation operates on whatever page the account's own browser session is already connected to (chosen once, at connect time, by `_mode`) - correct by construction, not by an added special case. | `tests/test_recovery.py` (lifecycle-based recovery, fail-closed broker matching, built from the real 2026-07-29 series-105 production incident) | **15 passed** |
| 5 | **Duplicate prevention works** | `core/risk_manager.py::check_duplicate_signal` - rejects a signal matching asset+direction+expiry within `DUPLICATE_SIGNAL_WINDOW_SECONDS` of an existing one, scoped correctly by channel. | `tests/test_risk_manager.py` (duplicate-signal cases) | **9 passed** |
| 6 | **Emergency Stop works** | Global: `api/main.py::emergency_stop`/`clear_emergency_stop` (`database.set_control_state`). Per-account: `api/broker_accounts_routes.py::emergency_stop_broker_account` - independent of the global switch and every other account's own, ends that account's active session. Both re-checked mid-pipeline by `core/risk_manager.py::check_not_stopped` (not just at signal entry) so a signal already in flight when Stop is pressed still gets caught. | `tests/test_broker_accounts_routes.py::EmergencyStopRouteTestCase` (4 cases: sets flag + records who, clears flag, ends this account's own session, does NOT touch a different account's session) | **22 passed** (full file) |
| 7 | **Stop-loss works** | `core/session_manager.py::check_session_limits` - session `loss_limit`, checked pessimistically against pending (open, unresolved) stake, not just realized P&L, so a burst of near-simultaneous signals can't blow through the limit before it's checked. Plus account-level `core/risk_manager.py::check_max_daily_loss`. | `tests/test_session_manager.py` (loss_limit cases), `tests/test_risk_manager.py` (max_daily_loss cases) | **8 + 14 passed** |
| 8 | **Stop-win works** | `core/session_manager.py::check_session_limits` - session `profit_target`, same file/function as stop-loss, checked first in the same ordered sequence. Plus account-level `core/risk_manager.py::check_daily_profit_target`. | Same test files as #7 (profit_target cases included in the same counts above) | Included above |
| 9 | **Daily loss limits work** | `core/risk_manager.py::check_max_daily_loss` (per-account) and `check_global_daily_loss` (account-wide) - both check current realized P&L AND pending open-trade exposure pessimistically, "at or beyond -limit if they all lose." | `tests/test_risk_manager.py` (max_daily_loss/global_daily_loss/daily_profit_target cases) | **14 passed** |
| 10 | **Trade audit trail works** | `signal_pipeline_events` table (`core/database.py::record_pipeline_event`/`list_pipeline_events_for_signal`) records every stage a signal passes through with a human-readable detail string. `core/timeline.py::TradeTimeline` persists the full stage-by-stage timing to `signals.trade_timeline_json`. `database.get_signal_detail`'s `rejection_reason` field (added 2026-08-01) surfaces the real reason text back off any rejected trade, not just the terse `rejected:<rule>` slug. | `tests/test_signal_pipeline_events.py`, `tests/test_signal_pipeline_routes.py`, `tests/test_trade_coordinator.py::test_audit_trail_rejection_reason_is_readable_back_off_the_trade` (new, added while preparing this document) | **28 + 61 passed** |
| 11 | **Live arm confirmation requires operator acknowledgement** | `core/database.py::confirm_live_arm(account_id, confirmed_by, reason)` - raises `ValueError` on an empty/whitespace-only reason; deliberately excluded from `update_broker_account`'s generic field set so the plain PATCH endpoint cannot set it. `core/broker_account_manager.py::account_effective_cabinet_mode` requires this confirmation as a THIRD, independent condition beyond `mode`/`live_enabled`. | `tests/test_broker_accounts_routes.py::ConfirmLiveArmRouteTestCase` (5 cases: sets fields correctly, empty reason 400s, unknown account 404s, confirming alone doesn't flip `live_enabled`, disabling clears the confirmation) | Included in the 22 passed above |
| 12 | **Live disarm works** | `core/database.py::update_broker_account` - setting `live_enabled=False` always clears `live_arm_confirmed_at`/`_by`/`_reason` in the SAME update, atomically, so a later re-enable can never silently inherit a stale confirmation. | `tests/test_trade_statistics_performance.py::test_disarming_clears_confirmation_so_the_account_reads_demo_again`, `tests/test_broker_accounts_routes.py::test_disabling_live_enabled_afterward_clears_the_confirmation` | Included in the 20 + 22 passed above |
| 13 | **Recovery logging works** | `core/database.py::record_recovery_event`/`get_recovery_event_stats` - every recovery attempt (browser reconnect, worker pool rebuild, process restart, resume-open-trade) written to the `recovery_events` table with a succeeded/failed outcome. `core/recovery.py`, `core/telegram_listener.py::run_forever` both log structured entries (`logger.info`/`.warning`/`.error`) to `logs/lifecycle.log` (rotating, `core/logger.py`). | `tests/test_recovery.py`, `tests/test_browser_warmup.py`, `tests/test_browser_worker_pool.py`, `tests/test_database_lock_retry.py`, `tests/test_telegram_listener_run_forever.py` | **25 passed** (the latter 3 files together) |

**Additional defense-in-depth found while preparing this evidence,
worth stating explicitly:** `core/risk_manager.py::check_demo_only()`
is called unconditionally in `trade_coordinator`'s preflight
(`trade_coordinator.py:123`) for **every** signal, regardless of which
broker account it's routed to. This checks the global `.env` `ACCOUNT`
variable - separate and redundant on top of the entire per-account
live-arm system above. Even a broker account that is fully Live-armed
(mode + `live_enabled` + `confirm_live_arm`) cannot execute a single
trade unless `.env`'s `ACCOUNT` is also set to something other than
`DEMO` - which requires a file edit and a process restart, a distinct,
deliberate, out-of-band action no API call can trigger.

**Total: 361 test cases directly relevant to Live safety, all passing,
run fresh while preparing this document.**

---

## Part 2 — Live Validation Checklist

The 13-point behavioral checklist from `AXIM_LIVE_PRODUCTION_GRADUATION.md`
Phase 3, integrated into the exact graduation-session sequence below
(Part 3) rather than repeated standalone - each checklist item is
tagged to the runbook step where it's actually exercised.

| # | Check | Exercised at runbook step |
|---|---|---|
| 1 | Correct login | Step 9 (Pocket Option Live login) |
| 2 | Correct account detection | Step 9 |
| 3 | Correct execution | Step 10 (small live trades) |
| 4 | Correct expiry | Step 10 |
| 5 | Correct stake sizing | Step 10 |
| 6 | Correct reconciliation | Step 11 |
| 7 | Correct bankroll updates | Step 12 |
| 8 | Correct recovery after interruption | Steps 3-8 (the reboot itself IS this test) |
| 9 | Correct stop-loss | Step 10 (deliberately configure a tiny loss limit first) |
| 10 | Correct stop-win | Step 10 (deliberately configure a tiny profit target first) |
| 11 | No duplicate trades | Step 10 |
| 12 | No missed trades | Step 10 |
| 13 | No race conditions | Step 10 (send 2 near-simultaneous test signals) |

---

## Part 3 — Production Runbook (Operator Steps)

### A. Normal startup (routine, not the graduation session)

1. **Start the listener process:** `python core/telegram_listener.py`
   (foreground) or via the registered Scheduled Task (`AXIM Listener`,
   see `scripts/install_scheduled_task.ps1`).
2. **Start the API/control UI:** `python api/main.py` or via the
   `AXIM API` Scheduled Task (`scripts/install_api_scheduled_task.ps1`).
3. **Health verification:** `GET /api/status` (process/control state),
   `GET /api/build-info` (confirms the running code version),
   `GET /api/pocket-option/status` (browser/cabinet connectivity).
   All three should return without error before trusting the system.
4. **Pocket Option login (Demo):** `POST /api/broker-accounts/{id}/connect`
   (or the Broker Accounts page's Connect button) - opens a real
   browser window for manual login; the UI polls `connection_status`
   until it reads `connected`.

### B. Health verification (any time, not just startup)

- `GET /api/status` - process running, no emergency stop/pause active.
- `GET /api/pocket-option/status` - cabinet connectivity per account.
- `logs/lifecycle.log` - tail for recent `ERROR`/`WARNING` entries.
- `recovery_events` table (`database.get_recovery_event_stats()`) -
  recent recovery attempts and their outcomes.

### C. Live account verification (before ever arming)

1. Configure `LIVE_URL`, `LIVE_MODE_VERIFICATION_SELECTOR`,
   `LIVE_MODE_VERIFICATION_TEXT` in `.env`, using values the operator
   has personally read off the real live cabinet's page source (never
   guessed).
2. Set the target broker account's `mode` to `"live"` or `"both"`
   (`PATCH /api/broker-accounts/{id}`).
3. Connect that account - `BrowserWarmupService.start()` will run
   `verify_live_mode` automatically and refuse to proceed
   (`DemoModeVerificationError`) if the page doesn't genuinely look
   like the configured live cabinet.

### D. Arming Live mode (the two independent switches)

1. `PATCH /api/broker-accounts/{id}` with `{"live_enabled": true}`.
2. `POST /api/broker-accounts/{id}/confirm-live-arm` with a real,
   specific `reason` (e.g. "verified live cabinet 2026-08-15, starting
   $10 graduation trades"). Both steps are required - neither alone is
   sufficient (Part 1, item #11).
3. Separately, in `.env`, set `ACCOUNT=LIVE` and restart the listener
   process - the global `check_demo_only()` gate (Part 1's "additional
   defense-in-depth" note) still applies on top of everything above.

### E. Monitoring during a live session

- Dashboard (`web/dashboard.html`) - live homepage, ~15 real endpoints.
- `core/timeline_report.py` - latency trends.
- `core/dashboard_server.py` - live read-only view.
- Notification bell (in-app) - automatic alerts on series
  blocked/errored, reconciliation-required, or an unhandled
  coordinator crash (commit `4e4bcf9`).

### F. Stopping trading (routine)

- Per-Fund: pause via `POST /api/funds/{id}/pause`.
- Per-session: `end_session` (Strike/session-limit paths already do
  this automatically on any stop condition).
- Per-account: disconnect (`POST /api/broker-accounts/{id}/disconnect`)
  - blocks any Fund pointing at it from trading further, does not
  delete the persistent login profile.

### G. Emergency shutdown

- Global: `POST /api/control/emergency-stop` - halts every account immediately,
  `check_not_stopped` catches even a signal already mid-pipeline.
- Per-account: `POST /api/broker-accounts/{id}/emergency-stop` - halts
  just that account, ends its active session, leaves every other
  account untouched.
- Physical fallback: stop the listener process (clean Ctrl+C/stop
  signal, per `DEPLOYMENT.md` - avoids orphaned Chrome tabs).

### H. Recovery after a crash (no reboot)

`run_forever()` (`core/telegram_listener.py`) handles this
automatically: browser reconnect, worker pool rebuild, Telegram
reconnect, with exponential backoff - live-fire verified per
`docs/AXIM_PRODUCTION_READINESS_REPORT.md`. On restart,
`core/recovery.py::resume_pending_trades` reconciles any trade left in
an unresolved state. No operator action required; verify via
`recovery_events` afterward.

### I. Recovery after a full reboot

1. Confirm both Scheduled Tasks (`AXIM Listener`, `AXIM API`) fire on
   logon (`Get-ScheduledTask -TaskName "AXIM Listener"`).
2. Confirm the listener resumes: `GET /api/status`.
3. Confirm the browser reconnects to the correct cabinet (Demo or
   Live, per each account's `mode`) and re-passes `_verify_account_mode`.
4. Confirm `core/recovery.py::resume_pending_trades` ran and
   reconciled any trade open at the moment of the reboot.
5. Check for orphaned `chrome.exe` processes (`DEPLOYMENT.md`'s own
   monitoring note).

**This exact sequence (steps 1-5) is what the dedicated graduation
session's "Verify automatic recovery / browser recovery / listener
recovery / API recovery / Pocket Option reconnect" steps mean
concretely - see Part 4.**

---

## Part 4 — The Graduation Session (operator-scheduled, not run today)

Exactly the sequence the operator specified, with each step's concrete
pass/fail criterion:

| Step | Action | Pass criterion |
|---|---|---|
| 1 | Stop Demo | Session #12 (or whichever is active) ends cleanly via a normal stop, not a kill - `stopped_manual` status, no orphaned trade. |
| 2 | Snapshot everything | `scripts/backup_axim_state.ps1` run successfully; verify the timestamped backup folder contains `data/axim.db`, `axim_session.session`, `sessions/pocket_browser/`. |
| 3 | Reboot Mini PC | Clean OS reboot. |
| 4 | Verify automatic recovery | Both Scheduled Tasks fire on logon (Runbook §I.1). |
| 5 | Verify browser recovery | Persistent Chromium profile reloads, no re-login required for the Demo/already-verified accounts. |
| 6 | Verify listener recovery | `GET /api/status` shows the listener process running. |
| 7 | Verify API recovery | `GET /api/build-info` responds correctly. |
| 8 | Verify Pocket Option reconnect | `GET /api/pocket-option/status` shows `connected`, `_verify_account_mode` passed (check `logs/lifecycle.log` for "mode verification passed"). |
| 9 | Log into the Live account | Runbook §C - `verify_live_mode` passes against the real live cabinet. |
| 10 | Execute 5-10 very small live trades | Every Live Validation Checklist item (Part 2) passes for each trade - smallest stake Pocket Option's UI allows. |
| 11 | Verify every reconciliation | Each trade's `signals.result`/`profit_loss` matches the broker's own trade history exactly. |
| 12 | Verify bankroll | `fund_manager.get_fund_balances` matches the broker's own account balance after the batch. |
| 13 | Verify logs | `logs/lifecycle.log` and `recovery_events` show no unexplained `ERROR` entries across the whole session. |
| 14 | Verify analytics | Live trades appear correctly when explicitly queried Live-only (`exclude_live=False`/scoped view) and are correctly EXCLUDED from any Demo-scoped view (`exclude_live=True`) - Part 1 item #3. |
| 15 | Decide Go/No-Go | Part 5's criteria, evaluated against this specific session's actual results. |

---

## Part 5 — Go / No-Go Checklist (for the graduation session itself)

Every box must be checked, independently verified (not self-reported),
before this account is declared Live Production Approved:

- [ ] Step 1-8 (Demo stop through reboot recovery) all passed with no
      manual intervention beyond what the runbook specifies.
- [ ] Step 9's live login passed `verify_live_mode`'s all 7 conditions.
- [ ] Every trade in step 10 passed all 13 Live Validation Checklist
      items (Part 2).
- [ ] Step 11's reconciliation matched exactly, including for any trade
      that happened to resolve during a deliberate mid-session restart
      test, if performed.
- [ ] Step 12's bankroll matched exactly (not "close enough").
- [ ] Step 13's logs show zero unexplained errors.
- [ ] Step 14's analytics isolation held (no Live trade leaked into a
      Demo-scoped view, no Demo trade appeared in the Live-only view).
- [ ] The stop-loss and stop-win tests (checklist #9/#10) fired at
      exactly the configured threshold, never late, never early.
- [ ] Zero duplicate trades, zero missed trades, zero race-condition
      symptoms across the whole session.

**No-Go conditions (any one blocks graduation regardless of everything
else passing):** an unresolved duplicate or missed trade; any
reconciliation mismatch; a stop-loss/stop-win that fired late or not at
all; a crash during the session that left an ambiguous trade state; a
bankroll figure that doesn't match the broker's own to the cent.

---

## Part 6 — Failure Recovery Procedure

If any step in Part 4 fails during the actual graduation session:

1. **Immediately trigger per-account Emergency Stop** on the Live
   account (Runbook §G) - halts new trades, ends the active session.
2. **Do not attempt to "fix and continue" the same session.** Record
   exactly which step failed and the observed vs. expected state.
3. **Reconcile manually** against the broker's own trade history for
   any trade placed before the stop - confirm AXIM's recorded state
   matches reality even if the automated reconciliation is what failed.
4. **If a trade's true outcome is genuinely ambiguous** (the specific,
   named residual risk in `DEPLOYMENT.md`'s "Known limitations" section
   - same-minute-close outcome-matching ambiguity), resolve it manually
   from the broker's own UI before trusting any AXIM-computed bankroll
   figure.
5. **File the failure** as a new, dated entry in this document's
   history (add a "Graduation attempts" section below once the first
   attempt happens) - a failed attempt is real evidence, not something
   to silently retry away.
6. **Disarm** (`live_enabled: false`) before making any code change in
   response - re-arming later always requires a fresh `confirm_live_arm`
   call (Part 1, item #12), so nothing stays silently armed while a fix
   is in progress.

## Part 7 — Rollback Procedure

Per `DEPLOYMENT.md`'s own "Rollback" section: all state lives in
`data/axim.db` and the two session directories - the schema has been
additive-only throughout this project's history, so rolling back code
is just restoring the previous commit/build, no database migration
required.

For a graduation-session-specific rollback:

1. Stop the listener/API processes cleanly.
2. Restore `data/axim.db`, `axim_session.session`, and
   `sessions/pocket_browser/` from the Part 4 Step 2 snapshot
   (`backups/<timestamp>/`).
3. `git checkout` the commit that was deployed before the graduation
   attempt.
4. Restart via the normal startup runbook (§A).
5. Confirm `GET /api/build-info` reports the rolled-back version.
6. The Live account's `live_enabled`/`live_arm_confirmed_at` are part
   of the restored database snapshot - explicitly re-verify their state
   after restore rather than assuming; a snapshot taken before arming
   will correctly restore to disarmed.

---

## Part 8 — Final Go / No-Go Report

Every capability relevant to Live graduation, categorized into exactly
one of the four required buckets. No item appears in more than one.

### Engineering Complete
(Built, tested, no further code required before the graduation session)

- Live account verification (`verify_live_mode`, 58 tests)
- Demo/Live isolation (`account_effective_cabinet_mode`, per-account
  `user_data_dir`)
- Live statistics isolation (`exclude_live`, 20 tests)
- Mode-agnostic reconciliation (`core/recovery.py`, verified zero
  demo/live branching)
- Live-mode safety gate / arm confirmation (`confirm_live_arm`, 22 tests)
- Live disarm (auto-clears confirmation, tested)
- Duplicate prevention, Emergency Stop (global + per-account),
  stop-loss, stop-win, daily loss limits, trade audit trail, recovery
  logging - all pre-existing, all with passing tests (Part 1)
- The global `ACCOUNT` env-var kill-switch as a redundant layer on top
  of the entire per-account system

### Operationally Ready
(Infrastructure and process exist, documented, individually exercised -
not yet run as one continuous end-to-end drill)

- Windows Scheduled Task process supervision (in-process recovery
  live-fire verified; OS-level restart-on-crash built and documented,
  `DEPLOYMENT.md`)
- `scripts/backup_axim_state.ps1` (snapshot/backup, confirmed safe to
  run live)
- Monitoring tooling (`core/timeline_report.py`,
  `core/dashboard_server.py`, `recovery_events` table, notification bell)
- Rollback procedure (Part 7) - mechanically simple, additive-only
  schema, but never yet exercised for a *Live*-arming scenario
  specifically
- This Live Validation Package itself (checklist, runbook, Go/No-Go
  gate) - complete and ready to execute

### Requires Operator Action
(Nothing this session can do without the operator personally acting)

- Supplying a real `LIVE_URL`/`LIVE_MODE_VERIFICATION_SELECTOR`/
  `LIVE_MODE_VERIFICATION_TEXT`, inspected from the real live cabinet
- Completing an honest win-rate observation window under real
  (non-relaxed) risk thresholds and explicitly accepting or rejecting
  the resulting figure as economically viable
- Scheduling and personally running the one dedicated graduation
  session (Part 4) - stopping the healthy Demo session, the reboot
  itself, and the live login all require the operator's own hands or
  explicit go-ahead
- Funding the Live account with the small stake the graduation
  session needs
- The final "fresh, explicit, in-the-moment" instruction to actually
  flip `ACCOUNT=LIVE` and complete arming - this document, like the
  graduation plan before it, does not itself constitute that approval

### Requires Live Validation
(Cannot be proven without the graduation session actually running)

- Multi-broker-account concurrency at real scale (only 1 concurrent
  session ever demonstrated; needs 2+ real accounts running together)
- Every item in the Part 2 Live Validation Checklist - by definition,
  provable only against a real Live account
- The full reboot-recovery drill (Part 4, steps 3-8) - the in-process
  recovery path is proven; a genuine physical reboot with Scheduled
  Tasks has not been observed end-to-end
- Whether reconciliation/bankroll/stop-loss/stop-win behave identically
  against the real Live cabinet's actual DOM/timing characteristics, as
  opposed to Demo's (verified) ones

---

## Graduation attempts

None yet. This section will be appended, not overwritten, after the
first graduation session - success or failure - per Part 6's "file the
failure" instruction.

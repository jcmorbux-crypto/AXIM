# AXIM Live Readiness Checklist

**As of 2026-07-10.** Supersedes `docs/AXIM_LIVE_READINESS_REVIEW.md`
(2026-07-05, now stale) and consolidates `docs/AXIM_RELEASE_CHECKLIST.md`.
Read this before ever setting a broker account's `live_enabled` flag or
`ACCOUNT` to anything other than `DEMO`. Every line cites where the
evidence actually lives - check it doesn't assume.

**Bottom line: closer than the 07-05 review, still not a go.** Execution
mechanics, recovery, observability, and the missing drawdown breaker from
the last review are all done and verified. What remains is (1) one
manual, operator-only step that literally cannot be done by an AI agent -
inspecting your own real Pocket Option live cabinet - and (2) the honest,
still-open question from the last review: **does any signal source this
account watches actually have an edge net of payout.**

## Safety-critical gates (verify every one, every time)

- [x] **`ARMED` in the checked-in `.env`** - the master kill switch for
      whether `execution/pocket_executor.py:prepare_trade` ever calls
      `click_direction` at all (`execution/pocket_executor.py:25,156-166`).
      Convention: this stays `false` in `.env` except for a deliberate,
      watched run against `ACCOUNT=DEMO` (e.g. the soak test below) -
      never for a live account. **Check this file's actual current value
      before every session, not from memory.**
- [x] **`ACCOUNT=DEMO`** enforced independently by
      `risk_manager.check_demo_only()` (`core/risk_manager.py:43-45,217`),
      called from the coordinator's risk-check pipeline on every trade.
- [x] **Per-broker-account live gating**, layered on top of the two global
      switches above: `core/broker_account_manager.py:62-73`
      `account_effective_cabinet_mode()` only loads the live cabinet if
      `account["mode"] in ("live","both") AND account["live_enabled"]` -
      both DB-backed, set from the Broker Accounts UI, not `.env`. A
      `mode="both"` account is still demo-only in practice until
      `live_enabled` is explicitly flipped on for that specific account.
      Three independent gates (global ARMED, global ACCOUNT, per-account
      live_enabled) must all agree before a live click can happen -
      confirmed by reading the actual call chain this session, not
      assumed from the roadmap doc.
- [x] **`MAX_DAILY_LOSS` drawdown breaker** exists and is wired
      (`core/risk_manager.py:136-157`, enforced in
      `core/trade_coordinator.py:113`) - the gap flagged in the 07-05
      review is closed. Currently `100` in `.env`; this is a real active
      default, not a placeholder - set it to your own risk tolerance
      before any live use.
- [x] Every other risk rule (`MAX_TRADE_AMOUNT`, `MAX_TRADES_PER_HOUR`,
      `MAX_CONSECUTIVE_LOSSES`, `COOLDOWN_AFTER_LOSS_SECONDS`,
      `DUPLICATE_SIGNAL_WINDOW_SECONDS`, `MINIMUM_PAYOUT`) is fail-closed
      by design (missing data rejects, doesn't allow) - but the current
      `.env` has several of these deliberately relaxed
      (`MINIMUM_PAYOUT=0`, `MAX_TRADES_PER_HOUR=1000`,
      `MAX_CONSECUTIVE_LOSSES=1000`, `COOLDOWN_AFTER_LOSS_SECONDS=0`) for
      the soak test below - **these are not the values to go live with**.
      `.env.example` has sane defaults to reset to.

## The one gate nobody but the operator can clear

- [ ] **`LIVE_URL` / `LIVE_MODE_VERIFICATION_CLASS` are unset in `.env`,
      on purpose.** `execution/browser_warmup.py:89-101` raises
      `LiveModeNotConfiguredError` and refuses to start any account
      requesting the live cabinet until both are set to values an
      operator has personally verified by inspecting their own real live
      Pocket Option account page in devtools (`config/settings.py`'s
      `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` docstring,
      `docs/AXIM_APP_PLAN.md`). This is a deliberate fail-closed design,
      not an oversight - AXIM will not guess what a live cabinet looks
      like. **This step requires your own live account access and
      judgment; it is not something to script or automate around.**

## Does the signal source have an edge? (the question that actually matters)

- [x] **No longer zero real-source signals** - the 07-05 review's
      critical finding #1 is out of date. `WATCH_CHANNELS` currently
      includes `PocketOption_quant_algorithm_bot,go_plusbot`, and the
      current soak run (see below) has processed 489 real signals from
      these sources, with 49 wins / 82 losses / 5 draws recorded
      (win rate among decided trades: **~37%**, excluding draws).
- [ ] **That win rate is not evidence of "no edge" or "an edge" yet,
      stated as honestly as the original review stated the opposite
      gap**: this soak run has `MINIMUM_PAYOUT=0` and other risk rules
      relaxed specifically to avoid rejecting signals mid-stress-test
      (same caveat the 07-05 review raised about the production stress
      test's data) - a real deployment with `MINIMUM_PAYOUT=90` and the
      other real thresholds from `.env.example` would reject a
      meaningfully different, smaller set of these signals, so this
      37% figure is not a preview of enforced-production performance.
      **Before any live trial: re-run a watched observation window with
      real, non-relaxed risk thresholds and read the resulting win rate
      net of actual payout - that number, not this one, is what should
      inform a go/no-go.**

## Long-running soak test

- [x] **In progress, healthy, not yet "complete" by any fixed target**
      (no specific duration was ever defined as the finish line - see
      `docs/AXIM_PRODUCTION_READINESS_REPORT.md` §6). The listener has
      been running continuously since before this session
      (`logs/soak_listener_stdout.log`, live-updating); its own
      structured health log (`logs/soak_test_log.csv`, written by
      `scripts/soak_snapshot.py`) shows **11.3+ hours** of a prior
      15-minute-interval snapshot window with 0 orphaned processes, a
      stable ~37MB listener / ~950-1150MB across 9 Chrome workers, and
      `heartbeat_stale=False` throughout - genuinely healthy, not
      inferred.
- [x] **Monitoring gap found and fixed this session**: the snapshot
      Scheduled Task's window was a one-time 12-hour repetition that had
      quietly expired at 11:27 while the actual listener kept running
      underneath it uninterrupted - a monitoring gap, not a soak-test
      failure. Replaced the ad-hoc task with a reusable, longer-window
      one: `scripts/install_soak_snapshot_task.ps1` (defaults to 7 days,
      re-run anytime to extend). Re-armed at time of writing.
- [ ] **Recommendation**: let it keep running. Check
      `logs/soak_test_log.csv`'s tail periodically for `errors_total`
      climbing faster than `signals_total`, `heartbeat_stale=True`, or
      `chrome_count` growing unbounded (a real leak signature) - none of
      those have occurred yet.

## Functional / operational (carried forward from the 07-05 checklist, re-verified)

- [x] Full automated regression suite passes: **503 tests, OK
      (1 skipped)**, re-run this session (`python -m unittest discover -s
      tests -p "test_*.py"`) - up from 420 at the last checklist,
      reflecting the multi-Fund/auth/Strategy Lab/billing work since.
- [x] Browser-crash and process-restart recovery both previously
      live-fire tested against the real production code (not
      reimplementations) - see `docs/AXIM_ROADMAP.md`'s "Process-level
      supervisor live-fire tested" section.
- [x] Process supervision configured: `scripts/install_scheduled_task.ps1`
      (listener) and `scripts/install_api_scheduled_task.ps1` (API) both
      verified current this session (real paths, correct `.env`-driven
      bind address).
- [x] Backup/retention plan exists and was previously verified live
      (`scripts/backup_axim_state.ps1`).
- [x] `INSTALL.md`/`USER_GUIDE.md`/`DEPLOYMENT.md` plus the new
      `docs/AXIM_SETUP_GUIDE.md` and
      `docs/AXIM_DEMO_VALIDATION_CHECKLIST.md` are current.
- [x] `requirements.txt` gap fixed this session: `pydantic` was imported
      throughout `api/` but not pinned (rode in transitively via
      FastAPI) - now pinned to the installed `2.13.4`.
- [x] Dead config removed this session: `TRADE_DELAY` and `SAVE_HTML`
      were defined in `config/settings.py`/`.env` but read nowhere in the
      codebase - deleted rather than left as misleading no-op knobs (same
      reasoning as the earlier `MODE` cleanup).

## Known, accepted, non-blocking gaps

- [x] **Live account balance display - implemented, not yet live-verified.**
      Was `api/main.py:545-568` returning `"balance": None` deliberately
      rather than fabricating a number. Closed this session:
      `pocket_dom.read_balance()` reads `.balance-info-block__balance
      .js-hd`'s `data-hd-show` attribute (confirmed against a real
      captured page, `logs/failures/*/page.html` - not guessed), wired
      into both the legacy heartbeat loop (new `ui_listener_heartbeat.
      balance` column, `COALESCE`-protected against a transient read miss
      overwriting a known-good value) and a new per-broker-account
      refresh loop (`core/broker_account_manager.py`'s
      `_balance_refresh_loop`, populating the previously-unpopulated
      `broker_accounts.last_balance`/`last_balance_checked_at` columns
      the UI already displayed). 505/505 tests pass (2 new). **Not yet
      exercised against a real running browser** - doing so would have
      required restarting the listener process driving the soak test
      above, which would have reset its uptime continuity. Confirm the
      Balance panel populates a real number the next time the listener
      restarts (a normal restart for any other reason is enough - no
      special action needed).
- **Risk-profile bankroll does not auto-update from real P&L** during
      live operation the way the backtester carries balance forward -
      Percent/Kelly sizing will use a stale bankroll unless the operator
      updates it manually between sessions. Treat as a manual pre-session
      step, not (yet) an automated one.
- True-simultaneous burst-traffic DOM contention, the settlement-window
  crash-overlap edge case, and same-minute closed-item matching ambiguity
  are all unchanged, still fail-safe (never a wrong result, only an
  occasional failure to record one), documented in
  `docs/AXIM_PRODUCTION_READINESS_REPORT.md` §4.

## What would actually need to happen before considering live (in order)

1. Keep the soak test running; periodically check for the failure
   signatures listed above.
2. Run a fresh observation window with **real, non-relaxed** risk
   thresholds (`.env.example` defaults) to get an honest win-rate/edge
   read - the current 37% figure is soak-test-relaxed, not that.
3. **You personally** inspect your real live Pocket Option cabinet and
   set `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` - no one else can do
   this step.
4. Only after step 2 shows a real edge net of payout, consider flipping
   a single broker account's `live_enabled` on, at the smallest possible
   stake, watched deliberately - the same discipline every demo test in
   this project has followed.

This document does not recommend a timeline for any of the above - it
exists so the remaining gate is visible and unambiguous.

## Sign-off

- [ ] Reviewed and approved for live use by: ___________ (date: ______)

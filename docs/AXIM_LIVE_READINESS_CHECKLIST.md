# AXIM Live Readiness Checklist

**As of 2026-07-11 (AXIM Core RC1).** Supersedes
`docs/AXIM_LIVE_READINESS_REVIEW.md` (2026-07-05, now stale) and
consolidates `docs/AXIM_RELEASE_CHECKLIST.md`. Read this before ever
setting a broker account's `live_enabled` flag or `ACCOUNT` to anything
other than `DEMO`. Every line cites where the evidence actually lives -
check it doesn't assume.

**Bottom line: closer than the 07-05 review, still not a go.** Execution
mechanics, recovery, observability, and the missing drawdown breaker from
the last review are all done and verified. AXIM Capital Strategies (tm)
Phase 1+2 shipped this session without weakening any safety gate (see
below). A real listener-supervision gap was found, fixed, and the fix
confirmed against the actual Scheduled Task trigger path this session
(see "Long-running soak test" below). What remains is (1) one manual,
operator-only step that literally cannot be done by an AI agent -
inspecting your own real Pocket Option live cabinet, and (2) the honest,
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
      current soak run (see below) has processed 499 real signals from
      these sources, with 49 wins / 82 losses / 5 draws recorded
      (win rate among decided trades: **~37%**, excluding draws - the
      wins/losses/draws split has held steady as `signals_total` climbed,
      the newer signals landing in `rejected_total` instead).
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
- [x] **Real disruption event this session - found, contained, root-caused,
      fixed.** Launching the axim-desktop Remote Client in local mode
      (verifying the "Package the Remote Client" deliverable) spawned a
      *second* `telegram_listener.py` against this exact live install,
      since local mode had no detection for an already-running listener.
      The two fought over the single persistent Chrome profile lock
      (`sessions/pocket_browser`) - confirmed via `logs/axim.log`:
      repeated `BrowserType.launch_persistent_context: Opening in
      existing browser session` errors from the second process every
      30-60s until it was killed by hand (twice, while root-causing it).
      **What was NOT affected, directly verified via the live heartbeat
      table both times**: `listener_pid` stayed `10524` throughout,
      `generation` never incremented (no crash-triggered rebuild),
      `worker_count` stayed `6` - the real browser session/worker pool
      was never actually displaced, only annoyed. Chromium's own
      singleton-lock correctly refused the second instance every time;
      that's exactly why no corruption occurred. Real, if minor, side
      effect: a few extra idle tabs accumulated in the real browser
      (`chrome_mem_mb` rose from ~870MB to ~1.6GB, `chrome_count`
      9→25) - within normal headroom, not touched further to avoid
      risking the real worker pool with a blanket cleanup. Also polluted
      `errors_total`/`recovery_events_total` in the CSV history around
      23:15-23:35 with entries from the second process's own repeated
      failed startup attempts, not the real listener - same "don't take
      a spike at face value without checking the timestamp" caveat as
      the test-suite log-pollution note earlier in this doc.
      **Root cause fixed**: `axim-desktop/src-tauri/src/lib.rs`'s
      `spawn_axim_processes` now independently checks liveness before
      starting each process - the API port (already existed) and, new,
      the listener's own `ui_listener_heartbeat` freshness (same 45s
      threshold `api/main.py` already trusts) before starting
      `telegram_listener.py`. Verified correct in both directions
      against a throwaway install (fresh heartbeat → skips spawning;
      stale heartbeat → spawns normally) - not re-tested against this
      real listener a third time, deliberately, to stop risking further
      disruption. Both installers rebuilt with the fix.
- [x] **Verified live this session, on this exact machine, against the
      real listener**: launched the built installer binary directly.
      The heartbeat-freshness guard worked correctly - no duplicate
      `telegram_listener.py` spawned (real listener's `generation`
      stayed `1`, `listener_pid` stayed `10524` throughout, confirmed via
      `database.get_listener_heartbeat()` before/after). **Real, narrower
      gap found**: the guard only covers the listener spawn, not the API
      server spawn - a duplicate `uvicorn api.main:app` process did start
      briefly. Immediately stopped, zero effect on the real server (a
      second process binding the same port either fails or serves
      redundantly - either way the real one, PID 7700/whichever is
      actually fronting traffic, was never replaced). Low priority: this
      machine is the server, not a remote-mode client, so a normal
      laptop deployment (remote mode, spawns nothing locally) never hits
      this path at all. Also found and removed one unrelated orphaned
      `telegram_listener.py` (PID 7472, parent process already exited,
      running since before this session-continuation started, never
      holding the real browser-profile lock per its own generation
      count staying flat) - harmless, now cleaned up. This machine's
      stray `remote_client_config.json` (persisted "local mode" from
      earlier testing) was also cleared so the installer shows the
      mode picker on next launch instead of auto-spawning again.
- [x] **Second, more serious incident this session - the listener actually
      stopped, and nothing brought it back.** PID `10524` (the soak
      test's real listener, ~34 hours uptime at the time) was confirmed
      healthy via a fresh heartbeat at 10:27:32, then went completely
      silent - `heartbeat_stale=True` for three consecutive 15-minute
      snapshots, and the OS process itself was gone (`Get-CimInstance
      Win32_Process` found nothing). Root cause of the crash itself is
      **not established** - no traceback or exit message near that time
      in `logs/axim.log` (its last entries were unrelated test-suite
      output from an earlier `pytest` run sharing the same log file, a
      pre-existing pollution quirk, not evidence either way). Explicitly
      ruled out: this session's own process cleanup around the same
      window (killing an orphaned duplicate listener and a duplicate API
      server) - confirmed safe via a heartbeat check showing `10524`
      still alive and writing fresh heartbeats *after* those specific
      kills, well before it actually went dark.
      **Real gap found**: `10524` had been running for ~34 hours with
      **no process supervision at all** - `scripts/run_listener_
      supervised.ps1` exists and the "AXIM Listener" Scheduled Task was
      already installed, but the task's only trigger is `AtLogOn` and it
      hadn't fired since 7/8 (days earlier); whoever/whatever started
      `10524` did so directly (bypassing the supervisor), so when it
      died, nothing was watching to restart it. This is exactly the
      failure mode `run_listener_supervised.ps1`'s own docstring
      describes - just never actually in the loop for this particular
      run.
      **Fixed by restarting through the supervisor** (`scripts\run_
      listener_supervised.ps1`, launched directly rather than waiting for
      a logon event) instead of a bare process - confirmed reconnected
      cleanly (`demo_mode_verified: 1`, `worker_count: 6`, a real
      populated `balance`, heartbeat updating every ~30s, uptime climbing
      steadily, watched for several consecutive updates with zero
      restart-cycle entries in `logs/supervisor.log`).
- [x] **Scheduled Task's actual trigger path confirmed, same session,
      without a disruptive full machine reboot.** A real OS reboot would
      affect more than AXIM (everything else running on this machine) and
      wasn't something to trigger unilaterally - instead, cleanly stopped
      the manually-launched supervisor+listener from the item above, then
      ran `Start-ScheduledTask -TaskName "AXIM Listener"` - the exact same
      action Windows itself runs on the task's `AtLogOn` trigger, just
      invoked directly rather than waiting for an actual logon event.
      Confirmed via `logs\supervisor.log` (a fresh "started, watching..."
      entry at the trigger time, no restart-cycle noise after) and the
      live heartbeat (`demo_mode_verified: 1`, `worker_count: 6`, new
      `listener_pid`, uptime climbing steadily 7.1 -> 8.7 minutes across
      two direct checks, balance still populated). The task's *action* is
      proven correct end to end; a full physical reboot/logon remains the
      only literally-untested variant, and there's no reason to expect it
      to behave differently - the task's action is the entire mechanism,
      or lack thereof, that a real logon event would invoke.

## Functional / operational (carried forward from the 07-05 checklist, re-verified)

- [x] Full automated regression suite passes: **644 tests, OK
      (1 skipped)**, re-run this session (`python -m pytest tests/ -q`) -
      up from 420 at the last checklist, reflecting the multi-Fund/auth/
      Strategy Lab/billing work, the heartbeat-balance and
      `tests/test_pocket_dom.py` DOM-parsing tests, and (later in this
      session) the full AXIM Capital Strategies (tm) addition plus wiring
      it into the existing historical Backtest Engine. Narrows,
      but does not close, the "no automated coverage for the DOM
      interaction layer" gap from the 07-05 review: the actual
      browser-touching selectors/clicks still have no automated coverage
      and rely on the manual `tests/manual_click_test*.py` scripts,
      honestly unchanged.
- [x] **AXIM Capital Strategies (tm) does not weaken any safety-critical
      gate above - confirmed by reading the call chain, not assumed.**
      `core/risk_engine.py`'s `compute_position_size` (now with Apex
      Ascension/Momentum/Cashflow/Sentinel/Fortress/Empire layered in) only
      ever produces a dollar `amount` or raises a clean rejection
      (`core/trade_coordinator.py:186-192`); it has no path to `ARMED`,
      `ACCOUNT`, or a broker account's `live_enabled` flag, all three of
      which are still enforced entirely downstream and independently
      (`execution/pocket_executor.py`, `core/risk_manager.py`,
      `core/broker_account_manager.py`) exactly as before this work
      started. A new sizing_mode can change how much is staked; it cannot
      change whether a live click is allowed to happen at all.
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

## Security audit (this session)

A focused pass on auth/session security, SQL injection, XSS, secrets at
rest, and CORS - not previously covered by any earlier review. Full
findings and reasoning in commit history; summarized here.

- [x] **SQL injection**: none found. Every f-string-built query only
      interpolates column names, gated by a hardcoded whitelist
      (`_UPDATABLE_FIELDS` and siblings in `core/database.py`) that raises
      before the string is built; every value goes through `?`
      placeholders.
- [x] **Password hashing**: PBKDF2-HMAC-SHA256, 600k iterations, random
      salt, constant-time comparison (`core/auth.py`) - solid.
- [x] **Session tokens / password-reset tokens**: `secrets.token_urlsafe(32)`
      (256-bit), hash-only storage, reset tokens are single-use, 30-min
      TTL, prior token invalidated on a new request, full session
      revocation on reset - solid.
- [x] **Secrets at rest**: Telegram credentials are Fernet-encrypted
      (`core/secrets_store.py`) before storage, masked on read-back; no
      secret found logged anywhere.
- [x] **CORS**: real allowlist, empty by default, explicit `*` rejection
      (incompatible with credentialed requests anyway) - not a
      wildcard-by-default footgun.
- [x] **Stored XSS via attribute-breakout - found and fixed.** Every
      page's `escapeHtml()` helper (17 near-identical copies across
      `web/*.html`, plus a new shared one added to `web/shell.js`) only
      encoded `& < >`, not `"`/`'`. Several call sites build
      `onclick="...('${escapeHtml(x)}')"` - a double-quoted HTML
      attribute containing a single-quoted JS string. A value containing
      `"` (e.g. a Telegram channel title, which is attacker-influenced -
      any channel owner can set it) could break out of the `onclick`
      attribute entirely and inject arbitrary handlers. Fixed by
      encoding `"`/`'` too, in all 17 copies plus a new one in
      `web/shell.js` (which had its own separate, more-permissive
      `<`-only ad hoc escape for notification messages - replaced with
      the same shared helper). Also fixed `web/shell.js` rendering the
      logged-in user's own email/role/access_tier unescaped (self-XSS at
      most, fixed anyway for consistency).
- [x] **No brute-force protection on login - found and fixed.**
      `verify_user_credentials`'s own docstring already claimed to check
      "isn't locked out" with nothing implemented behind it. Added real
      per-account lockout: `LOGIN_LOCKOUT_THRESHOLD` (10) consecutive
      failed attempts locks the account for `LOGIN_LOCKOUT_MINUTES` (15);
      a successful login always resets the counter; the login response is
      identical (generic 401) whether the account is locked or the
      password is simply wrong, so lockout state is never leaked.
      Deliberately per-account, not per-IP/global, matching this app's
      actual threat model (a private, trusted-network deployment where
      the real risk is unlimited guessing against one known email, not a
      distributed attack this app has no visibility into). 3 new tests
      (`tests/test_database_users.py`).
- 523/523 tests pass after these fixes (up from 520).

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

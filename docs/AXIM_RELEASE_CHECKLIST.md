# AXIM Production Release Checklist

Use this before increasing stakes, running unattended, or scaling signal
volume beyond what's been validated. Check items against real evidence, not
assumption - each line below cites where that evidence already exists, or
flags that it's still needed.

## Safety-critical (must all be true)

- [x] `ARMED` is not set to `true` in the checked-in `.env` (verify at
      release time, not just historically - this has held for the whole
      project but is a one-line regression risk)
- [x] `ACCOUNT=DEMO` unless a deliberate, reviewed decision has been made to
      go live
- [x] `risk_manager.check_demo_only()` and `BrowserWarmupService`'s
      demo-mode check both independently verified present and functioning
- [x] Every risk rule (`MAX_TRADE_AMOUNT`, `MAX_TRADES_PER_HOUR`,
      `MAX_CONSECUTIVE_LOSSES`, `COOLDOWN_AFTER_LOSS_SECONDS`,
      `DUPLICATE_SIGNAL_WINDOW_SECONDS`, `MINIMUM_PAYOUT`) is set to a value
      that reflects an actual, deliberate decision - not left at whatever a
      prior test session configured
- [x] A maximum-daily-loss/drawdown circuit breaker exists
      (`risk_manager.check_max_daily_loss()`, `MAX_DAILY_LOSS` in `.env`,
      default 100) - catches a steady bleed-out through an alternating
      win/loss pattern, which `MAX_CONSECUTIVE_LOSSES` alone cannot. 4 new
      unit tests, including one that explicitly proves the premise
      (consecutive-losses does NOT trip on the same alternating pattern
      that daily-loss does)

## Functional

- [x] Full automated regression suite passes (`python -m pytest tests/` -
      526 tests as of this release, up from 420 at the prior release and 53
      at initial release; covers the multi-fund/multi-broker-account
      architecture, concurrent trading sessions, Fund-owned Rule Builder,
      AI Strategy Lab, the client/server real-time sync layer, and the
      admin-privilege-escalation regression tests added this release)
- [x] Parser validated against every asset category (forex, crypto,
      commodity, stock, index) and against real messages from the actual
      production signal source
- [x] Production stress test executed and reported
      (`docs/AXIM_PRODUCTION_READINESS_REPORT.md`)
- [x] Browser-crash recovery confirmed (simulated crash → automatic
      reconnect → pool rebuild → next trade succeeds)
- [x] Process-restart recovery confirmed (killed with a trade open → restart
      → `recovery.py` re-attaches tracking → trade closes correctly)
- [ ] A genuine multi-hour soak test has run to completion (in progress at
      release time - see the Production Readiness Report §6 for current
      status; do not increase stakes before this completes cleanly)

## Operational

- [x] `INSTALL.md`, `USER_GUIDE.md`, `DEPLOYMENT.md` exist and are current
- [x] `requirements.txt` reflects actual runtime dependencies
      (telethon, playwright, python-dotenv)
- [x] Process supervision configured - Windows Scheduled Task "AXIM
      Listener" registered (`scripts/install_scheduled_task.ps1`),
      auto-starts at logon, auto-restarts up to 999x (1 min apart) on
      failure
- [x] Log rotation confirmed working (`core/logger.py`, 5MB × 5 backups per
      logger by default)
- [x] A backup/retention plan exists (`scripts/backup_axim_state.ps1`) -
      backs up `data/axim.db`, both session files, and the Chrome profile,
      keeps the most recent 14 by default; verified live against real
      state (gracefully skips locked Chrome files while AXIM is running
      rather than aborting)

## Client/Server & Remote Access (added this release - see docs/AXIM_ROADMAP.md for full detail on each)

- [x] All 14 Remote Client capability areas (Mission Control, Funds,
      Trading Sessions, Trade Center, Strategy Lab, Automation Studio,
      Signal Sources, Broker Accounts, Performance, Notifications, User
      Management, Settings, Help Center) sync in real time via SSE where
      they have live server state to sync - audited and closed the gap
      (was 5/14) live against a real running server, not by reading the
      code
- [x] Emergency Stop and Live-mode trade confirmations push instantly to
      every connected client (previously 2-5s polling) - the two most
      safety/time-critical pieces of live state in the app
- [x] A Remote Client shows a connection-loss indicator when its own SSE
      stream actually drops (debounced 4s to avoid false alarms on normal
      reconnects)
- [x] Login brute-force lockout (5 attempts -> 15min lock), verified live
      against a real running server
- [x] Connected Devices' "Revoke" (and an expired trial) actually
      terminates an already-open SSE stream within 30s, not just new
      requests - closed a real, documented gap between behavior and what
      `docs/AXIM_REMOTE_ACCESS.md` claimed
- [x] `/docs`, `/redoc`, `/openapi.json` disabled by default
      (`ENABLE_API_DOCS`) - was exposing the full 159-endpoint schema,
      admin routes included, to anyone who could merely reach the API
- [x] Standard HTTP security headers added (`X-Frame-Options`,
      `X-Content-Type-Options`, `Referrer-Policy`, conditional HSTS) -
      none existed before; verified live including on the SSE stream
      specifically (the case most likely to break under global response
      middleware)
- [x] Two stored-XSS classes found and fixed (unescaped signal data on
      Mission Control; an attribute-context escape bypass affecting 3
      pages) - fixed at the root (removed the injection point) rather than
      patching the escaping function, and re-swept the whole `web/`
      directory afterward for the same anti-pattern
- [x] **Privilege escalation fixed**: a plain "admin" account could
      previously grant itself (or anyone) the "owner" role, or demote an
      existing owner, through the ordinary user-management endpoints -
      verified live and exploitable before the fix, confirmed blocked
      (both directions) after it, with the legitimate owner-to-owner
      transfer path still working. `api/admin.py` had zero test coverage
      before this release; now has 10 dedicated regression tests
- [x] Audit logging added for financial/risk-critical actions (fund
      create, either half of the Live-trading double-switch, session
      starts, strategy deploys, rule changes) that previously left zero
      trace of who did what - verified live against the real Logs page
      endpoint
- [x] Full accessibility pass: modals (keyboard/focus/`role`/Escape, with
      the Live-trade confirmation modal deliberately excluded from
      Escape-dismissibility - verified it still can't be dismissed that
      way), 111 form labels linked to their inputs, 7 keyboard-unreachable
      clickable elements fixed, notification bell ARIA state, screenshot
      alt text, a real (visually-hidden) `<h1>` on Mission Control
- [x] Mobile responsiveness re-verified live at 375px width across all 16
      authenticated pages - zero horizontal overflow, off-canvas nav
      drawer confirmed working
- [x] CSRF exposure checked (SameSite=Lax cookies + confirmed no
      state-mutating GET endpoints exist) - already solid, no fix needed
- [x] File-upload endpoints (CSV/Excel signal import) checked - entirely
      in-memory, no filesystem writes, no user-controlled filenames, no
      path-traversal surface
- [x] Desktop client (`axim-desktop`) version strings synced to the API's
      own version, and unedited Tauri scaffold placeholder metadata
      (description, authors) replaced with real values
- [x] Root `README.md` (was completely empty) and a missing favicon (there
      was none at all) both added
- [x] Final full-page visual QA pass: all 17 authenticated `web/` pages
      (Mission Control, Funds, Trading Sessions, Signal Sources, Signal
      Inspector, Risk Engine, Automation Studio, Strategy Lab, Trade
      Center, Performance, Notifications, Broker, Users, Logs, Settings,
      Plan & Billing, the onboarding wizard) screenshotted live via
      Playwright against a freshly bootstrapped server and reviewed
      individually - zero console errors, zero rendering/UX defects found
- [x] Mobile re-verification at 375px extended to the pages added this
      release (Notifications, Broker, Plan & Billing, the onboarding
      wizard) - zero page-level overflow on any of 18 pages checked, and
      confirmed live that wide tables (e.g. Users) scroll horizontally
      within their own card as designed rather than being clipped -
      `table.scrollWidth (806px) > table.clientWidth (289px)` with
      `overflow-x: auto`, not a page-level overflow bug
- [x] **Session-hijack gap fixed**: self-service Settings > Security >
      Change Password did not revoke other active sessions, unlike the
      forgot-password reset flow (which already did, for the same
      credential-compromise-recovery reason) - a stolen session survived
      a legitimate password change. Fixed with
      `database.revoke_other_sessions()` (keeps only the session making
      the change); verified live with two real browser contexts (an
      "attacker" session's `/api/auth/me` went 200 -> 401 the moment the
      real owner changed their password, while the owner's own active
      session stayed valid). `api/auth_routes.py`'s `change_password` had
      zero test coverage before this; now has 4 dedicated regression
      tests
- [x] **Brute-force bypass on change-password fixed**: found while
      reviewing the fix above - `change_password`'s own "current
      password" check never called `record_failed_login`, unlike
      `login()`. A hijacked/stolen session (all this endpoint requires,
      not the password itself) could brute-force the real account
      password with unlimited attempts. Fixed by mirroring `login()`'s
      exact lockout check/record pattern; verified live (6 wrong
      attempts against a real server: 5x 401, 6th 429 with a lockout
      timestamp). 3 more regression tests added (7 total on this route)
- [x] **Owner-creation race condition fixed**: `bootstrap_owner()`'s
      "no owner yet" check and the account creation that followed it
      were non-atomic - proved this was a real, not theoretical, bug by
      racing 10 concurrent requests against the unlocked code and getting
      **10 owners created at once** on the first try. Fixed with an
      in-process lock (confirmed the API always runs as a single uvicorn
      process, so this is a complete fix); re-ran the same 10-way race
      with the fix and got exactly 1 owner, 9 clean 409 rejections. 2 new
      regression tests added
- [x] **Duplicate concurrent trading sessions fixed**: found via a
      dedicated audit of trading-safety-critical code for the same
      check-then-act race class as the two fixes above.
      `start_trading_session()`'s "no active session on this broker
      account" check and its own docstring called this "the real
      concurrency boundary," but the check and the INSERT that followed
      were non-atomic - two near-simultaneous session-starts (a
      double-click, two browser tabs) could both pass, leaving two
      sessions independently trading against one physical broker login.
      Didn't reproduce under plain unlocked threading (too narrow a
      window for fast SQLite calls), so proved it with a forced
      interleaving test instead: with the window artificially widened
      and no lock, 5/5 concurrent requests succeeded, creating 5
      duplicate sessions; with the fix, the identical test produces
      exactly 1. Fixed with an in-process lock inside the database
      function itself, protecting every call site
- [x] **Session max_trades cap could be exceeded fixed**: the second
      finding from the same audit. A `require_confirmation` session's
      `max_trades` cap was checked once, well before the human-
      confirmation wait - two signals arriving close together could both
      pass that check, both get confirmed, and both increment an
      unconditional counter with no re-check, each one also proceeding
      to real worker-pool acquisition and (in a live run) actual broker
      execution beyond the configured cap. Proved it first: reverting to
      the old unconditional increment and racing 10 threads against a
      3-trade cap produced `trades_count = 10`. Fixed by making the
      increment itself atomic and conditional
      (`... WHERE trades_count < max_trades`), returning whether it
      succeeded; the caller now rejects (same as every other pipeline
      stage) instead of proceeding when it doesn't. The identical 10-way
      race with the fix now stops at exactly 3. 3 new regression tests
      added
- [x] **Automation Studio rules could double-fire fixed**: a rule's
      false->true edge-trigger check compared against a snapshot read at
      the start of `evaluate_all()` (called once per Fund's own closed
      trade, not globally serialized) - two trades on two different
      Funds closing within milliseconds of each other could each read
      the same stale pre-fire state and both execute the action, e.g.
      double-vaulting the same profit via `_act_move_profit_to_vault`.
      Proved it first: reverted to the old read-then-write shape and
      raced 10 threads on the same edge - 2 fired (should be at most 1).
      Fixed by making the edge-trigger claim itself an atomic conditional
      `UPDATE ... WHERE last_condition_state = 0`; the identical race now
      fires exactly once. 1 new regression test added; the existing
      sequential edge-trigger test still passes unchanged
- [x] **Broker-account double-connect race fixed**: `POST
      /{account_id}/connect` checked `connection_status` and only later
      spawned the login subprocess and wrote the new status, as separate
      steps - a double-click on "Connect" (ordinary operator use, not an
      attack) could spawn two login processes against the same Chrome
      profile directory at once. Fixed with an atomic conditional claim
      (`database.claim_broker_account_connecting`,
      `UPDATE ... WHERE connection_status != 'connecting'`) replacing
      the separate check-then-spawn-then-write sequence. 4 new
      regression tests added, including a 10-thread concurrency proof
- [x] **Mid-login disconnect could be silently undone fixed**:
      `disconnect_broker_account` never tracked or killed the connect
      subprocess, so clicking "Disconnect" while a login attempt was
      still running left that script running unaffected - when it later
      finished, it wrote its outcome unconditionally, silently
      overwriting the operator's own explicit disconnect back to
      "connected". Proved it directly: disconnect, then simulate the old
      unconditional write - result was "connected" anyway. Fixed with
      `database.finalize_broker_account_connection`, an atomic
      conditional write (`WHERE connection_status = 'connecting'`) that
      only succeeds if nobody disconnected in the meantime. 4 new
      regression tests added
- [x] Broader security/architecture sweep after the check-then-act audit
      technique showed diminishing returns (3 consecutive audits found
      3, then 1, then 1 fixable issues): desktop client's Rust
      concurrency handling, FastAPI event-loop-blocking exposure,
      SQL-injection exposure across every f-string-built query, debug-
      mode stack-trace leakage, and the SSE resync/gap-recovery signal
      end-to-end (every subscribing page implements `onResync`) - all
      confirmed clean, no changes needed
- [x] **Soak-test error tracking fixed**: `scripts/soak_snapshot.py`'s
      `count_new_error_lines()` silently reported zero new errors,
      forever, after `axim.log` rotates (`core/logger.py`'s
      `RotatingFileHandler`) - exactly the kind of event a real
      multi-hour soak test run will hit, defeating the tool's entire
      purpose during the one scenario it exists for. Proved it directly:
      simulated a rotation with a real `ERROR` line present immediately
      after - old code reported 0. Fixed by treating a `last_count`
      larger than the current line count as "rotated," not "no new
      lines." 4 new regression tests added - zero test coverage existed
      on this script before this
- [x] **API process Scheduled Task had the listener's already-fixed
      silent-non-restart gap**: `install_scheduled_task.ps1` (the
      listener's installer) documents a live-fire finding that Windows
      Task Scheduler's `RestartOnFailure` doesn't trigger on a forcibly-
      terminated process, and wraps the listener in a supervisor script
      because of it - but `install_api_scheduled_task.ps1`, for the
      control-plane process every Remote Client depends on, was never
      updated to match; it called uvicorn directly and relied solely on
      the same proven-unreliable Task Scheduler setting. Fixed by adding
      `scripts/run_api_supervised.ps1` (the same supervisor-loop pattern
      as the listener's) and switching the Task's action to it. Verified
      live: syntax-checked, then smoke-tested the actual `Start-Process`
      mechanics (real venv Python, isolated scratch port, no real
      Scheduled Task registered, no stray processes left behind) -
      confirmed a genuine 200 OK after cold-start and clean termination
      on `Stop-Process -Force`
- [x] **Two dated readiness/review docs corrected**: `docs/
      AXIM_PRODUCTION_READINESS_REPORT.md` and `docs/
      AXIM_LIVE_READINESS_REVIEW.md` both made specific, decision-
      influencing claims that are now false - most notably "hold AXIM
      Desktop UI development" (the UI is built and hardened) and "no
      real signal from the trusted source has ever been processed" (it
      has, per the roadmap's "Version 1 production hardening" section).
      Added status banners correcting only what was actually re-verified
      - explicitly did not claim every item in either document was
      resolved, since overclaiming would just be a new stale-
      documentation problem
- [x] **`pocket_dom.py` pure-function test coverage added**: the DOM-
      interaction functions genuinely can't be unit-tested without a
      real browser (unchanged, accepted limitation), but several pure,
      dependency-free functions in the same file - including
      `_closest_closed_item`, the exact trade-outcome disambiguation
      logic already flagged elsewhere as a real ambiguity source - had
      zero test coverage. Added `tests/test_pocket_dom_pure_functions.py`
      (22 tests), including a day-boundary-wraparound case for the
      closed-item matcher

## AXIM Core directive (private live-trading build - see docs/AXIM_ROADMAP.md for full detail)

- [x] Full requirements audit completed (3 parallel agents covering auth/
      Telegram/parser/broker; Funds/Sessions/Money-Management; Mission
      Control/Trade Center/Logs/Remote/Safety) - concrete gap list
      produced, ranked by real-world blocker severity
- [x] **Emergency Stop safety gap fixed**: Mission Control's Emergency
      Stop button called a route that never actually ended active
      sessions (a session-scoped route existed and worked correctly, but
      the global route the dashboard actually uses did not) - fixed with
      a shared `end_all_active_sessions()` helper used by both routes
- [x] **Queued-signal-survives-emergency-stop gap fixed**: nothing in
      `trade_coordinator.py`'s pipeline re-checked `emergency_stop`/
      `paused` once a signal was already past Telegram ingestion -
      confirmed via grep that no risk check looked at control state at
      all. Fixed with `risk_manager.check_not_stopped()`, checked first
      in the preflight AND re-checked after the confirmation-wait gate
      (the pipeline's only long, unbounded wait). Live-proved the
      confirmation-wait race specifically
- [x] **Live-mode confirmation upgraded**: replaced a bare browser
      `confirm()`/`prompt()` (missing account/balance/trade-size/
      Martingale-exposure disclosure) with a proper modal showing all of
      it, reusing already-built backend endpoints (no new API surface
      needed). Verified live end-to-end including the real Martingale
      ladder math ($25 fixed × 3 steps × 2.0x = $175, not a placeholder)
      and that an empty/wrong confirmation phrase correctly blocks
      submission
- [x] **Interactive Telegram bot trigger-command workflow built** - the
      single biggest true gap from the audit. Added
      `core/telegram_bot_trigger.py` (send command via Telethon's
      `conversation()` -> await reply -> parse -> route through the same
      multi-broker-account-aware `route_signal()` passive channels use ->
      wait for result if configured -> request next -> stop on any
      existing session limit, re-checked fresh every iteration).
      Found and fixed a real bug while building this: the passive
      `handler()` never checked `source_type`, so a bot's reply would
      have been double-processed (once as the awaited response, once as
      an ordinary pushed signal) - added the exclusion. Also added the
      missing `max_requests_per_session` UI control (had a DB column and
      backend validation but no way to actually set it) and
      `database.get_channel()`. The real Telegram send/receive
      interaction can't be live-verified without real API credentials
      (not available here) - same documented limitation class as
      `pocket_dom.py`'s DOM functions - so built for testability instead:
      12 new tests using a fake Telethon client with scripted replies/
      timeouts, covering the full request/parse/route/stop-condition
      matrix
- [x] **Profit Vault `daily_target`/`weekly_target` triggers fixed**:
      completely dead before this - selectable in the UI, zero
      implementation existed. Added `target_vault_skim()` (session-
      scoped, reuses `milestone_amount`, fires once per session when the
      target is first reached - not a repeating ladder like
      `milestone_based`). 4 new tests
- [x] **`risk_profiles`-level `max_trades`/`profit_target`/
      `max_session_loss` fixed**: stored, API-editable, documented as
      Money Management settings, but never read - only a session's own
      copies of these same three concepts were enforced. Added
      `session_manager._check_profile_limits()`, checked after the
      session-level limits (which still take priority if both are set -
      proved the ordering with a dedicated test). `max_daily_loss`
      deliberately left alone - a true calendar-day, cross-session
      aggregate, not a same-session concept like the other three. 5 new
      tests
- [ ] Compounding "modes" run identical generic logic regardless of
      selection (daily doesn't reset daily, every-win isn't win-
      triggered) - left as-is. `core/risk_engine.py`'s own docstring
      already documents this as a deliberate scoping simplification, not
      a broken promise; giving each mode real differentiated behavior is
      a product/architecture decision, not a same-shape bug fix
- [x] **Mission Control Demo/Live indicator added**: no way to tell at a
      glance whether the connected account was DEMO or LIVE - added a
      badge next to the status pill, shown in both the combined and
      per-Fund views
- [x] **Mission Control Stop Session control added**: only Start,
      Pause, and Emergency Stop existed, even though the spec requires
      Stop Session too. Added, wired to the existing
      `POST /api/sessions/{id}/stop`; deliberately hidden in the
      combined "All Funds" view when more than one session is active at
      once (ambiguous which one it would stop). Verified live: dismiss
      leaves the session active, accept correctly stops it
- [ ] Remaining Mission Control/Trade Center/Logs completeness gaps:
      per-Fund view still shows lifetime P/L instead of today's, no
      clear loss-limit status line, no last-signal-vs-last-trade
      distinction; Trade Center missing Fund/broker-account columns;
      parser has no dedicated logger - not yet addressed
- [x] **`INSTALL.md` rewritten** - the entry-point doc `README.md`
      itself links to was stale in the same "pre-web-UI" way
      `USER_GUIDE.md` was before an earlier session's fix, but was never
      caught at the same time: framed the whole client/server web UI as
      "(Optional)", led with manual `.env` editing as the primary setup
      path, and told readers to run a manual Telegram-login script
      confirmed now fully superseded by the web UI's own Connect
      Telegram flow. Rewritten to cover the AXIM Core directive's
      required deliverables directly - setup guide, first-run checklist
      (verified against the wizard's actual 8 steps in `web/wizard.html`,
      not written from memory), demo validation procedure, live
      readiness checklist - with every specific claim (Risk Engine
      template duplication, the two-gate Live-mode switch, the Live-
      confirmation modal, Emergency Stop's access, both processes'
      Scheduled Task supervision) cross-checked against current code
- [x] **AXIM Core Server + Remote Client packaging audited** - found
      already largely satisfied by existing infrastructure (`axim-desktop`
      Tauri app with local/remote mode picker, the two Scheduled Task
      installer scripts) rather than needing new work. Found and fixed one
      real gap: closing the desktop app's window force-killed both spawned
      processes without running the orphaned-Chrome cleanup step
      `USER_GUIDE.md` says a force-kill requires - now automated in
      `src-tauri/src/lib.rs`'s window-close handler. **NOT build-verified**
      - no Rust/Cargo toolchain in this environment; run `npm run tauri
      build` on a machine with the Rust + MSVC toolchain before relying on
      this change. Standalone bundled-installer packaging (no separate
      venv/checkout step) remains a known, accepted, not-yet-attempted gap
      - see `axim-desktop/README.md`'s "Known limitation".

## Known, accepted limitations at this release

(Detail in `docs/AXIM_PRODUCTION_READINESS_REPORT.md` §4 - listed here so
they're explicitly signed off on, not silently inherited.)

- [x] True-simultaneous signal bursts have a measured real DOM-contention
      failure rate - accepted because real Telegram traffic is naturally
      spaced
- [x] A browser crash landing exactly on a trade's outcome-read window can
      cause that one outcome to fail to record (fails safe, never records
      wrong) - accepted as a rare edge case
- [x] Same-asset/same-direction trades closing in the same clock-minute
      have residual outcome-matching ambiguity - pre-existing, accepted
- [x] `MODE=DEMO` in `.env` was dead config (only `ACCOUNT` is read) -
      removed. `PO_EMAIL`/`PO_PASSWORD` (also dead - login is handled by
      the persistent browser profile) were found during the same pass and
      annotated in `.env` rather than removed, since they're a plausible
      placeholder for a future automated-login feature
- [x] `check_max_daily_loss`/`check_daily_profit_target`/
      `check_max_consecutive_losses` (`core/risk_manager.py`) only see
      already-CLOSED trades (`get_realized_pnl_since`/
      `get_recent_results`) - a genuinely correct implementation of what
      they document ("realized net P/L"), not a broken promise like the
      races fixed above. A burst of near-simultaneous signals can all
      pass using identical stale data, since none of their outcomes are
      known yet (binary-option expiry is minutes away, not
      instantaneous) - this is inherent to the domain, not a lock-
      ordering bug a mutex can close. A real fix would mean changing
      what these breakers actually measure (e.g. a worst-case check that
      also counts in-flight trade stakes as if they were losses) - a
      genuine behavior change to live risk logic, not a bug fix
      restoring an existing guarantee, so it needs explicit product
      sign-off rather than being decided unilaterally. Accepted as a
      known limitation for this release; flagged for a future decision

## Sign-off

Do not check this box until every unchecked item above has either been
completed or is an explicit, documented, accepted risk with a named owner.

- [ ] Reviewed and approved for the intended deployment scope by: ___________

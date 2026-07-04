# AXIM Latency & Reliability Sprint Plan

**Status: PLAN ONLY. No code has been changed to produce this document.**
Every recommendation below is proposed, not applied. Nothing in this file
should be read as "already done" - see the explicit "Not yet applied" marker
repeated on every recommended change.

## Success targets (as given)
- Under 2 seconds when asset/expiry/amount are already selected (no-op path).
- Under 5 seconds when the asset must change.
- Every stage measured in milliseconds - not just the total.

## Current measured baseline
6 real samples, signal-received -> trade-opened: min 1.97s, max 3.62s, avg
3.12s. See `docs/AXIM_COMPETITIVE_BENCHMARK.md` §2 for the full caveat - this
is too small a sample to be a real distribution, and per-stage breakdown
currently isn't queryable (only ever written as one text line to a log file
that has already rotated once this session). **Fixing that is recommendation
#1 below**, because every other estimate in this document is a structural
read of the code, not a confirmed measurement, until it exists.

---

## Top 10 speed bottlenecks (ranked by estimated impact)

1. **No structural per-stage latency data.** `LatencyTracker` computes the
   right checkpoints (`telegram_received` -> `parsed` -> `risk_approved` ->
   `asset_selected` -> `expiry_set` -> `amount_set` -> `click_completed` ->
   `confirmation_detected`) but only logs them as text. There is currently no
   way to answer "which stage is actually the bottleneck" from data - only
   from re-reading source and guessing, which is what the rest of this list
   is built from. This is a measurement gap, not a latency cost itself, but
   it blocks validating every other item below.

2. **No WebSocket/network-layer observation for trade confirmation.**
   `click_completed`/`confirmation_detected`/outcome-tracking all wait on DOM
   state (`.no-deals` visibility) that is a downstream rendering effect of
   whatever real-time channel the page uses. See Benchmark doc §3.4.
   Potentially the largest single win available, and the most novel/risky to
   implement correctly (unknown message format until observed).

3. **Screenshot capture on the critical path.** Two `await page.
   screenshot()` calls per trade (`prepare_trade`'s "prepared" and "clicked"
   stages), fully synchronous, gating the very sequence being optimized.
   `SAVE_SCREENSHOTS` exists but isn't read from `.env` or checked before the
   call - currently decorative.

4. **`select_asset`'s change-path is ~6-7 sequential IPC round trips**
   (open picker -> wait panel -> fill search -> wait row -> evaluate
   tradeable -> click row -> wait symbol text -> close modal). This is very
   likely the dominant cost of the "asset must change" 2s-5s target case
   specifically, since the no-op path already short-circuits entirely.

5. **`select_expiry` does 3 sequential fill+verify round trips** (hours,
   minutes, seconds inputs) where one batched `page.evaluate()` write might
   achieve the same result in one round trip - unconfirmed whether Pocket
   Option's input handlers tolerate that instead of sequential user-like
   input events.

6. **Synchronous SQLite calls directly on the asyncio event loop.** 5+
   separate `sqlite3.connect()`/query/close cycles per signal
   (`record_signal_received`, 4 risk checks, `update_trade_status` calls),
   none offloaded via `asyncio.to_thread`. Individually cheap locally, but
   serialized and loop-blocking - a real concern as worker count/signal
   volume grows.

7. **Risk-manager checks run strictly sequentially** even though
   `check_max_trades_per_hour`, `check_max_consecutive_losses`,
   `check_cooldown_after_loss`, `check_duplicate_signal` are independent
   read-only queries. Combined with #6, this is 4+ blocking round trips done
   one at a time where they don't have to be.

8. **Worker health-check on every `acquire_worker()` call**, even on the
   fully-healthy path - a live `page.evaluate("() => 1")` round trip every
   trade. Previously measured at 0-31ms (Phase 5). Small, but free to shave
   with a short TTL ("checked healthy within the last N ms, skip").

9. **`MAX_CONCURRENT_WORKERS` defaults to 2** - each additional warm tab is
   cheap (same browser context, no new login), and this cap has not been
   tested at higher values. Free lever, unexplored.

10. **`select_asset`/`select_expiry`/`set_amount` run strictly sequentially**
    inside `prepare_trade`, but expiry entry and amount entry touch unrelated
    parts of the page and don't obviously depend on each other - speculative
    micro-optimization (run concurrently via `asyncio.gather` once asset
    selection completes), unconfirmed whether simultaneous Playwright actions
    on one page are safe/faster in practice vs. contending with each other.

---

## Top 10 reliability risks (ranked; marked where grounded in real evidence)

1. **[EVIDENCE FOUND]** `select_asset`'s post-click confirmation
   (`expect(symbol).to_have_text(asset_name)`) failed at least once in real
   history with a wrong-asset readback: *"Locator expected to have text
   'GBP/JPY'. Actual value: EUR/USD OT[C]..."*. Given `wait_for_trade_result`
   already has documented, confirmed evidence that the Opened/Closed tab
   selection is a **cross-tab shared UI state** (not per-tab), this strongly
   suggests `.current-symbol` (or the underlying selection state) may
   likewise be shared across tabs of the same browser context/account rather
   than genuinely tab-isolated. If true, this undermines the core assumption
   behind Phase 5's whole design - that concurrent workers on separate tabs
   are independent. This is the single most important item to actually
   investigate before any further concurrency work.

2. **[EVIDENCE FOUND]** `wait_for_trade_result` timed out waiting on
   `.no-deals` in at least 2 of ~9 trades that reached opened status (~22%)
   - both captures show a *"Sorry, this trading instrument is currently
   unavailable. Click to reload"* banner present on the page at failure time.
   The current polling loop has no detection of or recovery from this
   specific site state - it just times out after 45s and the trade's outcome
   is never recorded (breaks downstream risk tracking: consecutive-losses,
   cooldown, etc. all depend on knowing results).

3. **[EVIDENCE FOUND]** `error:abandoned_on_restart` appears twice in
   history - trades left open across a process restart that `recovery.py`
   could not (or did not) fully re-attach outcome tracking to.

4. Session/auth-expiry blind spot: `BrowserWarmupService.health_check()`
   only confirms the page is *responsive* (`page.evaluate("() => 1")`), not
   that the account is still authenticated/on the trading page. A logged-out
   or kicked session would report healthy and fail on the next real trade
   attempt instead of proactively reconnecting.

5. Silent double-failure in the diagnostics path itself:
   `_capture_failure`'s screenshot and HTML capture are each wrapped in their
   own try/except that logs and continues. If both fail during a genuine DOM
   verification failure, there is no diagnostic evidence left for the
   failure that mattered most.

6. `MAX_SIGNAL_AGE` defaults to 10 seconds against a measured real baseline
   already at 2-3.6s median - relatively little margin before a legitimate
   (not stale) signal gets silently dropped as too old, especially under any
   Telegram delivery jitter or a momentarily busy worker pool.

7. Burst-signal capacity: with `MAX_CONCURRENT_WORKERS=2` and
   `WORKER_ACQUIRE_TIMEOUT_SECONDS=5`, a burst of 3+ near-simultaneous
   legitimate signals results in outright rejection (`all_workers_busy`) for
   the overflow, not queuing beyond 5s - unclear if this matches the actual
   burst behavior of the trusted source.

8. Synchronous DB I/O (see speed #6) is also a reliability risk under
   contention - a stalled/locked SQLite write could stall a worker's event
   loop turn with no timeout of its own.

9. `verify_direction_controls_ready`'s hit-testing depends on a fixed
   `NEUTRAL_CLICK_POINT = (800, 500)` and specific selector/DOM assumptions,
   already flagged in its own code comments as needing re-verification "if
   the site's layout changes" - there is no automated check that would
   surface a silent layout change before it caused live failures.

10. Static, global (not per-asset) duplicate/cooldown windows
    (`DUPLICATE_SIGNAL_WINDOW_SECONDS=120`, `COOLDOWN_AFTER_LOSS_SECONDS=300`)
    mean a loss or duplicate on one asset can delay or block an unrelated
    asset's legitimate signal - a design assumption worth revisiting, not a
    bug, but relevant to the "professional platform" framing.

---

## Recommended changes (NOT YET APPLIED - awaiting approval)

Prioritized by estimated latency/reliability impact versus implementation
risk. Each references the bottleneck/risk number above.

### P0 - do first (low implementation risk, unlocks measuring everything else)
- **(Speed #1)** Persist `LatencyTracker` checkpoints to the `signals` table
  (one additive JSON column) instead of only logging text. Unlocks a real
  per-stage breakdown across every future trade.
- **(Reliability #1)** Directly test whether `.current-symbol` is
  cross-tab-shared, the same way the Opened/Closed tab sharing was
  originally confirmed - open two workers, select different assets on each,
  and observe whether either tab's displayed symbol gets clobbered by the
  other. This determines whether Phase 5's concurrency model needs a
  correction before anything else is built on top of it.
- **(Speed #3)** Make screenshot capture fire-and-forget
  (`asyncio.create_task`, not awaited) and make it actually respect
  `SAVE_SCREENSHOTS` from `.env`.
- **(Speed #8)** Add a short TTL to the worker health-check so it doesn't
  re-verify on every single acquire.

### P1 - do next (real wins, moderate implementation/testing risk)
- **(Speed #4, #5)** Prototype a batched `page.evaluate()` write for
  `select_expiry`'s three inputs, and profile whether `select_asset`'s
  round-trip count can be reduced (e.g., skip the redundant post-click
  `.current-symbol` re-verification and modal-close if they can be merged).
  Must be validated against real DOM behavior, not assumed safe.
- **(Reliability #2)** Add explicit detection of the "instrument currently
  unavailable, click to reload" banner inside `wait_for_trade_result`'s
  polling loop, with a defined recovery action (reload and re-check, or fail
  fast with a specific, actionable error instead of a generic 45s timeout).
- **(Speed #6, #7)** Move `database.py`'s sync SQLite calls off the event
  loop (`asyncio.to_thread`), and evaluate parallelizing the independent risk
  checks.
- **(Reliability #4)** Extend `health_check()` to confirm the page is still
  on/authenticated to the trading page, not just responsive.
- **(Speed #9)** Test `MAX_CONCURRENT_WORKERS` at 3-4 in demo and measure
  whether reliability (esp. finding #1 above) holds before recommending a
  new default.

### P2 - investigate, higher uncertainty or lower urgency
- **(Speed #2)** Passive WebSocket/CDP network observation on the existing
  authenticated demo session, to see whether trade confirmation appears
  there measurably earlier than the DOM does. Start read-only/observational
  (log what's seen, change nothing) before considering it as a confirmation
  source.
- **(Reliability #3)** Audit `recovery.py`'s re-attachment path against the
  two real `abandoned_on_restart` cases to understand why re-attachment
  didn't succeed.
- **(Reliability #5, #6, #7, #9, #10)** Lower-urgency hardening items -
  worth tracking, not blocking.
- **(Speed #10)** Concurrent expiry/amount entry - speculative, needs a
  controlled test before committing to it.

---

## What I need from you to proceed
Tell me which P0/P1/P2 items to greenlight (all of P0, a subset, or
something else) and I'll implement in that order, testing each live against
the demo account before moving to the next, consistent with how every prior
phase in this project has been validated.

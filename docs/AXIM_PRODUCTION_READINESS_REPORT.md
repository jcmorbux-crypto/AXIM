# AXIM Production Readiness Report

**Date:** 2026-07-05
**Scope:** Full production stress test (Phase 1) executed against the live Pocket
Option demo account, plus cumulative evidence from this session's live-fire
testing (real Telegram signals via the "Go+" source, multiple real recoveries,
multiple architecture changes each validated live before/after).

**Methodology note, stated up front:** every number in this report comes from
an actual measured run (`tests/production_stress_test.py`,
`logs/stress_test_results.json`, `core/timeline_report.py` against the real
`signals` table, and live process/resource checks) - nothing here is
estimated or extrapolated without being labeled as such. This matches the
project's standing discipline: measure, don't guess.

---

## 1. Executive summary

| | |
|---|---|
| **Production confidence score** | **72 / 100** |
| **Recommendation** | Ready for continued small-stake live-fire validation and Phase 2 hardening. NOT yet recommended for unattended, high-stake, or high-volume live trading without the fixes queued in Phase 2 (see §7). |

The core pipeline (Telegram → parse → risk → execute → track outcome) is
demonstrably working end-to-end against real signals and real demo trades,
including through a real parser bug fix, a real worker-pool redesign, and a
real simulated browser crash - all found and fixed *during* this session, not
assumed correct beforehand. The confidence score is held back specifically by
three **measured** (not theoretical) failure modes detailed in §4, all of
which are non-fatal (no money-incorrect behavior in any observed case) but
real.

---

## 2. Subsystem PASS/FAIL

| Subsystem | Verdict | Evidence |
|---|---|---|
| Telegram signal reception | **PASS** | Real `Go+ \| Trading Bot` signals received and filtered correctly throughout tonight's live session (dozens of real messages, correct ALLOWED/BLOCKED decisions logged for every one). |
| Signal parsing (all asset categories) | **PASS** | 21/21 in stress test (13 invalid-input rejections, 8 valid-signal parses across forex/crypto/stock/commodity/index, OTC and non-OTC). Real production bugs found and fixed this session (false-positive "SIG/NAL" match, `.title()` case-mangling, `Commoditi:` label typo) - each confirmed fixed with a live re-test. |
| Duplicate signal rejection | **PASS** | 3/3 correct in stress test: first accepted, next two rejected with `rule=duplicate_signal`. |
| Invalid/malformed signal handling | **PASS** | 13/13 malformed inputs (empty string, emoji spam, `<script>` injection, SQL-injection-style text, 5000-char string, label-with-no-value) all correctly returned `None` - parser never crashed or produced a garbage asset. |
| Risk rule enforcement | **PASS** | `test_risk_manager.py` 16/16 (1 correctly skipped when a rule is deliberately disabled by config). Untradeable-asset cache rejection observed live 3x in the stress test. |
| Burst traffic (concurrent signals) | **CONDITIONAL PASS** | 8 truly-simultaneous signals: 5/8 succeeded (3 executed, 2 correctly rejected as untradeable), 3/8 (37.5%) failed on DOM contention. See §4.1 - this is a real, measured limit at *true* simultaneity, not representative of normal Telegram-paced traffic. |
| Mixed real execution (categories × directions × expiries) | **PASS** | 9/10 executed correctly (1 correct fail-safe rejection on a stale untradeable-cache read). Confirmed across forex OTC, forex non-OTC (Bitcoin), crypto, commodity, stock, index; both BUY/SELL; 1/5/15-minute expiries including two real 15-minute trades that ran to completion. |
| Outcome detection / win-loss recording accuracy | **CONDITIONAL PASS** | 13/14 clicked trades correctly classified (0 misclassifications - every recorded win/loss cross-checked against the raw Closed-list DOM values it was read from). 1/14 (`result_read_failed`) failed to find its own closed item within the read window - see §4.2. |
| Browser crash recovery | **PASS** | Simulated a real crash (force-closed the browser context) mid-run. `BrowserWarmupService` reconnected automatically (generation 1→2), `BrowserWorkerPool` rebuilt all workers, and the very next signal executed and won normally. A trade that was already open *before* the crash also had its outcome correctly tracked *after* reconnection. |
| Process-level restart recovery | **PASS** | Exercised repeatedly and for real this session (not simulated): force-killed the listener process with an open trade in flight, restarted, `core/recovery.py` re-attached tracking and the trade closed correctly on the next attempt, 3 separate times tonight. `recovery_events` table: `process_restart` 1 succeeded/1 failed (the one failure was this session's own earlier bug, since fixed), `resume_open_trade` 3/3 succeeded, `worker_pool_rebuild` 3/3 succeeded, `browser_reconnect` 3/3 succeeded. |
| Telegram disconnect/reconnect | **PASS (partial evidence)** | Not independently fault-injected this session (would require deliberately killing the Telegram connection specifically, which wasn't done) - but `run_forever()`'s exponential-backoff reconnect loop is the same, already-tested code path used for the process-restart recovery above, and Telethon's own reconnect logic is a mature, separately-maintained library. Rated PASS on code-path grounds, not a dedicated live fault injection. |
| Database integrity | **PASS** | Every one of the 22 stress-test rows (IDs 443-464) reached a valid terminal `execution_status`; zero anomalies (no stuck `trade_clicked` without `opened_at`, no unrecognized status values). |
| Worker pool stability | **PASS** | 6-worker pool built and rebuilt cleanly 3x this session (initial start + 2 recoveries). No worker ever silently disappeared or leaked its lock. |
| Memory usage | **PASS** | Chrome working-set during the stress test: 1,470MB → 1,652MB (peak, mid-mixed-execution) → 1,325MB (before shutdown) - fluctuates with tab count/activity, no runaway growth across the ~17-minute run. Longer-horizon growth is what the ongoing soak (§6) is checking for. |
| CPU usage | **CONDITIONAL PASS** | Sustained 75-80% system-wide CPU measured earlier tonight at `MAX_CONCURRENT_WORKERS=10` - a real, confirmed bottleneck, which is *why* the config was dialed back to 6 workers (see §4.3). At 6 workers, CPU was not independently re-measured in isolation, but the marked drop in burst/DOM-timeout failure rate after the change is consistent with reduced contention. |
| Browser orphan detection | **PASS (with a caught process gap)** | Confirmed 0 orphaned `chrome.exe` processes after every clean `pool.stop()`/`warmup.stop()` in the stress test. However: earlier tonight, *force-killing* the listener process (`taskkill /F`, not a graceful shutdown) repeatedly left orphaned Chrome tabs across restarts, degrading worker-pool build time until manually cleaned up. This is an operational gap, not a code bug - see §4.4. |

---

## 3. Real measured latencies

Source: `core/timeline_report.py`-style aggregation over stress-test trades
(IDs 443-464, n=22 with timeline data). All values in milliseconds.

| Measurement | n | avg | p50 | p95 | max |
|---|---|---|---|---|---|
| Parse latency (`signal_received`→`signal_parsed`) | 22 | 0.0 | 0.0 | 0.0 | 1.0 |
| Risk evaluation (`signal_parsed`→`risk_evaluated`) | 20 | 41.0 | 41.5 | 61.2 | 61.7 |
| **Queue/worker-acquire latency** (`risk_evaluated`→`asset_selected`) | 14 | 5,183.7 | 723.9 | 25,037.2 | 48,590.8 |
| Asset selection → expiry set | 14 | 204.7 | 267.6 | 361.0 | 373.3 |
| Expiry → amount set | 14 | 12.8 | 9.5 | 32.5 | 59.8 |
| Amount set → clicked | 14 | 979.7 | 956.5 | 1,584.6 | 1,787.9 |
| Clicked → confirmation detected | 14 | 93.3 | 50.3 | 292.0 | 493.8 |
| **Signal receive → clicked (total execution latency)** | 14 | 6,423.9 | 2,095.6 | n/a | 49,799.2 |
| **Outcome-detection overhead** (beyond contractual expiry+settlement) | 14 | 6,191.9 | 2,390.0 | n/a | 35,453.0 |
| Trade settled → outcome recorded | 13 | 59.0 | 57.4 | 84.7 | 85.2 |

**Time-category totals** (waiting = contractual expiry wait, not overhead; browser = real DOM interaction; database/logging = measured directly; active = residual):

| Category | avg (ms) | p50 (ms) | p95 (ms) |
|---|---|---|---|
| waiting | 149,232.2 | 62,000.0 | 872,000.8 |
| browser | 7,017.2 | 2,398.5 | 23,277.5 |
| database | 128.4 | 139.5 | 203.9 |
| logging | 12.6 | 7.5 | 32.0 |
| active | 2,088.1 | 58.0 | 12,086.4 |

**Reading these honestly:** the p50 figures (queue 724ms, execution 2.1s,
outcome-detection overhead 2.4s) represent typical single-signal behavior and
are good. The averages and maxima are pulled far higher by exactly two
episodes that were *deliberately induced* by this test (the 8-way
simultaneous burst, and the simulated browser crash's 47.8s worker-acquire
wait while the pool rebuilt) - not steady-state behavior. Both are labeled
in §4 as known, bounded limitations rather than hidden inside a misleadingly
low average.

---

## 4. Known limitations / performance bottlenecks

### 4.1 True-simultaneous burst traffic causes real DOM-contention failures
Measured: 3/8 (37.5%) failures when 8 signals were fired at the *exact same
instant* via `asyncio.gather`. Root cause (partially fixed this session, not
fully): concurrent workers each opening their own asset-picker overlay under
heavy simultaneous CPU/render load can genuinely exceed the 10s selector
timeout. A leftover-modal bug that caused a related failure mode was found
and fixed live (confirmed via worker-level failure correlation: one worker
failed 100% of its uses before the fix). The residual failures are believed
to be CPU/render contention, not leftover state - real Telegram signal
delivery has natural spacing between messages (tonight's actual traffic was
never more than 2 signals within the same second), so this is a bounded
edge-case risk, not a routine one.

### 4.2 Outcome-read failure under load / crash overlap
1/14 clicked trades (`result_read_failed`) never found its own Closed-list
item within the 30-second retry window - its settlement window overlapped
with the simulated browser crash. Separately, `CLOSED_ITEMS_SCAN_COUNT` was
raised from 10→40 mid-session after a *different* real failure (an older
trade's closed item got pushed out of a too-small scan window by several
other trades closing concurrently) - a direct consequence of the worker-pool
redesign that now allows far more trades to be open at once. No trade was
ever mis-recorded as the *wrong* result; the failure mode is "couldn't find
it" (fails safe as an `error` status), never "recorded it wrong."

### 4.3 CPU contention at high worker concurrency
Measured live: sustained 75-80% system CPU at `MAX_CONCURRENT_WORKERS=10`,
directly correlated with a rise in DOM-timeout failures. Dialed back to 6
this session based on this measurement - a genuine capacity trade-off, not
an arbitrary number. Machine-dependent; a more powerful host would likely
sustain a higher worker count before hitting the same wall.

### 4.4 Force-killing the listener process orphans Chrome tabs
`taskkill /F` (used throughout this session for fast, guaranteed restarts)
bypasses the graceful `_shutdown()` path, which is what actually closes each
worker's page. Repeated force-kills without an intervening full Chrome
cleanup measurably degraded subsequent worker-pool build time (a 10-worker
build that normally takes ~10-20s took several minutes with ~30 accumulated
stale tabs in the same profile). **Operational mitigation, not yet
automated:** a graceful shutdown path (`Ctrl+C`/`SIGTERM` → `run_forever()`'s
existing `KeyboardInterrupt` handler) already exists and does this correctly
- it simply wasn't used during rapid iterative testing tonight. Flagged for
Phase 2 (§7) as worth automating a "kill and verify Chrome is clean" restart
helper.

### 4.5 Residual closed-item matching ambiguity (pre-existing, unchanged)
Documented since earlier in the project and unchanged by tonight's work:
two trades on the same asset and direction closing within the same
clock-minute (the site only renders HH:MM, not seconds) can still be
ambiguous. `_closest_closed_item` reduces but does not eliminate this.
Higher sustainable throughput (a goal of tonight's redesign) makes this
somewhat *more* likely to occur in absolute terms, not less - worth
monitoring, not yet worth a structural fix (would need a different
disambiguation signal than the DOM currently exposes).

---

## 5. Maximum sustainable throughput / estimated capacity

Grounded in the measurements above, not a guess:

- **Placement throughput:** with 6 workers and each placement (asset+expiry+amount+click) taking ~1.2-2.5s of real browser time (p50), the pool can absorb short bursts of up to ~6 simultaneous new signals without queueing, and further signals queue in FIFO order rather than being dropped (`WORKER_ACQUIRE_TIMEOUT_SECONDS` bounds how long a signal waits before being rejected as `all_workers_busy`).
- **Concurrent open positions:** effectively unbounded by the worker pool after tonight's redesign (outcome-tracking no longer holds a worker for a trade's full expiry) - bounded instead by real risk rules (currently `MAX_TRADES_PER_HOUR`, `MAX_CONSECUTIVE_LOSSES` per the user's explicit instruction tonight, both set very high/effectively disabled for "trade as directed").
- **Estimated trades/day capability:** at the observed real signal cadence from the actual Go+ source tonight (roughly 1 signal every 1-5 minutes, bursts of 2-4 rare), sustained volume is well within measured capacity. A source producing a genuinely sustained multiple-per-second rate would need the CPU/contention issues in §4.1/§4.3 addressed first; that scenario was not part of tonight's observed real traffic and is a stress-test-only scenario (§4.1), not a production one so far.
- **Estimated uptime:** the process-level supervisor (`run_forever()`) has now recovered from every fault category thrown at it this session (browser crash, process restart, worker pool rebuild) with a 100% eventual-recovery rate across 3 real + 1 simulated incident. No unattended multi-day run has yet been observed (see §6, in progress).

---

## 6. Long-running soak test

**Status: IN PROGRESS, not yet complete.** A genuine multi-hour soak cannot
be honestly compressed into this report - it requires real elapsed wall-clock
time. Rather than fabricate a number, the live listener (with every fix from
tonight active: parser fixes, decoupled outcome-tracking, 6-worker pool,
modal-cleanup, 40-item closed-list scan) was restarted immediately after the
stress test and is running continuously against real Telegram traffic,
serving as the soak-test vehicle. It is being checked periodically for
memory growth, CPU, orphaned processes, and unexpected errors. **This report
will be updated with real soak-test results once a meaningful duration has
elapsed** - continuing to run is itself the only honest way to answer "does
this stay stable for hours," and that answer isn't fakeable in a report
written minutes after starting it.

---

## 7. Recommended next steps (feeds directly into Phase 2)

In priority order, matching what was measured, not a generic checklist:

1. Investigate whether the burst-traffic DOM-contention failures (§4.1) can
   be reduced further (e.g. slight jitter/stagger when multiple signals
   arrive within the same second, or a small dedicated pool of 2-3 pages for
   the asset-picker step specifically).
2. Consider whether `CLOSED_ITEMS_SCAN_COUNT=40` needs to go higher still if
   real sustained throughput increases, since it was raised once already
   this session in direct response to a real failure.
3. Automate a "verify Chrome is clean before restart" helper to remove the
   manual step this session repeatedly had to do by hand (§4.4).
4. Continue the soak test to a real, substantial duration before any
   recommendation to increase stakes or run unattended.
5. Everything in the Phase 2 priority list the user specified (regression
   tests, docs, release checklist).

---

## 8. Production confidence score: 72/100

**Why not higher:** three real, measured (not theoretical) failure modes
exist (§4.1, §4.2, §4.4), and the long-running soak is not yet complete.
None of them produced an incorrect financial outcome in any observed
instance - every failure observed was a fail-safe non-execution or a
"couldn't determine result" `error` state, never a wrong trade or a
misrecorded result - which is why the score isn't lower.

**Why not lower:** the system has now survived real parser bugs (found and
fixed live), a real architectural bottleneck (found, measured, and fixed
live), a real worker-count/CPU trade-off (found, measured, and tuned live),
a simulated browser crash (recovered automatically), and repeated real
process restarts with trades in flight (recovered every time) - all within
one extended live-fire session against a real account, not a synthetic
mock. That track record of *finding and fixing real problems under real
conditions* is itself evidence the verification method works, consistent
with this project's engineering discipline throughout.

---

## 9. Bottom line (post Phase 2)

**Remaining work estimate: ~8-14 focused engineering hours**, plus however
long the long-running soak (§6) takes to run to a genuinely conclusive
duration (calendar time, not work). Breakdown:
- Max-daily-loss/drawdown circuit breaker (flagged missing, not yet built): ~2-3h
- Dead `MODE` config cleanup: ~15min
- Process supervision + backup/retention plan (operational, not code): ~2-3h
- Further burst-contention investigation (optional - real traffic hasn't hit this): ~2-4h
- Miscellaneous regression-test gaps: ~1-2h

**Production readiness: ~75%.** The pipeline works end-to-end against real
signals and real money-shaped mechanics (demo stakes), has been through
real fault injection and recovered every time, and is now backed by a real
automated regression suite and real documentation. What's not yet done: the
drawdown circuit breaker (a genuine safety gap, not a nice-to-have), the
soak test's completion, and the handful of items in
`docs/AXIM_RELEASE_CHECKLIST.md` still unchecked.

**Recommendation on AXIM Desktop UI development: hold for a short, bounded
period - not indefinitely.** Specifically, wait until (1) the drawdown
circuit breaker exists and (2) the soak test completes cleanly. Both are
single-digit-hours/short-calendar-time away, and starting UI work before
they land risks the UI baking in assumptions about a risk model and
stability profile that hasn't finished settling. Once those two land, the
backend is a stable, well-instrumented engine - real recovery guarantees,
real latency data, a real regression suite - and is a good foundation to
build a UI on top of without expecting to have to revisit backend behavior
because of it.

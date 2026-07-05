# AXIM Full Observability

**Status:** Built and verified with real trades. Every number in this
document's examples comes from an actual live-demo-account benchmark run,
not an estimate.

## What this is

Per the instruction that preceded this build: *"Before implementing
additional features, I want complete observability... Do not estimate.
Measure everything."* This replaces the P0 sprint's `LatencyTracker`
(8 cumulative-ms checkpoints, no category breakdown) with `core/timeline.py`'s
`TradeTimeline`: a 10-stage, absolute-timestamp timeline plus 4 genuinely
measured time categories, for every trade, persisted to the database.

## The 10 stages

Recorded as absolute wall-clock (ISO 8601) timestamps, not monotonic
elapsed-ms - a single trade's timeline can span two different OS processes
(a trade resumed by `core/recovery.py` after a restart continues its own
`track_outcome` in a brand-new process), and monotonic clocks aren't
comparable across that boundary.

`signal_received`, `signal_parsed`, `risk_evaluated`, `asset_selected`,
`expiry_set`, `amount_set`, `clicked`, `confirmation_detected`,
`trade_settled`, `outcome_recorded`.

`clicked` and `confirmation_detected` are marked separately (inside
`pocket_dom.click_direction`) - previously these were marked back-to-back
with no separating work, which silently produced a 0ms "confirmation
latency" that wasn't a real finding, just an instrumentation gap. Closed
as part of this build.

## The 4 measured categories (+ 1 residual)

- **waiting** - intentional, deterministic delays (the settlement sleep in
  `wait_for_trade_result`, sized to the trade's own known expiry).
- **browser** - real Playwright/DOM IPC (every public function in
  `pocket_dom.py` that touches the page).
- **database** - every `core/database.py` call.
- **logging** - every `logger.info/warning/error/...` call, timed via a
  transparent wrapper installed in `core/logger.py` - zero call-site
  changes needed anywhere in the codebase.
- **active** (not separately instrumented) - the residual: total trade
  duration minus the sum of the 4 measured categories. This mirrors how
  "wall time = CPU time + I/O wait + ..." is normally computed -
  instrumenting every line of pure-Python logic individually would be
  impractical and would itself add overhead. The subtraction is arithmetic
  on four genuinely measured quantities, not a guess.

### How this stays additive under concurrency

Category timers use a `contextvars`-based "current timeline" so
`database.py`/`pocket_dom.py`/the logger can record time without any of
their many callers threading a timeline object through - and so a
background `track_outcome` task correctly inherits the SAME timeline
object its parent `prepare_trade` used (contextvars propagate into
`asyncio.create_task()` children by reference-copy at creation time).

Two real bugs were found and fixed while verifying this arithmetic
actually holds up (i.e., categories should sum to ≈ total duration, with
"active" landing as a small positive residual - not always 0, which was
the tell that something was being double-counted):

1. **Fire-and-forget background work (screenshot capture) was being
   counted into the same trade's categories** even though it runs
   *concurrently* with the trade's own sequential execution - two
   operations that overlap in wall-clock time can't be summed additively
   against a wall-clock total, the same reason "CPU time" can exceed "wall
   time" on a multi-core system. Fixed by having that background task call
   `timeline.clear_current()` so its own work (screenshot IPC + the DB
   write recording its path) isn't attributed to any timeline at all.
2. **`persist()` double-counted categories across repeated calls on the
   same object.** A trade's timeline is written in two passes (once from
   `prepare_trade`, again later from `track_outcome`) using the SAME
   in-memory `TradeTimeline` - since `category_totals_ms` is cumulative on
   that object, resending the full total on the second `persist()` call
   summed it again on top of what the first call already saved. Fixed by
   tracking what this object has already persisted and sending only the
   delta each time.

Both were caught by the same discipline that's been used throughout this
project: verify the arithmetic against real measurements rather than
trusting the code compiled and ran. An isolated single-trade debug run
(no concurrency at all) was enough to expose both, since "active" landing
at exactly 0.0 on every single trade was implausible enough to investigate
rather than accept.

## Real measured example (10-trade benchmark, post-fix)

| Category | avg | P50 | P95 | P99 |
|---|---|---|---|---|
| waiting | 23003.0ms | 23000.0ms | 23015.0ms | 23015.0ms |
| browser | 2272.2ms | 2048.0ms | 2769.1ms | 2791.4ms |
| database | 136.1ms | 140.5ms | 173.1ms | 184.2ms |
| logging | 15.7ms | 15.5ms | 32.0ms | 32.0ms |
| active | 73.4ms | 72.4ms | 90.6ms | 91.9ms |
| total | 25500.4ms | 25297.6ms | 26012.1ms | 26014.4ms |

Stage transition `risk_evaluated->asset_selected` averaged 536.6ms across
the sample but is bimodal in the raw data - near-zero (26-58ms) when the
asset is already selected, ~1200-1340ms when it must change - consistent
with everything measured in the P0 sprint.

## Running it

```
python core/timeline_report.py [--limit N]
```

Prints a per-trade timeline (most recent 10) and aggregate P50/P95/P99
statistics for every stage transition and every category, across all
trades in `data/axim.db` that have timeline data. Percentile method:
linear interpolation between closest ranks (the numpy/Excel default),
spelled out in the module docstring since "P95" can otherwise silently
mean different things.

Read-only. Does not execute trades.

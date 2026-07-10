# AXIM Live-Mode Readiness Review

> **Status update - read this before the rest of the document.** This review
> predates `docs/AXIM_PRODUCTION_READINESS_REPORT.md` and several sessions of
> work since. Of the numbered "Critical gaps" below, three are now resolved -
> verified, not assumed:
> - **#1 ("Zero real signal-source trades, ever")** is resolved: AXIM was
>   later run against the real production signal source (`Go+ | Trading Bot`)
>   with `ARMED=true`, real trades placed with real wins and losses (see
>   `docs/AXIM_ROADMAP.md`'s "Version 1 production hardening" section).
> - **#2 (no `MAX_DAILY_LOSS`/drawdown circuit breaker)** is resolved:
>   `core/risk_manager.py`'s `check_max_daily_loss()` exists, is wired into
>   `trade_coordinator.py`'s risk-check sequence, and has dedicated
>   regression tests.
> - **#3 (`MODE=DEMO` dead/misleading config)** is resolved: removed.
> - **#5 (no supervisor above the Python process)** is resolved and, as of
>   this same pass, had a real gap closed: both the listener and the API
>   process now run under Windows Scheduled Tasks wrapped in a supervisor
>   script that correctly restarts on a forced termination (Task Scheduler's
>   own `RestartOnFailure` doesn't reliably catch that case - see the
>   roadmap).
>
> **#4, #6, and #7 were NOT specifically re-verified while writing this
> banner** - don't assume they're resolved just because the others are.
> #6 in particular (no automated regression suite for `pocket_dom.py`'s
> actual DOM selector/interaction functions specifically, as opposed to the
> worker-pool orchestration around them) still looks accurate: `tests/
> test_browser_worker_pool.py` and `tests/test_browser_warmup.py` exist, but
> no `test_pocket_dom.py` does. Treat this whole document as a dated
> historical record and check `docs/AXIM_ROADMAP.md` /
> `docs/AXIM_RELEASE_CHECKLIST.md` for current state before relying on any
> specific claim below, including the "Bottom line up front" immediately
> below this banner (which restates #1 and is the most acutely stale
> sentence in the document).

**Purpose:** an honest assessment of what would need to be true before `ARMED`
is ever considered for anything beyond deliberate, watched demo validation -
not a recommendation to enable it. Every finding below is grounded in what
was actually found in the code and the database, not assumed.

**Bottom line up front: not ready.** The system's execution mechanics are
now solid and extensively verified, but the single most important question
for live trading - *does the signal source actually have an edge?* - is
completely unanswered, because **no real signal from the trusted source has
ever been processed.** Everything measured to date is synthetic test data.
That alone is disqualifying regardless of how solid the execution layer is.

---

## What's genuinely solid

- **Demo-mode enforcement is structural, not just configuration.**
  `risk_manager.check_demo_only()` hard-fails unless `ACCOUNT=DEMO`, and
  `BrowserWarmupService` separately hard-fails startup if the page doesn't
  carry the `is-chart-demo` class - two independent layers, not one
  config flag to flip.
- **`ARMED` has never been touched in `.env`** across this entire project.
  Every test that needed it true set `os.environ["ARMED"]` in an isolated
  process only. This discipline has held for the whole engagement.
- **Execution reliability**: asset selection, expiry/amount entry, direction
  confirmation, and outcome detection have all been through multiple rounds
  of real-bug-found-and-fixed cycles (asset-inactive overlay false
  positives, win/loss misclassification, tab-state bugs, the outcome-
  detection concurrency bug, two timeline double-counting bugs) - each
  found through live testing against the real demo account, not assumed
  fixed. The pattern of *finding* real bugs this way is itself evidence the
  verification method works, not just that the code got lucky.
- **Observability is now comprehensive**: per-trade timelines, P50/P95/P99
  latency aggregates, real recovery-rate data across all 4 recovery layers,
  and a live dashboard - if something goes wrong in live operation, there's
  now a real way to see it quickly, which wasn't true a few sessions ago.
- **Recovery has been live-fire tested, not just code-reviewed**: real
  browser-crash simulation, real process-restart fault injection, and a
  real orphaned-open-trade resume all independently verified working and
  recording accurate outcomes.
- **Risk-rule fail-closed posture is consistent**: `WATCH_CHANNELS` empty
  blocks everything, missing payout reading is rejected not allowed, an
  unreadable demo-mode check refuses to start. This "fail closed, not
  open" pattern is the right instinct for anything money-adjacent.

---

## Critical gaps (in order of severity)

### 1. Zero real signal-source trades, ever
Queried `data/axim.db` directly: of 404 signal rows, **0** came from the
actual trusted source (`PocketOption_quant_algorithm_bot`, configured in
`WATCH_CHANNELS`). Every row is either an old untagged test row (283, no
channel/sender at all, IDs 1-304 spanning June 28-July 4) or an explicitly
named test/benchmark source (`latency-benchmark`, `concurrency-test`,
`resume-test`, etc.). **The live listener has never actually processed a
genuine incoming Telegram signal in production.** Every latency number,
every "trade prepared/clicked" measurement, every entry in the dashboard
right now reflects synthetic test signals with hand-picked assets and
directions - none of it says anything about whether this specific source's
signals would actually win money. This is not a code defect; it's simply a
question that hasn't been asked yet, and it's the only one that actually
matters for "should this go live."

### 2. No maximum-daily-loss or drawdown risk rule exists at all
The risk rule set is real and fail-closed for what it covers: `MAX_TRADE_
AMOUNT`, `MAX_TRADES_PER_HOUR`, `MAX_CONSECUTIVE_LOSSES`, `COOLDOWN_AFTER_
LOSS_SECONDS`, `DUPLICATE_SIGNAL_WINDOW_SECONDS`, `MINIMUM_PAYOUT`. But
`MAX_CONSECUTIVE_LOSSES` only catches an unbroken losing *streak* - an
account can bleed out steadily through an alternating win/loss pattern
(exactly what the binary-options payout structure, paying out less than
100% on a win, guarantees will happen on average with no edge) without ever
tripping it. There is no `MAX_DAILY_LOSS` / drawdown-percentage circuit
breaker anywhere in `config/settings.py` or `.env` - not even as unwired
dead config. This is a real, structural absence, not an oversight in
wiring.

### 3. `MODE=DEMO` in `.env` is dead and actively misleading
Grepped the whole codebase: `MODE` is never read anywhere. Only `ACCOUNT`
gates demo-vs-live (`risk_manager.check_demo_only()` checks `ACCOUNT`, not
`MODE`). `.env` has both `MODE=DEMO` and `ACCOUNT=DEMO` sitting next to
each other, looking like two related switches - a future operator (or a
future me, in a later session with less context) could reasonably believe
changing `MODE` does something. It does nothing. This is exactly the kind
of confusing-but-harmless-looking config that becomes dangerous the moment
someone is reasoning about how to safely enable live trading.

### 4. Historical DB data reflects relaxed-risk-rule test conditions, not enforced production behavior
Nearly every benchmark script this session (`tests/latency_benchmark.py`
and the various live-fire test scripts) deliberately set `MINIMUM_PAYOUT=1`,
`MAX_TRADES_PER_HOUR=1000`, `MAX_CONSECUTIVE_LOSSES=1000`, `COOLDOWN_AFTER_
LOSS_SECONDS=0` via `os.environ` for that process only, specifically so
real risk rules wouldn't contaminate latency measurements. That means the
dashboard's "recent signals" table right now shows trades placed at payouts
as low as 38-48% - well under the real `MINIMUM_PAYOUT=90` that would
reject them in actual operation. None of the historical win/loss/ROI
numbers in the dashboard represent what production risk enforcement would
have actually allowed through. Worth stating plainly so this data is never
mistaken for a preview of real behavior.

### 5. No supervisor above the Python process itself
`telegram_listener.py`'s `run_forever()` (live-fire tested and confirmed
working) catches exceptions *within* the running process and restarts the
browser/worker/Telegram stack in place. But if the OS process itself dies
outright - killed, machine reboots, a segfault in a native dependency -
nothing restarts `python core/telegram_listener.py` again. There's no
Windows Task Scheduler entry, no service wrapper, nothing outside the
process. For genuine 24/7 operation this is the outermost layer still
missing.

### 6. No automated regression suite for the DOM interaction layer
`tests/test_risk_manager.py` (16/16) is real, automated, and runs in
about a second with no browser. But every one of `pocket_dom.py`'s actual
selector/interaction functions - the part that clicks real buttons - has
only ever been verified through manual, live, one-off test scripts run
during development sessions. There's no CI-style suite that would catch a
regression there automatically; correctness currently depends on
remembering to re-run live verification after any change to that file.
This has worked so far because of consistent discipline, not because a
safety net exists independent of that discipline.

### 7. Limited real observation of the source's own behavior
The research module (`core/source_observer.py`/`source_profiler.py`) was
run for short, informal sessions early in this project - not a sustained
data-gathering period. There isn't enough real observational data yet to
know this source's actual signal cadence, typical lead time, edit/
correction frequency, or session/time-of-day patterns with any confidence.

---

## What would actually need to happen before considering live

In order - each step produces real evidence for the next, rather than
skipping straight to trusting the system with money:

1. **Run the live listener against the real source in observation/preview
   mode** (`AUTO_EXECUTE=false` or `PREVIEW_ONLY=true`, `ARMED` still
   `false`) for a real, meaningful stretch of time - long enough to
   accumulate genuine signals, not a few minutes. This is the only way to
   learn whether the source's signals would have actually won money, using
   real risk-rule thresholds (not the relaxed ones used for latency
   testing).
2. **Add a real daily-loss/drawdown circuit breaker** to the risk rule set
   - a genuine gap, not a nice-to-have.
3. **Remove or properly consolidate `MODE`** so there is exactly one
   switch that controls demo-vs-live, not two where only one does anything.
4. **Only after step 1 produces real win/loss data**, evaluate honestly
   whether the source has an actual edge net of payout, before spending any
   more effort on execution speed - a faster losing strategy is still
   losing.
5. **Consider an OS-level restart mechanism** (Task Scheduler "restart on
   failure" or equivalent) as the true outermost layer, if 24/7 unattended
   operation is the goal.
6. **If, and only if, all of the above hold up**, any live trial should
   start at the smallest possible stake, with the tightest possible risk
   caps, watched deliberately - the same discipline that's governed every
   demo test in this project so far, not a relaxation of it.

This document does not recommend taking any of these steps on any
particular timeline - it exists so the gaps are visible and explicit
before that door is opened.

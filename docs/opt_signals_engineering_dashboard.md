# AXIM OPT SIGNALS — Engineering Dashboard

Frozen baseline as of 2026-07-25. The provider audit is complete
(`docs/opt_signals_audit_ledger.json`, schema v2, 16/16 real Telegram folder
sources reconciled). This dashboard is the consolidated summary; it will be
updated only when a parser/normalization/assembler/replay-engine/routing
change triggers targeted revalidation, or when a provider's operational
status changes (enable/disable, Fund/broker assignment, graduation).

## Provider Summary (parser readiness — technical capability only)

| Parser Readiness | Count | Providers |
|---|---|---|
| VERIFIED | 12 | 162, 167, 187, 207, 9, 25, 44, 116, 163, 166, 199, 231 |
| PARTIALLY_VERIFIED | 1 | 216 (Pocket Option Signals — martingale-continuation gap) |
| BLOCKED | 3 | 16 (Go+, bot), 370 (Trading Booster Bot, bot), 372 (Trading Booster Elite, confirmed parser gaps) |
| **Total** | **16** | |

## Operational Summary (should AXIM trade this now — independent of parser readiness)

| Operational Status | Count | Providers |
|---|---|---|
| LIVE | 0 | — (globally blocked: `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` not configured) |
| DEMO | 1 | 162 Pro Trading Robot |
| OBSERVATION_ONLY | 1 | 207 NTrade |
| DISABLED | 4 | 167, 187, 9, 16 |
| NOT_ASSIGNED | 10 | 25, 44, 116, 163, 166, 199, 216, 231, 370, 372 |
| **Total** | **16** | |

Note the deliberate independence: 167, 187, and 9 are parser-VERIFIED but
operationally DISABLED — a fully correct parser does not imply the provider
should be trading. Conversely 207 is only OBSERVATION_ONLY despite a
VERIFIED parser, because no Fund/broker account is assigned yet.

## Provider Graduation Lifecycle

`DISCOVERED → PROFILED → REPLAY_VALIDATED → PARSER_VERIFIED → OBSERVATION_MODE → DEMO_VERIFIED → LIVE_VERIFIED`

| Stage | Count | Providers |
|---|---|---|
| DISCOVERED | 0 | — |
| PROFILED | 2 | 16, 370 (interactive bots — sampled/classified, not replay-validated) |
| REPLAY_VALIDATED | 3 | 116 (terminal — not a signal provider), 216 (real gap unresolved), 372 (real gap unresolved) |
| PARSER_VERIFIED | 9 | 167, 187, 9, 25, 44, 163, 166, 199, 231 |
| OBSERVATION_MODE | 1 | 207 |
| DEMO_VERIFIED | 1 | 162 |
| LIVE_VERIFIED | 0 | — (blocked globally on live-mode configuration) |

## Capability Coverage

Full detail: [`opt_signals_capability_matrix.md`](opt_signals_capability_matrix.md) (25 capabilities rated).

| Rating | Count | % |
|---|---|---|
| Supported | 15 | 60% |
| Partially Supported | 6 | 24% |
| Unsupported / No evidence | 4 | 16% |

Partially-supported/unsupported capabilities, linked to their closing gap:

- Interactive bots → [gap #6](opt_signals_gap_queue.md#6-interactive-bot-buttoncallback-capture-passive-logging-only)
- Reply-thread correlation → [gap #7](opt_signals_gap_queue.md#7-reply-thread-persistence-for-replay-verification)
- Edited messages / Cancellations → [gap #3](opt_signals_gap_queue.md#3-messageedited-cancellation-live-fire-confidence)
- Delayed follow-up messages → [gap #5](opt_signals_gap_queue.md#5-216-pocket-option-signals-martingale-continuation-attribution) (216) and channel 372 in [gap #4](opt_signals_gap_queue.md#4-channel-372-dedicated-adapter)
- Direction extraction / Narrative filtering → [gap #1](opt_signals_gap_queue.md#1-shared-parser-false-positive-hardening)
- Entry timing → [gap #2](opt_signals_gap_queue.md#2-entry-timing-extraction-unimplemented)
- Safe Option messages, FIRST/SECOND continuations → no real evidence in the OPT SIGNALS folder; no action queued

## Remaining Engineering Queue (ranked by live-trading impact)

Full detail: [`opt_signals_gap_queue.md`](opt_signals_gap_queue.md).

1. **Shared parser false-positive hardening** — High impact, High shared-component risk
2. **Entry-timing extraction unimplemented** — Medium-High impact, Low shared risk
3. **MessageEdited/cancellation live-fire confidence** — High impact if wrong, Low remaining effort
4. **Channel 372 dedicated adapter** — Low impact (disabled provider), Medium complexity
5. **216 martingale-continuation attribution** — Low-Medium impact, Medium-High shared risk (deliberately deprioritized)
6. **Interactive-bot button/callback capture (logging only)** — Medium impact, Low risk
7. **Reply-thread persistence for replay verification** — Low impact, audit-confidence only

## Execution-reliability priority order (per Phase Gate mandate)

1. Complete remaining parser gaps (queue items 1–2)
2. Complete MessageEdited support verification (queue item 3)
3. Finish interactive bot adapters (queue item 6, capture only — driving adapters need explicit authorization)
4. Verify every enabled provider through Observation Mode (currently: 207 in progress)
5. Execute repeatable Demo trades (currently: 162 proven)
6. Confirm broker acknowledgements / result reconciliation (proven for 162)
7. Confirm restart recovery, duplicate protection (proven — `process_control.py`, `trade_coordinator.py`)
8. Graduate providers to Live individually (blocked globally on `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS`, pending user's own live-account verification)

No UI redesign, analytics, backtesting, or new product features are in scope
during this phase.

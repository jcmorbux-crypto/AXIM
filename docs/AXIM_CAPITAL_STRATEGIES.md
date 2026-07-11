# AXIM Capital Strategies (tm)

**Status: Phase 1 + partial Phase 2 complete (2026-07-11).** Confirmed
product direction - see `memory/project_axim_capital_strategies.md` for
how this was confirmed (arrived via an unusual channel first, verified
directly with the user before any implementation started, per this
project's standing practice for consequential instructions).

**Phase 2 progress**: Momentum, Fortress, and Empire are real and wired
into live sizing (`core/risk_engine.py`), same standard as every Phase 1
strategy - not catalog-only. Axiom Vault gained its `per_trade` trigger
type, plus an on-demand `manual` transfer (`POST /api/sessions/
{session_id}/vault-transfer`) - no calculation, just an explicit call to
`database.add_to_vault`, the same function every automated trigger already
uses. Momentum and Fortress are genuine state machines whose math needs a
base_amount sourced from a DIFFERENT sizing mode's settings, so they don't
fit the quick single-path demo simulator honestly - they're marked
`implemented: true` but `simulate_supported: false`, same honest treatment
already used for Sentinel/Cashflow/Strike/Dominion/Phoenix. **Empire is
different**: its settings are fully self-contained (its own ladder, no
external base_amount), so it now runs through `simulate_strategy` for
real, reusing the exact same `empire_next_stake`/`empire_advance` the live
engine calls - a run stops the moment the ladder hits `challenge_complete`
or `terminated`, same as it would live. **QuantEdge (Kelly)** is also now
quick-simulatable - stateless, so it slotted into `_SIZE_FUNCS` directly,
using the identical f\* formula as `core/risk_engine.py`'s `kelly` branch.
Still open from Phase 2: Leviathan, Blackwater, Sniper (need more design
input - see "Deliberately not started" below).

## What this is

A rebrand + expansion of AXIM's existing risk-sizing engine
(`core/risk_engine.py`) into a full "Investment House" / named-strategy
catalog, presented as an institutional capital-allocation desk rather
than plain "money management." **Existing sizing modes, Martingale,
Compounding, and Profit Vault are unchanged** - this is additive, not a
rewrite. Every pre-existing risk_profile continues to behave exactly as
before (the new Cashflow/Sentinel/Apex Ascension features all default to
`enabled=0`).

## Architecture

- `core/capital_strategies.py` - the Phase 1 calculation engine. Pure
  functions (bankroll/settings in, a number or decision out), each
  directly unit-tested (`tests/test_capital_strategies.py`, 30+ tests)
  against the spec's own worked examples, not invented numbers.
- `core/capital_strategies_catalog.py` - the full 17-strategy / 4-house
  catalog as code-defined structured data (not a DB table - static
  reference content, same pattern as `web/shell.js`'s NAV_ITEMS).
- New `risk_profiles` sub-tables (`core/database.py`), following the
  exact same one-profile-has-many-config-tables pattern already
  established by `martingale_settings`/`compounding_settings`/
  `profit_vault_settings`: `apex_ascension_settings`,
  `drawdown_protection_settings` (Sentinel), `cashflow_settings`,
  `strike_settings`, plus `capital_tier_events` (the Apex Ascension audit
  trail).
- `api/capital_strategies_routes.py` - catalog browsing + a basic demo
  simulation endpoint. Strategy *instance* configuration reuses the
  existing `api/risk_engine_routes.py` (new PATCH endpoints for the four
  new sub-configs, matching the martingale/compounding/vault pattern
  exactly).
- `web/capital_strategies.html` - Investment House browsing → strategy
  catalog → strategy detail → configure/simulate, wired into the nav
  (`web/shell.js`). Live-verified in a real browser (screenshots taken
  during this session, not just code review).
- **Wired into live trade sizing**, not just the demo simulator:
  `core/risk_engine.py`'s `compute_position_size()` now has a real
  `apex_ascension` sizing_mode branch, plus Cashflow/Sentinel as opt-in
  post-processing layers that can reject a signal cleanly
  (`CashflowTargetReached`/`SentinelSuspended`, same `(rule, reason)`
  shape as every other rejection `core/trade_coordinator.py` already
  handles).

## Naming map (spec name → what it actually is)

| Capital Strategy | Underlying reality |
|---|---|
| Foundation (tm) | existing `sizing_mode='fixed'`, unchanged |
| Titan Allocation (tm) | existing `sizing_mode='dynamic'` (the one that recalculates against *current* bankroll - matches the spec's own worked example; the older static `percent` mode does not contract with bankroll and is intentionally left un-renamed) |
| QuantEdge (tm) | existing `sizing_mode='kelly'`, unchanged |
| Dominion (tm) | the existing multi-Fund architecture, relabeled only |
| Axiom Vault (tm) | existing Profit Vault, relabeled only (Phase 2 adds new trigger types) |
| Phoenix (tm) | existing Martingale - already step-capped by design, relabeled as an explicit standalone high-risk strategy, never presented as part of a conservative house |
| Apex Ascension, Cashflow, Strike, Sentinel | genuinely new calculations, Phase 1 |
| Momentum, Fortress, Empire | genuinely new calculations, Phase 2 |

## What's real vs. catalog-only right now

The catalog UI shows all 17 strategies with full philosophy/tagline/risk
content regardless of phase (the spec requires the catalog to be
complete even before every calculation exists). The "Run Simulation"
button appears for strategies with a real quick-simulate wiring today:
**Foundation, Titan Allocation, Apex Ascension, Empire, QuantEdge**.
Everything else marked `implemented: true` in the catalog (Sentinel,
Cashflow, Strike, Dominion, Axiom Vault, Phoenix, Momentum, Fortress) has
real, live calculations running through the existing Risk Engine / Funds
pages already - just not (yet) through this page's simplified demo
simulator, which the UI states plainly rather than showing a button that
would 400. Momentum and Fortress specifically are post-processing layers
that need a base_amount sourced from a DIFFERENT sizing mode's settings -
this single-strategy simulator has no honest source for that without
fabricating a convention the spec never defined, so they stay out
deliberately (Phase 3's real Strategy Lab integration is the right place
to solve this, not a guessed default here). **Leviathan, Blackwater,
Sniper, and Oracle** are genuinely not built yet - see "Deliberately not
started" below for why, rather than a rushed, fabricated version of each.

## Deliberately not started (Leviathan, Blackwater, Sniper)

These three need a real design decision or a new data source before
they can be built honestly, not just more engineering time:

- **Sniper** needs signal-level metadata (confidence, volatility, signal
  age at receipt) to filter on. `parsers/signal_parser.py`'s current
  output doesn't carry most of these - they'd need to be added to the
  signal schema first (or sourced from `core/source_profiler.py`'s
  research module), not invented at the strategy layer.
- **Blackwater** needs a "conviction level" classification (Watch /
  Qualified / Prime / Whale / Blackwater per the spec) computed from
  something - provider historical win rate, multi-source agreement,
  etc. - none of which AXIM tracks per-signal today. This is the same
  underlying gap Oracle (Phase 3's confidence-score engine) needs too;
  building Blackwater properly probably means building a shared
  scoring primitive both can use, not two separate ad hoc ones.
- **Leviathan** is a genuine multi-phase state machine (break-even
  objectives, "Pay Opportunities," controlled 2X sequences) with more
  free design parameters than the spec pins down precisely enough to
  implement without guessing at several judgment calls (how a "Pay
  Opportunity" is actually detected, phase-advancement thresholds).
  Worth a short design pass with the user before writing code, not
  worth fabricating defaults for.

## Known simplifications (stated plainly, not silently overclaimed)

- Sentinel's `drawdown_percent` and Cashflow's `period_realized_pnl` both
  reuse `core/risk_engine.py`'s existing session-scoped-P&L pattern
  (same one already documented for Compounding/Vault's daily/weekly
  modes) rather than true peak-tracking or calendar-spanning aggregation
  - a real simplification, not a bug.
- The demo simulator (`capital_strategies.simulate_strategy`) runs one
  seedable deterministic path, not a probability distribution - genuine
  Monte Carlo / historical backtesting is Phase 3's Strategy Lab
  integration, and the UI says so directly under every result.

## Next up

Remaining Phase 2: Leviathan, Blackwater, Sniper (blocked on the design/
data-source gaps above, not effort). Phase 3: Oracle, Phoenix/Momentum/
Fortress's full re-wiring into the quick simulator (needs the Strategy
Lab's richer multi-mode-aware simulation, not this single-strategy
helper), the Strategy Builder, Strategy Lab Monte Carlo/historical
backtesting, sportsbook support.

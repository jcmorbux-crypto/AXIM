# AXIM Capital Strategies (tm)

**Status: Phase 1 + Phase 2 complete except Leviathan/Oracle (2026-08-01).**
Confirmed product direction - see `memory/project_axim_capital_strategies.md`
for how this was confirmed (arrived via an unusual channel first, verified
directly with the user before any implementation started, per this
project's standing practice for consequential instructions).

**2026-08-01 update - Blackwater and Sniper are now real and live**,
resolving the "Deliberately not started" blocker below in a genuinely
different way than originally guessed at: a fresh product decision ruled
out ever inventing a "conviction level"/confidence score for either -
both are driven entirely by `core/provider_scorecard.py`, a provider's
own REAL, measurable trade history (win rate, sample size, profit
factor, expected value, payout, rolling windows, drawdown, streak,
signal age, signal rejection rate) computed from the real `signals`
table, nothing fabricated. Blackwater is a real `sizing_mode` in
`core/risk_engine.py`/`core/capital_strategies.py` (tiered
base/premium/elite/institutional stake percentages, gated by
configurable per-tier scorecard thresholds); Sniper is a hard
`core/risk_manager.check_sniper_qualification` preflight gate in
`core/trade_coordinator.py`. Both have real settings tables
(`sniper_settings`/`blackwater_settings`), full Money Management Studio
config UI (`web/risk.html`), a genuine walk-forward (no-lookahead)
backtest simulation in `core/backtest_engine.py`, and real
qualification-reason surfacing on Trade Detail
(`database.get_signal_detail`'s `rejection_reason` field). **Leviathan
and Oracle remain Definition Required** - see the updated "Deliberately
not started" section below; they are visible in the catalog
(`core/capital_strategies_catalog.py`, `definition_required: True`) but
cannot be activated, and no placeholder logic exists for either.

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
- `web/capital_strategies.html` (Investment House browsing → strategy
  catalog → strategy detail → configure/simulate) was live-verified in a
  real browser when first built, but was later deliberately removed - see
  "Next up" below. The catalog API (`api/capital_strategies_routes.py`)
  is still live; strategy configuration now happens only through
  `web/risk.html`'s Money Management Studio.
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
to solve this, not a guessed default here). **Leviathan and Oracle**
are genuinely not built yet - see "Deliberately not started" below for
why, rather than a rushed, fabricated version of each. Blackwater and
Sniper are real and live now (2026-08-01) but, like Sentinel/Cashflow/
Momentum/Fortress above, aren't wired into this simplified single-path
demo simulator either - Blackwater's tiered sizing needs a real
Provider Scorecard as input (no honest source for one in a stateless
demo call) and Sniper is a pass/fail gate, not a stake-size calculation,
so neither fits this simulator's shape.

## Deliberately not started (Leviathan, Oracle)

Sniper and Blackwater are DONE as of 2026-08-01 (see the status update
above) - the original framing below (needing invented "confidence"/
"conviction" metadata) turned out to be the wrong approach entirely; a
2026-08-01 product decision ruled that out and both were built instead
on real, already-collected Provider Scorecard statistics. Leviathan and
Oracle still need a real design decision before they can be built
honestly, not just more engineering time:

- **Leviathan** is a genuine multi-phase state machine (break-even
  objectives, "Pay Opportunities," controlled 2X sequences) with more
  free design parameters than the spec pins down precisely enough to
  implement without guessing at several judgment calls (how a "Pay
  Opportunity" is actually detected, phase-advancement thresholds) -
  and per the 2026-08-01 decision, any such rule must itself be defined
  in terms of real, measurable statistics, not invented ones. Worth a
  short design pass with the user before writing code, not worth
  fabricating defaults for.
- **Oracle**'s own spec concept - a "0-100 AXIM Confidence Score" - IS
  the fabricated AI/confidence scoring the 2026-08-01 decision forbids
  inventing. It needs a replacement product definition: a deterministic
  formula over real Provider Scorecard statistics that maps to a
  deployment band, with every recommendation traceable to the real
  numbers behind it, not a learned/opaque score.

See `core/capital_strategies_catalog.py`'s `definition_required_reason`
field on both entries for the exact same text surfaced to the catalog
API - kept in sync by hand, not duplicated blindly.

## Known simplifications (stated plainly, not silently overclaimed)

- Sentinel's `drawdown_percent` and Cashflow's `period_realized_pnl` both
  reuse `core/risk_engine.py`'s existing session-scoped-P&L pattern
  (same one already documented for Compounding/Vault's daily/weekly
  modes) rather than true peak-tracking or calendar-spanning aggregation
  - a real simplification, not a bug.
- The demo simulator (`capital_strategies.simulate_strategy`) runs one
  seedable deterministic path, not a probability distribution - genuine
  Monte Carlo is still Phase 3's Strategy Lab work (see below). Historical
  backtesting is a separate, already-existing feature
  (`core/backtest_engine.py`, predates this session) and is current -
  see "Backtest Engine now Capital-Strategies-aware" below.

## Backtest Engine now Capital-Strategies-aware (found and fixed this session)

`core/backtest_engine.py` (AXIM's existing historical-signal-replay
Strategy Lab, unrelated in origin to this session's Capital Strategies
work) reuses `risk_engine._base_amount`/`_apply_martingale` directly, but
before this fix its `simulate_strategy` loop never applied Momentum,
Cashflow, Sentinel, Fortress, or Empire's ladder advancement at all -
running a backtest on a profile with any of these enabled would silently
ignore them, understating real risk/behavior rather than erroring. Worse,
`_base_amount`'s `apex_ascension` branch calls `database.record_tier_event`
as a live side effect - every backtested tier crossing was writing a real
row into `capital_tier_events`, polluting that profile's actual audit
trail with simulated data. Both fixed:
- `_base_amount` gained a `record_events=False` parameter (default `True`,
  so live behavior is unchanged) - `core/backtest_engine.py` passes
  `False`, so a backtest never writes real DB rows regardless of what a
  simulated apex_ascension tier crossing does.
- `simulate_strategy`'s loop now applies every layer
  `compute_position_size`/`on_trade_closed` apply live, reusing the same
  pure functions, with Empire's ladder level and Fortress's protected
  principal correctly tracked as PROFILE-scoped state (persists across
  simulated sessions, matching how `empire_settings`/`fortress_settings`
  work live) rather than incorrectly reset every session like Martingale/
  Momentum's session-scoped steps are. A `profile_snapshot` saved before
  these features existed (missing the new sub-config keys) still
  simulates exactly as before - treated as not-enabled, not a crash.
- 10 new tests in `tests/test_backtest_engine.py`
  (`CapitalStrategiesSimulationTests` + a DB-backed purity test in
  `RunBacktestIntegrationTests`), including one that specifically asserts
  zero rows land in `capital_tier_events` after a backtest that crosses
  an Apex Ascension tier.

## Next up

Remaining: Leviathan and Oracle (blocked on a real product design
decision, not effort or a missing data source - see "Deliberately not
started" above). Also still open: Phoenix/Momentum/Fortress's full
re-wiring into the quick single-strategy demo simulator (needs the
Strategy Lab's richer multi-mode-aware simulation, not that
single-strategy helper - historical replay through the real Backtest
Engine already works for them today, see above), Strategy Lab Monte
Carlo simulation, sportsbook support.

Note: `web/capital_strategies.html` (the standalone Investment House
browsing page referenced earlier in this doc) was deliberately removed
by a later session - Money Management Studio's narrower official-plans
model became the real product surface, and a competing "browse all ~20
strategies" page was judged to contradict it. The catalog module
(`core/capital_strategies_catalog.py`) and its read-only API
(`api/capital_strategies_routes.py`) still exist and stay accurate, but
new strategy work (like Blackwater/Sniper above) integrates into
`web/risk.html`'s Money Management Studio, not a revived browse page.

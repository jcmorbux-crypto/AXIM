# AXIM Capital Strategies (tm)

**Status: Phase 1 complete (2026-07-11).** Confirmed product direction -
see `memory/project_axim_capital_strategies.md` for how this was
confirmed (arrived via an unusual channel first, verified directly with
the user before any implementation started, per this project's standing
practice for consequential instructions).

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

## What's real vs. catalog-only right now

The catalog UI shows all 17 strategies with full philosophy/tagline/risk
content regardless of phase (the spec requires the catalog to be
complete even before every calculation exists). The "Run Simulation"
button only appears for strategies with a real quick-simulate wiring
today: **Foundation, Titan Allocation, Apex Ascension**. Everything else
marked `implemented: true` in the catalog (Sentinel, Cashflow, Strike,
Dominion, QuantEdge, Axiom Vault, Phoenix) has real, live calculations
running through the existing Risk Engine / Funds pages already - just
not (yet) through this page's simplified single-path demo simulator,
which the UI states plainly rather than showing a button that would 400.
Momentum, Empire, Leviathan, Blackwater, Sniper, Fortress, and Oracle are
genuinely not built yet (Phase 2/3) and the UI says so.

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

## Next up (Phase 2 / Phase 3, per the confirmed build priority)

Phase 2: Momentum, Empire, Leviathan, Blackwater, Sniper, Fortress, Axiom
Vault's new trigger types. Phase 3: QuantEdge/Oracle/Phoenix's full
re-wiring into the quick simulator, the Strategy Builder, Strategy Lab
Monte Carlo/historical backtesting, sportsbook support.

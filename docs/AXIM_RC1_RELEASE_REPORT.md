# AXIM Core — Release Candidate 1 Report

**Date:** 2026-07-11
**Status:** Installable, demo-ready. Not live-trading-ready (by design — see "Live trading preparation").

## Deliverables

All three in `dist/`:

- `AXIM-Core-Server-v1.0.0-20260711-142743.zip` — the server, for the machine that runs the Telegram listener and Pocket Option browser session.
- `AXIM Trader_0.1.0_x64-setup.exe` — the Remote Client installer (NSIS), for a second device (laptop) to control the server over Tailscale.
- `AXIM Trader_0.1.0_x64_en-US.msi` — the same Remote Client, MSI format, for environments that prefer/require MSI.

Both freshly built from current source and smoke-tested this cycle (see "Test summary").

## Features completed

**Core trading pipeline:** Telegram signal parsing → risk-rule pipeline → Pocket Option execution (real, visible browser) → outcome tracking → recorded history. Fail-closed throughout (missing data rejects, never silently allows).

**Multi-Fund architecture:** independent Funds, each with its own broker account, risk profile, and channels; concurrent trading sessions across Funds; per-Fund broker-account connection lifecycle.

**AXIM Capital Strategies™:** full 17-strategy / 4-Investment-House catalog, with 9 real calculation engines wired into live sizing (Foundation, Titan Allocation, Apex Ascension, Cashflow, Strike, Sentinel, Dominion, Momentum, Fortress, Empire, plus Phoenix/Axiom Vault/QuantEdge as relabels of existing engines). Quick demo simulator covers 5 of these directly. Also wired into the historical Backtest Engine, not just live sizing. Leviathan, Blackwater, Sniper, Oracle, Strategy Builder, and Sportsbook Support are explicitly deferred to the commercial roadmap — catalog entries exist, calculations do not, and this is stated plainly in the UI rather than faked.

**Guided onboarding:** 8-step Setup Wizard (owner account → Telegram link → Pocket Option connect → risk profile → channels → Fund/session → one real demo test trade → ready).

**Safety systems, independently verified:**
- Three-gate live-trading lock: global `ARMED`, global `ACCOUNT=DEMO`, per-broker-account `live_enabled` — all three must agree before a live click can happen.
- Drawdown circuit breaker, max-trade-amount, max-trades-per-hour, consecutive-loss cooldown — all fail closed.
- Emergency Stop — verified live this cycle: flips global control state, stops every active session app-wide, not just one.
- Fund-aware real-money bankroll: Percent/Kelly/Dynamic/Apex Ascension sizing now reads a Fund's actual accumulated P&L instead of a stale manually-set number, without risk of one Fund's results bleeding into another Fund sharing the same risk profile.
- Listener process supervision: a real incident this cycle (the soak-test listener died with no supervisor watching it) led to fixing and *directly verifying* the Scheduled-Task auto-restart path, not just assuming it works.
- Cross-Fund rule isolation: found and fixed a real gap where the Automation Studio's rule editor could leave a rule's `session_id` pointing at a different Fund's session than its own `fund_id` — Stop Session/Emergency Stop/resize-risk/vault-move actions now correctly refuse to act on a mismatched Fund's session (fixed both at the API validation layer and with an independent re-check at evaluation time).

**AXIM Trader (Remote Client):** local mode (spawns the server processes itself) and remote mode (connects to a server elsewhere over Tailscale, spawns nothing locally). This cycle added a reachability probe before navigating — a bad/unreachable remote address now shows a recoverable in-app error instead of dropping into WebView2's native browser error page. Verified end-to-end in a real browser against the actual served files, plus a direct run of the rebuilt installer binary.

## Test summary

**655 passed, 0 failed** (`python -m pytest tests/ -q`), run twice this cycle — once in the dev checkout, once from a completely fresh extraction of the shipped zip with its own venv, both clean. (One test that's usually "intentionally skipped" ran instead in the fresh-package run, because it depends on a `.env` value that differs between `.env` vs `.env.example` — expected, documented behavior, not a bug.)

Beyond unit tests, every core workflow this cycle was **live-verified against real production code** via isolated throwaway server instances (never the running soak-test listener): Telegram channel search/add, Pocket Option account connect/disconnect lifecycle (API layer — the login itself is an unavoidable manual step, see below), per-Fund broker-account assignment, risk-profile selection/save with real validation, session start/profit-target-stop/loss-limit-stop, trade logging, and Emergency Stop.

## Known limitations (do not block RC1)

- **AXIM Trader local mode** requires an existing AXIM project checkout with its own `venv/` on that machine — not a bundled, self-contained installer. Remote mode has no such requirement. Documented in `TROUBLESHOOTING.md`.
- **Pocket Option login is manual.** No supported programmatic login exists; a human completes it once per broker account in a real browser window. This is a permanent characteristic of the broker, not a gap to close.
- **`same_asset_only`/`same_source_only` Martingale fields** are stored and shown in the UI but not enforced — genuinely underspecified (would need a product decision on exact reset semantics), deliberately not guessed at.
- **Billing/Stripe** is a complete scaffold with zero live calls until real API keys are supplied — intentional, not accidental.

## Outstanding roadmap items (explicitly deferred, not started)

Sniper, Leviathan, Blackwater, Oracle, Strategy Builder, Sportsbook Support — all need either a product/design decision this session correctly declined to guess at (e.g., Leviathan's "Pay Opportunity" trigger is undefined in the spec), or new data infrastructure that doesn't exist yet (e.g., Sniper's payout filter needs cross-module work spanning `trade_coordinator.py` and `pocket_executor.py`). Full reasoning per item in `docs/AXIM_CAPITAL_STRATEGIES.md`.

A full visual brand identity redesign (app icon, installer icon, splash/loading screens, empty-state illustrations) was requested during this cycle and explicitly deferred by the user to start after RC1 ships.

## Installation instructions

See `INSTALL.md` (or `QUICK_START.md` for the condensed version) in the root of the unzipped server package.

## First-time setup

See `FIRST_TRADE.md` — the guided Setup Wizard walkthrough, ending with one confirmed real demo trade.

## Live trading preparation

**Not ready yet, by design.** See `LIVE_CHECKLIST.md` for the summary or `docs/AXIM_LIVE_READINESS_CHECKLIST.md` for full evidence. Two things remain, both requiring the operator personally:

1. Inspect your real live Pocket Option cabinet and set `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` — no safe default exists, and nothing in this project's history has ever seen a real live cabinet.
2. Run a fresh observation window with real, non-relaxed risk thresholds to get an honest win-rate-net-of-payout read. **This is already in progress** as of this report: real thresholds (`minimum_payout=90`, `max_trades_per_hour=10`, `max_consecutive_losses=3`, `cooldown_after_loss_seconds=60`) were switched on live at `2026-07-11T11:45:54` (boundary: `signals_total=499` in `logs/soak_test_log.csv`). Signal volume on the current watched channel set has been slow — check the log against that boundary before drawing any conclusion; a handful of trades isn't enough either way.

## Recommended next milestone

1. Let the real-thresholds observation window keep accumulating real signal volume; read the resulting win rate once there's a meaningful sample.
2. Operator inspects the real live Pocket Option cabinet (the one step nobody else can do) — whenever ready, not blocking anything else.
3. Visual brand identity redesign (icon, installer branding, splash/loading, empty states) — explicitly deferred to start now that RC1 has shipped.
4. When ready to resume feature work: Leviathan/Blackwater/Sniper/Oracle each need a short design pass first (see "Outstanding roadmap items"), not more engineering time on the current spec.

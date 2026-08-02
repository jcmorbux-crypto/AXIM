# AXIM Capability Matrix

**A living inventory of what AXIM can actually do today**, module by
module, cross-checked against real code and this project's own prior
verification reports rather than aspirational descriptions. Every row
below cites the file/line or doc backing its status - if a claim can't
be traced to something real, it isn't in this document.

**Companion documents:** `docs/AXIM_LIVE_PRODUCTION_GRADUATION.md` is
the detailed Phase 1-5 plan for closing every Live-blocking item listed
here (audit detail, blocker type, effort, exit criteria).
`docs/AXIM_LIVE_VALIDATION_PACKAGE.md` is the executable operational
package built on top of it: evidence-backed proof every Live safety
feature works (361 passing tests cited directly), the operator
runbook, the exact one-session graduation script, the Go/No-Go gate,
and failure-recovery/rollback procedures. This matrix stays the single
source of truth for module status.

**Last compiled:** 2026-08-01 (Phase 2 graduation pass - re-tagged
every row to the graduation taxonomy below). Originally compiled
2026-08-01 by direct code inspection (four parallel research passes
over core/, api/, web/, docs/) plus that session's own verified work on
Blackwater/Sniper. Update this doc whenever a module's status
materially changes - it's meant to stay current, not be a one-time
snapshot.

## Status legend

Per the AXIM Phase 2 — Live Production Graduation mandate, every
capability is tagged with one of these 5 states:

| Status | Meaning |
|---|---|
| **Production Ready** | Built, wired end-to-end, tested, and verified working under real usage (Demo-money or non-trading real usage) - no known defect blocks normal operation, and no Live-specific gate applies beyond what's already handled. |
| **Configuration Required** | Engineering is done; what's missing is a value only the account owner can supply (e.g. a real `LIVE_URL`), not more code. |
| **Product Decision Required** | Blocked on a decision only the product owner can make - a genuinely new strategy definition (Leviathan, Oracle), or accepting/rejecting a measured result (e.g. a win-rate figure) as economically viable. |
| **Live Validation Required** | Engineering is done (or is Production Ready in Demo); what's left is proof under real Live conditions, per `AXIM_LIVE_PRODUCTION_GRADUATION.md`'s Phase 3 checklist. |
| **Live Production Approved** | A specific broker account has passed every Phase 4 graduation criterion and been explicitly, freshly approved for Live trading. **No account currently holds this status.** |

One deliberate addition beyond the 5 required states: **Future Feature
(Not Live-Blocking)** — used only for the handful of items that are
genuinely just unbuilt backlog (Monte Carlo simulation, email/SMS/push
notifications) with no bearing on Live graduation at all. Forcing these
into "Product Decision Required" or any of the other 4 would be
inaccurate - nobody is waiting on a decision, they're simply not
scheduled. Kept to a minimum, called out explicitly rather than
silently mixed into the 5-state system.

## Bottom line (the honest one-paragraph answer)

AXIM's Demo-account trading engine, money-management/capital-strategies
system, Fund/Provider management, backtesting, analytics, automation,
and operational tooling are all real, tested, and in day-to-day use
(session #12 has been running continuously since 2026-07-17, 363+
trades). **Live (real-money) trading is deliberately not enabled.**
AXIM is now in an active, structured graduation process
(`AXIM_LIVE_PRODUCTION_GRADUATION.md`) rather than open-ended waiting:
Phase 1's audit is complete, Phase 2's engineering blockers are being
closed one at a time, and Phase 3's Live Validation checklist is
designed and ready for the moment a real Live account is available.
**No broker account is Live Production Approved today.**

---

## 1. Trading Engine Core (execution, risk, sessions, recovery)

| Capability | Status | Evidence |
|---|---|---|
| Signal ingestion (Telegram) → parse → execute pipeline | **Live Validation Required** | `execution/pocket_executor.py:27` gates every click on `ARMED`; `core/risk_manager.py:100` independently hard-fails unless `ACCOUNT=DEMO`. 499 real Demo signals soak-tested per `docs/AXIM_LIVE_READINESS_CHECKLIST.md`. See Graduation Plan Phase 3, checks #1-#5. |
| Risk management (emergency stop, max daily loss, consecutive losses, cooldown, duplicate detection) | **Production Ready** | All enforced in `core/risk_manager.py` (`check_demo_only`, `emergency_stop`, `check_max_consecutive_losses`, `check_duplicate_signal`, `MAX_DAILY_LOSS` since RC1); wired into `core/trade_coordinator.py`. |
| Live-mode safety gates (mandatory, reason-required confirmation before an account is Live-effective) | **Production Ready** | `database.confirm_live_arm` + `POST /{account_id}/confirm-live-arm` - `account_effective_cabinet_mode` now requires this in addition to `live_enabled`. Graduation Plan Phase 1 item #5, commit `cd0a88d`. |
| Live account verification (is this cabinet genuinely Live, not Demo?) | **Production Ready** | `execution/account_mode_verification.py`'s `verify_live_mode()` - fails closed across 7 independent conditions, verified against a real live cabinet's DOM (2026-07-31), 58 passing tests. Graduation Plan Phase 1 item #3 - corrected 2026-08-01 from an earlier "not built" finding. |
| Multi-broker-account architecture (N concurrent Pocket Option logins) | **Live Validation Required** | Code supports independent `BrowserWarmupService`/`BrowserWorkerPool` per account (`core/broker_account_manager.py:41-115`), but `docs/AXIM_V1_FINAL_ACCEPTANCE_REPORT.md:122-126` confirms only **1** concurrent live browser session has ever actually been demonstrated. Graduation Plan Phase 1 item #4. |
| Session management (loss limit / max trades / duration) | **Production Ready** | `core/session_manager.py:121` enforces pessimistically against pending stake, not just realized P&L. |
| Trade reconciliation (broker-history vs. AXIM state) | **Production Ready** | `core/recovery.py:30-148` - verified mode-agnostic by construction (zero demo/live branching anywhere in the reconciliation path, `pocket_dom.py`, or `pocket_executor.py`); lifecycle-based recovery, fail-closed broker matching, built from a real 2026-07-29 production incident (series 105). Graduation Plan Phase 1 item #6. |
| Live statistics / execution telemetry isolation (Live never blended with Demo in any aggregate) | **Production Ready** | `database.get_trades_between(exclude_live=...)`, threaded through every previously-unscoped `core/trade_statistics.py` aggregate; 6 tests confirm isolation. Graduation Plan Phase 1 item #7, commit `71f6246`. |
| Crash/process-level recovery (`run_forever`) | **Live Validation Required** (in-process recovery is Production Ready; full reboot untested) | `core/telegram_listener.py:980` handles browser/Telegram reconnect, live-fire verified. A real incident found the OS-level Scheduled Task hadn't fired for days (`AXIM_LIVE_READINESS_CHECKLIST.md`). Graduation Plan Phase 1 item #9. |
| `LIVE_URL` / live cabinet configuration | **Configuration Required** | Unset - raises `LiveModeNotConfiguredError`. Only the operator can supply a real live cabinet URL, after personally inspecting it. Graduation Plan Phase 1 item #2. |
| Honest win-rate under real (non-relaxed) risk thresholds | **Product Decision Required** | Last measured win rate (37%) was taken under relaxed thresholds (`AXIM_LIVE_READINESS_CHECKLIST.md`) - not representative. A fresh observation window's result must be reviewed and explicitly accepted by the operator. Graduation Plan Phase 1 item #8. |
| **Live (real-money) trading, overall** | **Configuration Required, pending Live Validation Required items** | `docs/AXIM_LIVE_READINESS_REVIEW.md`: "Bottom line up front: not ready." Full dependency chain in `AXIM_LIVE_PRODUCTION_GRADUATION.md` Phase 4. |

## 2. Money Management / Capital Strategies (tm)

| Strategy / Capability | Status | Evidence |
|---|---|---|
| Foundation, Titan Allocation, QuantEdge (Kelly), Cashflow, Strike, Apex Ascension, Sentinel, Momentum, Fortress, Empire, Dominion (multi-Fund), Axiom Vault, Phoenix (Martingale) | **Production Ready** | Real calculations in `core/capital_strategies.py`/`core/risk_engine.py`, live-wired sizing/modifiers, full config UI in `web/risk.html`, `implemented: true` in `core/capital_strategies_catalog.py`. |
| **Blackwater** (tiered stake allocation by provider quality) | **Production Ready** | Built 2026-08-01. Driven entirely by `core/provider_scorecard.py`'s real stats - never a fabricated score. Full settings persistence, config UI, walk-forward backtesting, sizing wired into `core/risk_engine.py`. |
| **Sniper** (hard execution gate on provider quality) | **Production Ready** | Same date. `core/risk_manager.check_sniper_qualification`, real preflight gate in `core/trade_coordinator.py`, qualification reasons surfaced on Trade Detail. |
| **Leviathan** (multi-phase "Pay Opportunity" state machine) | **Product Decision Required** | `definition_required: True` in the catalog - visible, not activatable. Needs a real product definition of what a "Pay Opportunity" is; no placeholder logic exists. Intentionally out of scope for Live graduation. |
| **Oracle** (adaptive confidence-driven allocation) | **Product Decision Required** | Same gate. Its own spec concept ("0-100 AXIM Confidence Score") is the exact fabricated-scoring pattern the 2026-08-01 product decision forbids - needs a real deterministic-formula redefinition first. Intentionally out of scope for Live graduation. |
| Export/Import a custom strategy (portable JSON) | **Production Ready** | Backend existed, UI added 2026-08-01; a real bug where import silently dropped 10 of 13 config sections was found and fixed same session. |
| Money Management Studio's 5 official canonical plans | **Production Ready** | `core/money_studio.py` - zero-DB-footprint virtual profiles, real engine mapping, locked/non-editable by design. |

## 3. Fund Management, Signal Providers, Graduation

| Capability | Status | Evidence |
|---|---|---|
| Multi-Fund independent bankrolls (trading + vaulted balance) | **Production Ready** | `core/fund_manager.py:29-48`; no stub/placeholder markers found. |
| Fund ↔ broker-account attachment, solvency-checked capital transfer | **Production Ready** | `fund_manager.py:225-282, 348-405`. |
| Fund ↔ Signal Provider assignment, enforced at session-start | **Production Ready** | `api/funds_routes.py:188-201`; enforcement fixed in a prior session (commit `1de5891`). |
| Fund pause/resume/archive/duplicate | **Production Ready** | `funds_routes.py:143-185`, dangling-session cleanup on archive. |
| Provider trading-mode gate (observation → demo_ready → demo → live) | **Production Ready** | `core/provider_profile.py` - one-way state machine, every source starts in `observation` (signals recorded, never executed), each promotion needs an explicit human `approved_by`. |
| Provider graduation criteria (sample size, parse success rate, coverage, no active drift) | **Production Ready, genuinely earned** | `provider_profile.py:64-93,143-161` - raises rather than silently passing on unmet criteria; no `force`/`override`/`bypass` path exists anywhere in the module. |
| Format-drift auto-detection (auto-reverts a live/demo source) | **Production Ready** | `provider_profile.py:104-140`. |
| Provider Scorecard (real per-provider stats: win rate, profit factor, EV, drawdown, streak, signal age, rejection rate) | **Production Ready** | `core/provider_scorecard.py`, added 2026-08-01, the sole permitted input for Blackwater/Sniper. |
| Signal-level metadata (confidence, volatility, signal age at receipt) | **Product Decision Required** | `parsers/signal_parser.py` only populates `asset`/`direction`/`expiry`/`entry_time`/`raw_message`. Blocks Leviathan/Oracle specifically, not Live graduation - what metadata to add (and from where) is a product question, not an engineering one. |

## 4. Strategy Lab / Backtesting

| Capability | Status | Evidence |
|---|---|---|
| CSV/Excel historical signal import | **Production Ready** | `api/backtest_routes.py`. |
| Multi-strategy backtest run, ranking, comparison | **Production Ready** | Reuses the exact live sizing functions (`core/backtest_engine.py`'s own docstring) - not a separate toy simulator. |
| Run versioning / duplication | **Production Ready** | Prior-session gap closed (commit `3102166`). |
| Deploy-to-Fund (backtested strategy → real risk profile) | **Production Ready** | `database.create_risk_profile_from_snapshot`, `api/backtest_routes.py`. |
| AI narrative summary of a completed run | **Production Ready** | `core/ai_analysis.py` over real, already-computed metrics - not mocked text. |
| Blackwater/Sniper walk-forward backtesting (no lookahead bias) | **Production Ready** | Built 2026-08-01; `sniper_rejected_count` now flows through to the saved metrics and Strategy Lab UI (found as a real gap and fixed same session). |
| Monte Carlo simulation | **Future Feature (Not Live-Blocking)** | `docs/AXIM_CAPITAL_STRATEGIES.md` "Next up" - explicitly not started; today's demo simulator runs one deterministic path only. |

## 5. Analytics

| Capability | Status | Evidence |
|---|---|---|
| Cross-provider / cross-Fund performance cohorts | **Production Ready** | `/api/channels/performance-summary`, `/api/funds/performance-summary` reuse existing, already-tested calculation functions - no fabricated numbers. |
| Low-sample-size disclosure | **Production Ready** | Providers with &lt;10 trades are flagged, not hidden or silently averaged in (`web/analytics.html`). |
| Deep-links from Analytics into filtered Trade History | **Production Ready** | Built in a prior session (commits `ea9b01a`, `4880fa6`, `4d53af4`, `c278f35`, `c246d8c` - a full provider → session → trade linking chain). |
| Sniper/Blackwater qualification-reason visibility on Trade Detail | **Production Ready** | Built 2026-08-01 (`database.get_signal_detail`'s `rejection_reason` field). |

## 6. Automation Studio

| Capability | Status | Evidence |
|---|---|---|
| Rule engine (11 condition types × 9 action types) | **Production Ready** | `core/rule_engine.py`'s own docstring: "every action executor calls an existing real mutation function... never invents a new mutation path." No UI-only stubs found in the registry. |

## 7. Notifications

| Capability | Status | Evidence |
|---|---|---|
| In-app notification bell, unread count, bulk mark-all-read | **Production Ready** | `api/notifications.py`. |
| Per-notification mark-as-read | **Production Ready** | Built 2026-08-01 - backend always existed, UI wasn't wired to it until now. |
| Automatic owner alerts on real system failures (series blocked/errored, reconciliation-required, unhandled coordinator crash) | **Production Ready** | Commit `4e4bcf9`, 2026-08-01, 8 new tests. |
| Email / SMS / push notifications | **Future Feature (Not Live-Blocking)** | `api/notifications.py`'s own docstring: "In-app only... those would need an external provider and credentials, out of scope here." `web/settings.html`'s Notifications tab states plainly: "Not built yet." |

## 8. Dashboard

| Capability | Status | Evidence |
|---|---|---|
| Live homepage (status, funds, growth curve, provider performance, active sessions) | **Production Ready** | Pulls from ~15 real, live endpoints (`web/dashboard.html`); no placeholder markers found. |

## 9. Settings, Auth & Security

| Capability | Status | Evidence |
|---|---|---|
| Authentication (owner/admin/user roles, hashed passwords, session management) | **Production Ready** | `api/auth_routes.py`, PBKDF2-SHA256 600k iterations (`core/auth.py:14`). |
| Security remediation history | **Production Ready (fixed, verified live)** | Change-password session hijack, brute-force bypass + owner-creation race, stored-XSS + login lockout, privilege-escalation in `api/admin.py`, missing security headers - all closed and verified against a live server (commits `cdd5d9a`/`5ea75b7`/`cdc143c`/`4fa34b0`/`56add70`/`b672a6c`). |
| Settings UI (General/Security/Trading/Telegram/Notifications/Backups/Developer) | **Production Ready** | Verified live across all 7 tabs (`AXIM_V1_FINAL_ACCEPTANCE_REPORT.md`). |
| Remote access (Tailscale + Remote Client) | **Production Ready** | Full guide in `docs/AXIM_REMOTE_ACCESS.md`; installer smoke-tested per `AXIM_RC1_RELEASE_REPORT.md`. |
| Windows Scheduled Task process supervision | **Production Ready, opt-in** | `DEPLOYMENT.md` - "AXIM is not registered to start automatically by default." A real bug (API left down after force-termination) was found and fixed via live testing, not assumed. |
| Full observability / structured logging | **Production Ready, measured** | `docs/AXIM_OBSERVABILITY.md`: "Built and verified with real trades... every number... comes from an actual live-demo-account benchmark run." |

---

## Live Trading Readiness (cross-cutting, the question that matters most)

Every module above is scoped to AXIM's **Demo** Pocket Option cabinet.
Going to **Live** (real money) is governed by
`docs/AXIM_LIVE_PRODUCTION_GRADUATION.md` - a formal, phased plan, not
open-ended waiting. Current state:

- `ACCOUNT=DEMO` and `ARMED=false` remain the defaults; nothing in this
  codebase flips them automatically.
- `LIVE_URL` is unset (**Configuration Required** - operator-only).
- **Graduation Plan Phase 2 (all 4 engineering items) is complete**:
  live account verification and live reconciliation were audited and
  found already correct; live statistics isolation and a mandatory
  reason-required live-arm confirmation gate were built and tested.
  What remains for Live is entirely configuration/external-dependency/
  operator-time (items #2, #4, #8, #9), not engineering.
- The formal Live Validation checklist (13 checks: login, account
  detection, execution, expiry, stake sizing, reconciliation, bankroll
  updates, interruption recovery, stop-loss, stop-win, no duplicates,
  no missed trades, no race conditions) is designed and ready
  (Graduation Plan Phase 3), pending a real Live account.
- Objective Go/No-Go graduation criteria are defined (Graduation Plan
  Phase 4) - graduation is evaluated **per broker account**, and
  **no account currently holds Live Production Approved status.**

**Do not flip any Live-enabling flag without a fresh, explicit,
in-the-moment instruction from the user** - a past mandate on this
project already establishes that describing the future Live-enable
steps (including the Graduation Plan itself) does not itself authorize
taking them.

---

## Keeping this document current

This matrix reflects the state of the codebase as of 2026-08-01. When a
module's status changes (a new strategy ships, Live validation
progresses, a capability gets fixed), update the relevant row and this
"Last compiled" date rather than letting the document drift stale -
the whole point is that it stays trustworthy enough to answer "what can
AXIM actually do" without re-deriving it from scratch every time.

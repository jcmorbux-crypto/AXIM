# AXIM Capability Matrix

**A living inventory of what AXIM can actually do today**, module by
module, cross-checked against real code and this project's own prior
verification reports rather than aspirational descriptions. Every row
below cites the file/line or doc backing its status - if a claim can't
be traced to something real, it isn't in this document.

**Last compiled:** 2026-08-01, by direct code inspection (four parallel
research passes over core/, api/, web/, docs/) plus this session's own
verified work on Blackwater/Sniper. Update this doc whenever a module's
status materially changes - it's meant to stay current, not be a
one-time snapshot.

## Status legend

| Status | Meaning |
|---|---|
| **Production-ready** | Built, wired end-to-end, tested, and verified working under real usage (demo-money or non-trading real usage) - no known defect blocks normal operation. |
| **Complete** | Fully built and wired with no known functional gaps, but not yet independently load/scale-verified the way "Production-ready" items have been. |
| **Experimental** | Real code exists and runs, but is unproven at the scale/condition the product actually needs (e.g. only 1 of N supported concurrent accounts has ever been demonstrated). |
| **Requires live validation** | Works correctly against Pocket Option's Demo cabinet; going to a real-money Live cabinet requires additional, explicitly-gated validation before use. |
| **Disabled** | Exists in the codebase but is intentionally inert - a hard gate (env var unset, `definition_required`, etc.) prevents it from running. |
| **Planned** | Not built. Explicitly named as future scope in project docs, not started. |

## Bottom line (the honest one-paragraph answer)

AXIM's Demo-account trading engine, money-management/capital-strategies
system, Fund/Provider management, backtesting, analytics, automation,
and operational tooling are all real, tested, and in day-to-day use
(session #12 has been running continuously since 2026-07-17, 363+
trades). **Live (real-money) trading is deliberately not enabled**:
`ACCOUNT=DEMO`/`ARMED=false` gate every execution path, `LIVE_URL` is
unset (raises `LiveModeNotConfiguredError` if anything tries), and the
project's own RC1 release report states it plainly - "Installable,
demo-ready. Not live-trading-ready (by design)." Two operator-only,
non-code items remain before Live can even be considered: inspecting a
real live Pocket Option cabinet to configure `LIVE_URL`, and completing
an honest-win-rate observation window under real (not relaxed) risk
thresholds. See "Live Trading Readiness" below for specifics.

---

## 1. Trading Engine Core (execution, risk, sessions, recovery)

| Capability | Status | Evidence |
|---|---|---|
| Signal ingestion (Telegram) → parse → execute pipeline | **Requires live validation** | `execution/pocket_executor.py:27` gates every click on `ARMED`; `core/risk_manager.py:100` independently hard-fails unless `ACCOUNT=DEMO`. 499 real Demo signals soak-tested per `docs/AXIM_LIVE_READINESS_CHECKLIST.md`. |
| Risk management (emergency stop, max daily loss, consecutive losses, cooldown, duplicate detection) | **Production-ready** | All enforced in `core/risk_manager.py` (`check_demo_only`, `emergency_stop`, `check_max_consecutive_losses`, `check_duplicate_signal`, `MAX_DAILY_LOSS` since RC1); wired into `core/trade_coordinator.py`. |
| Multi-broker-account architecture (N concurrent Pocket Option logins) | **Experimental** | Code supports independent `BrowserWarmupService`/`BrowserWorkerPool` per account (`core/broker_account_manager.py:41-115`), but `docs/AXIM_V1_FINAL_ACCEPTANCE_REPORT.md:122-126` confirms only **1** concurrent live browser session has ever actually been demonstrated. |
| Session management (loss limit / max trades / duration) | **Production-ready** | `core/session_manager.py:121` enforces pessimistically against pending stake, not just realized P&L. |
| Trade reconciliation (broker-history vs. AXIM state) | **Production-ready** | `core/recovery.py:30-148` - lifecycle-based recovery, fail-closed broker matching, built from a real 2026-07-29 production incident (series 105). |
| Crash/process-level recovery (`run_forever`) | **Production-ready (in-process)**, reboot untested | `core/telegram_listener.py:980` handles browser/Telegram reconnect live-fire verified. A real incident found the OS-level Scheduled Task hadn't fired for days (`AXIM_LIVE_READINESS_CHECKLIST.md`) - "a full physical reboot/logon remains the only literally-untested variant." |
| **Live (real-money) trading, overall** | **Disabled, requires live validation to lift** | `docs/AXIM_LIVE_READINESS_REVIEW.md`: "Bottom line up front: not ready." `docs/AXIM_PRODUCTION_READINESS_REPORT.md`: confidence 72/100, "NOT yet recommended for unattended, high-stake, or high-volume live trading." |

## 2. Money Management / Capital Strategies (tm)

| Strategy / Capability | Status | Evidence |
|---|---|---|
| Foundation, Titan Allocation, QuantEdge (Kelly), Cashflow, Strike, Apex Ascension, Sentinel, Momentum, Fortress, Empire, Dominion (multi-Fund), Axiom Vault, Phoenix (Martingale) | **Production-ready** | Real calculations in `core/capital_strategies.py`/`core/risk_engine.py`, live-wired sizing/modifiers, full config UI in `web/risk.html`, `implemented: true` in `core/capital_strategies_catalog.py`. |
| **Blackwater** (tiered stake allocation by provider quality) | **Production-ready** | Built 2026-08-01 this session. Driven entirely by `core/provider_scorecard.py`'s real stats - never a fabricated score. Full settings persistence, config UI, walk-forward backtesting, sizing wired into `core/risk_engine.py`. |
| **Sniper** (hard execution gate on provider quality) | **Production-ready** | Same session/date. `core/risk_manager.check_sniper_qualification`, real preflight gate in `core/trade_coordinator.py`, qualification reasons surfaced on Trade Detail. |
| **Leviathan** (multi-phase "Pay Opportunity" state machine) | **Disabled / Planned** | `definition_required: True` in the catalog - visible, not activatable. Needs a real product definition of what a "Pay Opportunity" is; no placeholder logic exists. |
| **Oracle** (adaptive confidence-driven allocation) | **Disabled / Planned** | Same gate. Its own spec concept ("0-100 AXIM Confidence Score") is the exact fabricated-scoring pattern the 2026-08-01 product decision forbids - needs a real deterministic-formula redefinition first. |
| Export/Import a custom strategy (portable JSON) | **Production-ready** | Backend existed, UI added 2026-08-01; a real bug where import silently dropped 10 of 13 config sections was found and fixed same session. |
| Money Management Studio's 5 official canonical plans | **Production-ready** | `core/money_studio.py` - zero-DB-footprint virtual profiles, real engine mapping, locked/non-editable by design. |

## 3. Fund Management, Signal Providers, Graduation

| Capability | Status | Evidence |
|---|---|---|
| Multi-Fund independent bankrolls (trading + vaulted balance) | **Production-ready** | `core/fund_manager.py:29-48`; no stub/placeholder markers found. |
| Fund ↔ broker-account attachment, solvency-checked capital transfer | **Production-ready** | `fund_manager.py:225-282, 348-405`. |
| Fund ↔ Signal Provider assignment, enforced at session-start | **Production-ready** | `api/funds_routes.py:188-201`; enforcement fixed in a prior session (commit `1de5891`). |
| Fund pause/resume/archive/duplicate | **Production-ready** | `funds_routes.py:143-185`, dangling-session cleanup on archive. |
| Provider trading-mode gate (observation → demo_ready → demo → live) | **Production-ready** | `core/provider_profile.py` - one-way state machine, every source starts in `observation` (signals recorded, never executed), each promotion needs an explicit human `approved_by`. |
| Provider graduation criteria (sample size, parse success rate, coverage, no active drift) | **Production-ready, genuinely earned** | `provider_profile.py:64-93,143-161` - raises rather than silently passing on unmet criteria; no `force`/`override`/`bypass` path exists anywhere in the module. |
| Format-drift auto-detection (auto-reverts a live/demo source) | **Production-ready** | `provider_profile.py:104-140`. |
| Provider Scorecard (real per-provider stats: win rate, profit factor, EV, drawdown, streak, signal age, rejection rate) | **Production-ready** | `core/provider_scorecard.py`, added 2026-08-01, the sole permitted input for Blackwater/Sniper. |
| Signal-level metadata (confidence, volatility, signal age at receipt) | **Not captured - blocks future work** | `parsers/signal_parser.py` only populates `asset`/`direction`/`expiry`/`entry_time`/`raw_message`. Any future strategy wanting to condition on signal confidence/volatility has no data source today. |

## 4. Strategy Lab / Backtesting

| Capability | Status | Evidence |
|---|---|---|
| CSV/Excel historical signal import | **Production-ready** | `api/backtest_routes.py`. |
| Multi-strategy backtest run, ranking, comparison | **Production-ready** | Reuses the exact live sizing functions (`core/backtest_engine.py`'s own docstring) - not a separate toy simulator. |
| Run versioning / duplication | **Production-ready** | Prior-session gap closed (commit `3102166`). |
| Deploy-to-Fund (backtested strategy → real risk profile) | **Production-ready** | `database.create_risk_profile_from_snapshot`, `api/backtest_routes.py`. |
| AI narrative summary of a completed run | **Production-ready** | `core/ai_analysis.py` over real, already-computed metrics - not mocked text. |
| Blackwater/Sniper walk-forward backtesting (no lookahead bias) | **Production-ready** | Built 2026-08-01 this session; `sniper_rejected_count` now flows through to the saved metrics and Strategy Lab UI (found as a real gap and fixed same session). |
| Monte Carlo simulation | **Planned** | `docs/AXIM_CAPITAL_STRATEGIES.md` "Next up" - explicitly not started; today's demo simulator runs one deterministic path only. |

## 5. Analytics

| Capability | Status | Evidence |
|---|---|---|
| Cross-provider / cross-Fund performance cohorts | **Production-ready** | `/api/channels/performance-summary`, `/api/funds/performance-summary` reuse existing, already-tested calculation functions - no fabricated numbers. |
| Low-sample-size disclosure | **Production-ready** | Providers with &lt;10 trades are flagged, not hidden or silently averaged in (`web/analytics.html`). |
| Deep-links from Analytics into filtered Trade History | **Production-ready** | Built in a prior session (commits `ea9b01a`, `4880fa6`, `4d53af4`, `c278f35`, `c246d8c` - a full provider → session → trade linking chain). |
| Sniper/Blackwater qualification-reason visibility on Trade Detail | **Production-ready** | Built 2026-08-01 this session (`database.get_signal_detail`'s `rejection_reason` field). |

## 6. Automation Studio

| Capability | Status | Evidence |
|---|---|---|
| Rule engine (11 condition types × 9 action types) | **Production-ready** | `core/rule_engine.py`'s own docstring: "every action executor calls an existing real mutation function... never invents a new mutation path." No UI-only stubs found in the registry. |

## 7. Notifications

| Capability | Status | Evidence |
|---|---|---|
| In-app notification bell, unread count, bulk mark-all-read | **Production-ready** | `api/notifications.py`. |
| Per-notification mark-as-read | **Production-ready** | Built 2026-08-01 this session - backend always existed, UI wasn't wired to it until now. |
| Automatic owner alerts on real system failures (series blocked/errored, reconciliation-required, unhandled coordinator crash) | **Production-ready** | Commit `4e4bcf9`, 2026-08-01 (prior session), 8 new tests. |
| Email / SMS / push notifications | **Planned** | `api/notifications.py`'s own docstring: "In-app only... those would need an external provider and credentials, out of scope here." `web/settings.html`'s Notifications tab states plainly: "Not built yet." |

## 8. Dashboard

| Capability | Status | Evidence |
|---|---|---|
| Live homepage (status, funds, growth curve, provider performance, active sessions) | **Production-ready** | Pulls from ~15 real, live endpoints (`web/dashboard.html`); no placeholder markers found. |

## 9. Settings, Auth & Security

| Capability | Status | Evidence |
|---|---|---|
| Authentication (owner/admin/user roles, hashed passwords, session management) | **Production-ready** | `api/auth_routes.py`, PBKDF2-SHA256 600k iterations (`core/auth.py:14`). |
| Security remediation history | **Production-ready (fixed, verified live)** | Change-password session hijack, brute-force bypass + owner-creation race, stored-XSS + login lockout, privilege-escalation in `api/admin.py`, missing security headers - all closed and verified against a live server (commits `cdd5d9a`/`5ea75b7`/`cdc143c`/`4fa34b0`/`56add70`/`b672a6c`). |
| Settings UI (General/Security/Trading/Telegram/Notifications/Backups/Developer) | **Production-ready**, one sub-tab planned | Verified live across all 7 tabs (`AXIM_V1_FINAL_ACCEPTANCE_REPORT.md`). The Notifications tab's email/push toggles are explicitly "Not built yet." |
| Remote access (Tailscale + Remote Client) | **Production-ready** | Full guide in `docs/AXIM_REMOTE_ACCESS.md`; installer smoke-tested per `AXIM_RC1_RELEASE_REPORT.md`. |
| Windows Scheduled Task process supervision | **Production-ready, opt-in** | `DEPLOYMENT.md` - "AXIM is not registered to start automatically by default." A real bug (API left down after force-termination) was found and fixed via live testing, not assumed. |
| Full observability / structured logging | **Production-ready, measured** | `docs/AXIM_OBSERVABILITY.md`: "Built and verified with real trades... every number... comes from an actual live-demo-account benchmark run." |

---

## Live Trading Readiness (cross-cutting, the question that matters most)

Every module above is scoped to AXIM's **Demo** Pocket Option cabinet.
Going to **Live** (real money) is a separate, explicitly-gated decision,
not a natural consequence of any module being "production-ready" in
Demo. As of this document:

- `ACCOUNT=DEMO` and `ARMED=false` remain the defaults; nothing in this
  codebase flips them automatically.
- `LIVE_URL` is unset; any code path that would need it raises
  `LiveModeNotConfiguredError` rather than guessing.
- `docs/AXIM_RC1_RELEASE_REPORT.md`'s own verdict: **"Installable,
  demo-ready. Not live-trading-ready (by design)."**
- Two remaining items are explicitly operator-only, not further
  engineering: (1) personally inspecting a real live Pocket Option
  cabinet to set `LIVE_URL`, and (2) completing an honest win-rate
  observation window under real (not relaxed) risk thresholds - the
  most recent measured win rate (37%) was taken under relaxed
  thresholds and isn't considered representative.
- Multi-broker-account concurrency is real code but only proven at N=1
  live session - going to Live with multiple simultaneous accounts
  would need that scale actually demonstrated first.

**Do not flip any Live-enabling flag without a fresh, explicit,
in-the-moment instruction from the user** - a past mandate on this
project already establishes that describing the future Live-enable
steps does not itself authorize taking them.

---

## Keeping this document current

This matrix reflects the state of the codebase as of 2026-08-01. When a
module's status changes (a new strategy ships, Live validation
progresses, a capability gets fixed), update the relevant row and this
"Last compiled" date rather than letting the document drift stale -
the whole point is that it stays trustworthy enough to answer "what can
AXIM actually do" without re-deriving it from scratch every time.

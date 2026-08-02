# AXIM Phase 2 — Live Production Graduation Plan

**Objective:** graduate AXIM from Demo Production to Live Production
with objective evidence that it is safe, stable, and reliable. Not
feature expansion — eliminating the remaining Live blockers, one at a
time, with real validation behind every claim.

**Source of truth:** `docs/AXIM_CAPABILITY_MATRIX.md`. This plan does
not repeat that document's per-module inventory — it isolates only the
capabilities that stand between AXIM and Live, and gives each one a
concrete owner-type, effort estimate, and exit criterion.

---

## Phase 1 — Live Readiness Audit

Every capability from the Capability Matrix currently marked
Demo-only / Requires Live Validation / Disabled / Experimental, scoped
to what actually blocks Live specifically (Leviathan/Oracle are
excluded — they're a strategy-catalog gap, not a Live-trading gap, and
remain intentionally out of scope per this plan's own constraints).

| # | Capability | Current Status | Why Blocked | Blocker Type | Est. Effort | Required Validation | Exit Criteria |
|---|---|---|---|---|---|---|---|
| 1 | Live execution pipeline (signal → click) | Demo-verified only | `ARMED=false`, `ACCOUNT=DEMO` are the hard defaults; every real click is gated on both independently (`execution/pocket_executor.py`, `core/risk_manager.check_demo_only`) | Configuration + Engineering (live-specific hardening still needed, see #3/#5) | Small (config) + Medium (hardening) | Phase 3 checklist, full pass | `ARMED=true`+`ACCOUNT=LIVE` only after Phase 3 passes for that specific broker account |
| 2 | `LIVE_URL` / live cabinet configuration | Unset — raises `LiveModeNotConfiguredError` | Only the account owner can supply a real live Pocket Option cabinet URL, after personally inspecting it | External dependency (operator-only) | N/A (not engineering) | URL format + reachability + cabinet-type-is-live check (built in Phase 2) | Operator supplies a real `LIVE_URL`; AXIM validates it automatically before allowing that account to arm |
| 3 | Live account verification (is this cabinet genuinely Live, not Demo?) | Not built | No explicit check today distinguishes "a URL that happens to work" from "a URL that is genuinely the account's real-money cabinet" | Engineering | Medium | Unit tests + a real live cabinet to confirm against (later) | `verify_live_account()` exists, tested, wired into the connect flow, and is a hard gate before `live_enabled` can be set |
| 4 | Multi-broker-account concurrency at scale | Experimental — only 1 concurrent session ever demonstrated live (`AXIM_V1_FINAL_ACCEPTANCE_REPORT.md`) | Code supports N independent `BrowserWarmupService` instances; never exercised with 2+ simultaneously | External dependency (needs 2+ real accounts) + unproven-at-scale | Large | Run 2+ accounts concurrently, demo first, confirm no cross-account state bleed | 2+ concurrent sessions demonstrated stable for a real observation window |
| 5 | Live-mode safety gates | Partial — `ACCOUNT`/`ARMED` checks exist; no Live-specific tightened defaults or mandatory extra confirmation | Demo and Live currently share the same risk thresholds/circuit breakers; nothing in the code forces Live to start more conservative | Engineering | Medium | New tests proving Live defaults are strictly tighter and a live session cannot start without an explicit extra confirmation step | Live sessions default to tightened risk limits and require an explicit, logged, one-way confirmation distinct from Demo's session-start flow |
| 6 | Live reconciliation | `core/recovery.py` built and incident-tested against Demo (series-105 fix) | Never explicitly verified to be account-mode-agnostic — could have a Demo-only assumption baked in somewhere unaudited | Engineering (verification, and fixes if any gap is found) | Small–Medium | Read + targeted tests confirming no `ACCOUNT`/mode-specific branching in the reconciliation path | Reconciliation code and its tests explicitly cover a Live-mode broker account, not just Demo |
| 7 | Live statistics / execution telemetry isolation | Provider Scorecard, trade statistics, and Observability all built and measured against Demo trades only | No explicit runtime guarantee that Live and Demo trades can never be blended in an aggregate stat (win rate, provider scorecard, dashboard) — this matters the moment even one Live trade exists | Engineering | Medium | Tests proving every aggregate stat function can filter/scope by account mode and never silently mixes Demo+Live | Every statistics surface that would be shown to the operator can be explicitly scoped to Live-only, with a passing test proving Demo history never contaminates a Live-only view |
| 8 | Honest win-rate under real risk thresholds | Last measured win rate (37%) was taken under relaxed thresholds — not representative (`AXIM_LIVE_READINESS_CHECKLIST.md`) | The only rigorous read requires a real observation window run under real, non-relaxed thresholds | Product decision + operator time (not pure engineering) | N/A engineering; time-based | A fresh Demo observation window, real thresholds, statistically meaningful sample size | A documented win-rate figure from a run under real thresholds, reviewed against the strategy's own economics before Live is considered |
| 9 | Full physical reboot recovery | Only in-process recovery (`run_forever`) is live-fire verified; a real OS reboot/logon has never been tested (`AXIM_LIVE_READINESS_CHECKLIST.md`) | Untested, not unbuilt — the Scheduled Task mechanism exists (`DEPLOYMENT.md`) but a real reboot has never been observed end-to-end | Validation (does not require Live credentials — can be run against Demo) | Small | A real reboot with the Scheduled Tasks registered, confirming the listener and API both resume automatically | Documented pass: machine rebooted, both Scheduled Tasks fire, listener/API resume, no orphaned browser/session state |

**Not a Live blocker (explicitly excluded per this plan's constraints):**
Leviathan and Oracle remain `definition_required` — a strategy-catalog
gap pending a product decision on their own terms, unrelated to whether
AXIM's existing, implemented strategies are safe to run Live.

---

## Phase 2 — Eliminate Engineering Blockers

Scope: items **#3, #5, #6, #7** above — the ones that are genuinely
engineering work, not external dependencies or operator-time items.
**Live mode is not enabled by any of this work.** Each is implemented,
tested, committed, and pushed as its own milestone; this plan and the
Capability Matrix are updated after each one. See the commit history
following this plan's introduction for the actual implementation.

Item #1 (the pipeline itself) is not separately re-engineered here — it
already exists and is gated correctly; #3/#5/#6/#7 are what harden it
enough to trust with real money once #2/#4/#8/#9's non-engineering
items are also satisfied.

---

## Phase 3 — Live Validation Checklist

A formal checklist to be executed against a **real Live account with
intentionally small stakes**, only after Phase 2 is complete and the
operator has supplied `LIVE_URL` (item #2). This phase requires real
credentials and is not something this session can execute — it is
designed here so it's ready the moment the operator chooses to run it.

Each row must be independently observable (a screenshot, a DB row, a
log line) — "it seemed to work" is not a pass.

| # | Check | What "pass" means | How it's verified |
|---|---|---|---|
| 1 | Correct login | AXIM logs into the real Live cabinet, not Demo, using the operator-supplied `LIVE_URL` | `verify_live_account()` (Phase 2) confirms cabinet type before the session is allowed to trade; screenshot of the logged-in cabinet showing real balance |
| 2 | Correct account detection | The connected account is definitively identified as the intended Live account, not a different one | Account balance, currency, and any visible account ID match the operator's own out-of-band knowledge of that account |
| 3 | Correct execution | A test trade's asset/direction/expiry/stake match exactly what was intended | Compare the signal AXIM received against the trade actually placed (DB row `signals.*` vs. broker UI/history) |
| 4 | Correct expiry | The trade's expiry on the broker matches the configured expiry, not a default or drifted value | Broker trade-history expiry timestamp vs. `signals.expiry`/`opened_at` |
| 5 | Correct stake sizing | The dollar amount actually risked matches what the active Money Management strategy computed | `signals.trade_amount` vs. the broker's own recorded stake for that trade |
| 6 | Correct reconciliation | A trade's real win/loss/draw outcome is correctly pulled back from the broker and recorded | `signals.result`/`profit_loss` matches the broker's own trade history exactly, including for a trade that resolves while AXIM was briefly disconnected |
| 7 | Correct bankroll updates | The Fund's `trading_balance` reflects real P&L after each trade, with no drift | `fund_manager.get_fund_balances` compared against the broker's own account balance after a small batch of trades |
| 8 | Correct recovery after interruption | Killing the process mid-trade and restarting resumes correctly — no lost trade, no duplicate, no orphaned state | Kill `core/telegram_listener.py` while a trade is open; restart; confirm the trade resolves exactly once and reconciliation catches it |
| 9 | Correct stop-loss | A configured daily/session loss limit halts new trades the moment it's reached, never after | Deliberately configure a very small loss limit; confirm the session stops at or before the limit, never over it |
| 10 | Correct stop-win | A configured profit target halts new trades the moment it's reached | Same as #9, mirrored for the profit-target path |
| 11 | No duplicate trades | The same signal never executes twice | `core/risk_manager.check_duplicate_signal`'s real behavior confirmed against a deliberately re-sent/duplicated signal |
| 12 | No missed trades | Every valid signal that should execute does execute — none silently dropped | Compare Telegram channel message count against `signals` row count for the validation window, reconciled against explicit rejections/skips (all of which are logged with a reason) |
| 13 | No race conditions | Two near-simultaneous signals (or a signal arriving during a session-start/stop) never produce inconsistent state | Deliberately send two signals within the same second; confirm both are handled correctly and sequentially, no double-charged bankroll |

**Stake sizing for this phase:** the smallest stake Pocket Option's own
UI allows, on a Fund seeded with the minimum amount needed to survive
the full checklist's loss-limit test (#9) without risking anything
beyond that pre-committed amount.

---

## Phase 4 — Graduation Criteria (Go / No-Go)

AXIM graduates a specific broker account from Demo Production to Live
Production only when **every** condition below is true, evaluated
per-account (graduating one Live account never implies another is
graduated):

- [ ] Phase 2's engineering items (#3, #5, #6, #7) are implemented,
      tested, and merged.
- [ ] Item #2 (`LIVE_URL`) is supplied by the operator and passes
      `verify_live_account()`.
- [ ] Item #8's honest win-rate observation window is complete, with
      the resulting number reviewed and explicitly accepted by the
      operator as economically viable — this is a product decision,
      not something this plan can pass/fail on its own.
- [ ] Item #9's full reboot-recovery test has a documented pass.
- [ ] Every row in Phase 3's Live Validation Checklist has passed,
      independently verified (not self-reported), against the specific
      account being graduated.
- [ ] The operator has given a fresh, explicit, in-the-moment
      instruction to enable Live for that account — a plan describing
      these steps, including this one, does not itself constitute that
      approval.

**No-Go conditions (any one of these blocks graduation regardless of
everything else passing):** an unresolved duplicate-trade or
missed-trade finding from Phase 3; a reconciliation mismatch between
AXIM's recorded outcome and the broker's own trade history; a stop-loss
or stop-win that fired late or not at all during validation; any crash
during the interruption-recovery test that left an ambiguous trade
state.

Once graduated, the account's Capability Matrix status becomes **Live
Production Approved** (see Phase 5) — every other account remains at
whatever status it was already at.

---

## Phase 5 — Documentation

`docs/AXIM_CAPABILITY_MATRIX.md` is updated after every milestone in
this plan, using the 5-state taxonomy this plan introduces:

- **Production Ready** — done, no Live-specific gate applies (most of
  the existing matrix).
- **Configuration Required** — blocked only on a value the operator
  must supply (e.g. `LIVE_URL`).
- **Product Decision Required** — blocked on a decision only the
  product owner can make (Leviathan, Oracle, item #8's win-rate
  acceptance).
- **Live Validation Required** — engineering is done; a real Live
  account and Phase 3's checklist are what's left.
- **Live Production Approved** — a specific broker account has passed
  every Phase 4 criterion and been explicitly, freshly approved.

---

## Status log

- **2026-08-01** — Plan created (this document). Phase 1 audit
  complete. Phase 2 not yet started.

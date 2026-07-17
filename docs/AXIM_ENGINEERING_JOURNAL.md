# AXIM Engineering Journal — Autonomous Execution Session

Started 2026-07-15, following a Mini PC reboot and a full state audit (no work lost -
master was clean and pushed; one research worktree had uncommitted-but-complete work,
recovered and committed). This journal covers the autonomous Tier 2 execution session
that followed, per standing instruction: work continuously, commit frequently, only
stop for credentials/paid services/real-money/irreversible-deletion/unresolvable
production-safety issues, and report everything back in one Executive Progress Report
at handoff.

Tier 2 order (as given):
1. Finish the Provider Intelligence research queue.
2. Complete the Capital Allocation Engine.
3. Implement automatic provider backtesting.
4. Run every approved bankroll strategy against eligible providers.
5. Build the recommendation engine.
6. Recommend minimum/suggested/conservative capital allocations.
7. Implement one-click Create Recommended Demo Fund.
8. Implement interactive Telegram bot support where applicable (Demo-first).

---

## 2026-07-15 — Pre-autonomous-mode audit and research recovery

**Reboot audit.** Confirmed via `git status`/`git log`/worktree inspection: master clean
and pushed at 044db35, AXIM API and AXIM Listener scheduled tasks running and healthy
(listener self-recovered from a mid-reboot browser timeout via existing
`browser_warmup`/`recovery.py` machinery - no pending trades lost), full production
regression suite green (787 passed, 1 skipped).

**Item 1 (Provider Intelligence) - closed out.** One research worktree
(`C:/AXIM-telegram-research`, branch `telegram-provider-intelligence`) had a fully-written
but uncommitted adapter (Daniel FX Trade) sitting in the working tree when the reboot
hit - verified via the full 91-test research suite before committing, nothing lost.
Continued the roadmap's provider queue to completion:

- **Daniel FX Trade** (commit 1b25234) - Demo-ready. Recovered pre-reboot work.
- **SIGNALS # 2 Not Martingale** (commit c1f56d0) - Forward observation required.
  Confirmed genuinely no martingale (matches its own name), but found a more severe
  version of NTrade's result-verification gap: 100% of this channel's results are
  captionless photo attachments - zero text-resolvable outcomes at all, not just most.
- **VIP | Signals** (commit fb03484) - Unsupported/unsafe. Zero loss markers across
  325 messages (same red flag as the already-flagged Micha Trader | Vip), compounded
  by a stake-multiplier pattern ("x2"/"x4"/"x10") shaped like a recovery-after-loss
  mechanic, and 47 of 98 "win" results referencing trades that were never announced
  as their own signal message at all ("Personal VIP entry" bonus trades).
- **Go+ | Trading Bot** (commit 62c77fa) - confirmed Insufficient history, but with
  real substance now: only 4 of 53 messages are actual signal output, the rest is
  unverifiable marketing copy. Not adapted - not enough real signal volume to validate against.
- **NEBORTRADE** (commit 62c77fa) - reclassified from "Insufficient history" to
  "Unsupported/unsafe (not a signal source)". Full read revealed this is a scripted
  fund-then-get-signal-access onboarding DM, not a signal channel - zero signals
  ever appear. Flagged as a structural red flag, not a data-volume problem.

All 10 OPT SIGNALS providers now have a final, evidence-based classification. Research
branch: 113/113 tests passing, clean working tree, 5 new commits this session.
Production (`C:\AXIM`) untouched throughout - every research commit message confirms this.

---

## 2026-07-15 — Autonomous Tier 2 execution session

User handed off with a standing autonomous-execution directive: work through Tier 2
items 2-8 without interim check-ins, commit frequently, only stop for credentials/
paid-services/real-money/irreversible-deletion/unresolvable-production-safety issues.
Branch: `tier2-capital-allocation-engine` off master.

**Item 2 (Capital Allocation Engine) - already complete, verified, no new work needed.**
Assessed core/fund_manager.py, database.py's funds/fund_capital_transfers/fund_sources
tables, and web/funds.html before writing anything: Reserve accounting
(`get_broker_account_reserve`/`transfer_capital`), broker-account attachment, and
per-provider Signal Source attachment (`fund_sources`, POST/DELETE
`/api/funds/{id}/sources`, a working checkbox UI in web/funds.html) all already exist
and are tested (69/69 fund-related tests passing). This *is* "a single Pocket Option
balance with allocated capital per provider" - initial assessment of `fund_sources` as
a dormant/unused table was wrong (grepped the literal string "fund_sources" first and
missed the real function names `add_fund_source`/`list_fund_source_channel_ids`).

**Item 3-4 (automatic provider backtesting + running the 4 official strategies).**
Two real gaps found before building: (1) the 4 official Money Studio strategies
(core/money_studio.py) had no persistent `is_template=True` risk_profile rows - only
created ad hoc when a user clicks "Use This Strategy" - so Strategy Lab's backtest
picker couldn't select them at all without a user first doing that by hand. Added
`database.seed_money_studio_templates()` (idempotent, same pattern as the existing
27-strategy library seeder), called from api/main.py startup. (2) No bridge existed
from the research branch's parsed signal+result data into core/backtest_engine.py's
`imported_signals` pool. Built `scripts/import_provider_research.py` - a standalone
script (not a core/ module or live API route, since it depends on the research
worktree existing as a sibling directory, which won't be true on every machine this
repo is cloned onto) that: imports every Demo-ready-classified provider's cleanly-
linked win/loss/draw trades (Martin Trader, OTC Pro Trading Robot, TYLER VIP CLUB,
Pocket Option Signals, Daniel FX Trade - the only classification with real, trustworthy
outcome data), tags every row with the research branch's own non-broker-verified
caveat, and auto-runs a backtest_run per provider against all 4 seeded strategies.
Real bug caught by a live run against the actual production DB: the script never
called `database.initialize_database()`, so a schema addition made after the live API
process's own last startup (the new `capital_recommendations` table, see below) never
got created - fixed by calling it (idempotent) at the top of the import function.

**Item 5-6 (recommendation engine + min/suggested/conservative allocations).** Built
`core/capital_recommendation.py`: picks each provider's #1-ranked backtested strategy
(reusing backtest_engine.rank_strategies' existing composite score) and computes three
allocation tiers as explicit, documented multiples of that strategy's own real
max_drawdown_amount (1.5x/2.5x/4x), floored at max($50, avg_trade_size x 10) - same
"labeled heuristic, never a fabricated projection" discipline backtest_engine.py's own
risk_score/best_for_label already use. New `capital_recommendations` table, one row
per provider (upserted, not accumulated). 9 unit tests, all passing.

**Item 7 (one-click Create Recommended Demo Fund).** `api/capital_recommendation_routes.py`:
creates a fresh Fund sized at the chosen tier, deploys the recommended strategy via the
existing `create_risk_profile_from_snapshot` mechanism (same one
`/api/backtest/runs/{id}/strategies/{id}/deploy` already uses), attaches a Demo broker
account, and attaches the provider's Signal Source channel via `fund_sources` *if* that
channel is already synced into AXIM - if not, the Fund is still created correctly and
the response says plainly that the source attachment needs to be done manually, rather
than silently skipping it. Real bug caught by its own test suite before it shipped: the
first draft double-nested the fund report under two "fund" keys
(`{"fund": fund_manager.get_fund_report(...)}`, where get_fund_report's own return
already has a "fund" key) - renamed to "fund_report". 8 tests, all passing. Added a
"Provider Recommendations" tab to web/strategy_lab.html (reusing its existing
.compare-card styling) with a "Create Recommended Demo Fund" modal (tier picker +
optional name) rather than a new top-level nav page, since this is additive to an
existing screen, not a new UI Vision surface.

**Item 8 (interactive Telegram bot support, Demo-first).** Found this already fully
built and tested on the unmerged `worktree-client-server-realtime-sync` branch
(commit 6e3e66e, "AXIM Core: build the interactive Telegram bot trigger-command
workflow") - self-contained (core/telegram_bot_trigger.py, a telegram_listener.py
integration point, web/telegram.html's max_requests_per_session field, 12 tests with a
fake Telethon client), and NOT entangled with that branch's SSE real-time-sync fork-in-
the-road (a separate, still-undecided concern documented on master since a079ac0).
Ported forward rather than rebuilt: checked master's current `database.get_channel`/
`broker_account_manager.route_signal`/`get_trading_session` signatures matched exactly
what the ported module expects (they'd already independently converged), so the port
required zero adaptation - all 12 ported tests passed immediately. Send-command/await-
reply/parse/route/wait-for-result/stop-at-session-limit loop, using Telethon's
`client.conversation()` (isolated from the main passive listener), routing every reply
through the same `broker_account_manager.route_signal()` passive channels use.

**Real bug found and fixed via live verification against the actual production
database (not caught by any test, since tests use realistic-but-modest sample data):**
running scripts/import_provider_research.py for real produced a genuinely absurd
recommendation for Martin Trader - "Alternating Compound" ranked #1 with an ROI of
~1.4x10^14 percent and a suggested allocation of ~$1.5x10^14. Root cause: Martin
Trader's real backtested win rate is ~93% (a martingale-recovery provider, where most
chains eventually resolve as a "win"), and a fixed-percent-of-bankroll strategy with no
profit vault compounds hyperexponentially over 1630 real trades with no cap.
backtest_engine.rank_strategies' composite score normalizes ROI trivially to 1.0
regardless of scale, so an inflated-beyond-reason ROI always wins that ranking -
checked all 4 official strategies against this provider's real data and every single
one blows up. Fixed in core/capital_recommendation.py: pick_best_strategy now excludes
any strategy whose ROI exceeds a documented 5000% implausibility ceiling before
ranking applies; if nothing plausible remains (as for Martin Trader), it returns None -
no confident recommendation is better than a fabricated one, matching this project's
standing discipline (Micha Trader/VIP | Signals' flagged win-rate implausibility).
Also fixed: the stale $1.5x10^14 recommendation row was NOT deleted when regeneration
found nothing plausible to replace it with (caught by checking the actual database
after the "fix," not just re-running the pure functions) -
generate_recommendation_for_provider now deletes a provider's existing recommendation
in that case. Also fixed a cosmetic-but-misleading trades_backtested count silently
capped at 500 by list_imported_signals' default limit.

**Current recommendation state, verified against the real production database:**
OTC Pro Trading Robot (Capital Preservation, ROI -46.78%, suggested $2051.40), TYLER
VIP CLUB (Recovery Ladder, ROI 95.97%, suggested $2409.08), Pocket Option Signals
(Recovery Ladder, ROI 234.8%, suggested $2851.40), Daniel FX Trade (Growth
Accelerator, ROI 46.06%, suggested $2522.20). Martin Trader correctly has NO
recommendation - a known, honest limitation of applying generic bankroll-management
strategies to a martingale-recovery provider's real data, not a bug to paper over.

**Verification approach:** every new pure function has unit tests (56 total across the
Capital Recommendation Engine + Telegram bot port); the one-click Create Recommended
Demo Fund endpoint was verified via its own DB-backed test suite (8 tests covering the
full orchestration - Fund creation, strategy deployment, broker-account/source
attachment, graceful handling of an unsynced channel) plus a direct call against real
recommendation data to confirm the read path renders correctly. Deliberately did NOT
invoke the actual fund-creation mutation against the production database unprompted -
that creates a real (if Demo-only) Fund in the user's own account, left for them to
trigger deliberately via the new Strategy Lab tab.

**Merge and live rollout.** Merged `tier2-capital-allocation-engine` into master
(`--no-ff`, preserving branch history) after the full suite passed on the branch (833/833).
Re-ran the full suite on merged master to confirm: 833/833 again. Did NOT push to
`origin` - that's a repo-visible action beyond what "create commits"/"merge approved
feature branches" in the standing autonomy grant covers, left for deliberate review.

**Real operational incident found and resolved while bringing the new code live:**
restarting the "AXIM Listener" Scheduled Task via `Stop-ScheduledTask`/`Start-ScheduledTask`
does NOT reliably terminate the underlying python.exe process - confirmed directly:
after several restart attempts, FOUR separate `telegram_listener.py` processes were
found running concurrently (one dating back to this morning's reboot, PID 12160,
never actually replaced by any of the "restarts"), all fighting over the same
Playwright browser profile (`sessions/pocket_browser`), which is what caused the
repeated "Opening in existing browser session" launch failures - not stale files, not
Chrome cleanup, but genuinely duplicate live processes. Each failed attempt was also
independently leaking orphaned chrome.exe processes (~20 accumulated at the worst
point, on top of the reboot's own leftover batch from the morning audit) faster than
`scripts/cleanup_axim_chrome.ps1` alone could keep up while duplicate listeners kept
running. Resolved by force-killing every listener PID directly (`Stop-Process -Force`),
running the existing cleanup script once against a genuinely process-free state, then
starting exactly one fresh instance - confirmed via `Get-CimInstance` process listing
before the final start, not just trusting the Scheduled Task's own reported state.
Verified stable for 5+ minutes post-restart: clean startup log (demo mode verified,
6-worker pool built, recovery ran, broker account adopted), exactly one listener
process, a normal ~15 chrome.exe count, live API healthy and serving the new
`/api/capital-recommendations` route (401, not 404, confirming registration).

**Worth flagging for future reference, not fixed here (out of Tier 2's scope):**
`Stop-ScheduledTask` on this Task Scheduler configuration does not appear to guarantee
the underlying process actually exits before returning - a real gap in the existing
`scripts/install_scheduled_task.ps1`/restart tooling, separate from anything Tier 2
touched. Anyone restarting these services in the future should verify via
`Get-CimInstance Win32_Process` that the old PID is actually gone before starting a
new instance, not just trust the Scheduled Task state.

Later the same evening: fixed the exact mismatch above for real (force-killed the
orphaned PID directly rather than relying on Stop-ScheduledTask, cleaned Chrome,
started fresh, verified Task Scheduler state and process reality agree) - clean this
time, no leaked Chrome processes. Also disabled `live_enabled` on a pre-existing,
dormant Fund ("Tyler Live Trading", created 2026-07-12, untouched since, `live_enabled=1`
with no live broker account anywhere in the system to justify it) - a deliberate,
narrow, reversible safety correction, nothing else about the Fund touched.

Delivered a full ground-truth Executive Status Audit on request (git/branch/worktree
state, every running process, every Fund and recommendation in the real database, the
complete OPT SIGNALS provider table, feature-by-feature status) - caught two more real
findings in the process: (1) a stale pre-existing Fund with a live-trading flag set (see
above), (2) `web/capital_strategies.html` is a DIFFERENT, older ~20-strategy system than
Money Management Studio - the actual 4-strategy Studio UI lives in `web/risk.html`
(confirmed only after an initial grep false-negative, since strategy names are populated
dynamically from the API, never hardcoded in the HTML - corrected before reporting).

---

## 2026-07-15 (continued) — Phase 2: Provider Intelligence Engine (Priority #1)

User declared V1 complete and opened **Phase 2**: build toward a commercial SaaS
product, not just an admin dashboard, with standing autonomous authorization except
for live-trading risk / data deletion / credentials-payment-external-accounts /
fundamental production-behavior changes. Execution order given: Provider Intelligence
Engine first, then Portfolio Command Center, capital allocation, automatic
re-analysis, Money Management Studio polish, then commercial SaaS capabilities.
Branch: `phase2-recommendation-engine` off master.

**Extended the recommendation-card field set** (`core/capital_recommendation.py`,
`capital_recommendations` table migrated with 8 new columns): net profit, ending
balance, longest losing streak, average daily trades, a documented confidence score
(60% sample-size / 40% session-consistency, saturating at 500 trades - an explicit
heuristic, not a statistic), a derived 1-5 star rating, and a recommended session
goal/daily stop scaled to the actual suggested allocation (not the backtest's own
smaller starting bankroll). A losing backtest gets NO session goal at all (None, not a
fabricated positive number) - the daily stop is still always computed, since downside
protection is meaningful even for a strategy that lost money on average. Real bug
caught before shipping: an INSERT statement had 21 `?` placeholders for 22 columns
(`sqlite3.OperationalError: 21 values for 22 columns`) - caught immediately by the
test suite, not live. 26 tests, all passing.

**Built the automatic Provider Language Learner** (`core/provider_language_learner.py`)
- the actual flagship ask: detect a new provider's signal format automatically, without
a hand-written adapter first. A library of pattern templates encoding the real
structural shapes found across the 12-provider OPT SIGNALS research corpus (compact
single-line BUY/SELL, compact HIGH/LOWER, labeled multi-field blocks, two-step
asset-then-direction messages), each scored against a real message batch, best-scorer
used if it clears a 10% coverage floor, otherwise honestly reports "no pattern fits"
rather than force one. **Validated directly against the real 12-provider research
database** (not synthetic data): 6 of 10 real signal-providers got a detected pattern -
Daniel FX Trade's auto-detected signal count (154) matched the hand-built adapter's
count *exactly*; Pocket Option Signals and VIP | Signals came close. Correctly refused
to detect a pattern for NEBORTRADE and Go+ (the two providers confirmed NOT to be real
signal sources) and for four complex providers (TYLER VIP CLUB, OTC Pro Trading Robot,
Martin Trader, Pattern Signals) this v1's pattern library doesn't cover yet - an honest,
documented limitation, not a hidden gap. 23 tests, all passing.

**Wired it into a real onboarding flow** (`core/provider_onboarding.py`,
`api/provider_onboarding_routes.py`, a new "Analyze This Provider" button on
`web/telegram.html`): one action takes a synced channel, fetches history via a new
`telegram_channels.fetch_channel_raw_history` (returns everything unfiltered, unlike
the existing `fetch_channel_history` which only keeps what the live per-message parser
already recognizes), runs the language learner, imports decided trades, auto-backtests
all 4 official strategies, and generates a capital recommendation - reusing every
piece already built in Tier 2 rather than duplicating it. Every inconclusive outcome
("no history," "pattern not detected," "pattern detected but nothing links to a
result") is a normal returned status, never an exception - matching this project's
standing "never fabricate confidence" discipline. 4 tests (mocked Telegram fetch, real
DB-backed orchestration downstream of the mock), all passing.

Deliberately did NOT touch `parsers/signal_parser.py` (the live, real-time, per-message
parser the Telegram listener uses to actually place trades) - the language learner is
a separate, batch-mode, analysis-only module, never imported by
`core/telegram_listener.py`/`core/trade_coordinator.py`/`execution/pocket_executor.py`,
same isolation discipline the OPT SIGNALS research adapters already established.

---

## 2026-07-15 (continued) — Phase 2 Priority #3: a real architectural gap found

**Real, load-bearing finding - NOT fixed here, deliberately.** Set out to stress-test
"two providers trading concurrently, sharing one broker account" (Priority #3's own
example: Tyler VIP $250 + Go+ $500 out of one $1,000 Pocket Option balance). Writing a
genuine concurrent-thread test (`tests/test_risk_engine.py`'s
`test_real_concurrent_sizing_never_cross_contaminates_two_funds`) surfaced that
`database.start_trading_session`'s exclusivity check
(`get_active_trading_session_for_broker_account`) is scoped to `broker_account_id`, not
`fund_id` or channel: **two Funds sharing the SAME broker account cannot both have an
active session today, at all** - the second `start_trading_session` call raises before
a second session row even exists. Since fund-scoped routing
(`core/broker_account_manager.route_signal`) requires an active `session_id` to resolve
a Fund in the first place, this means only ONE Fund sharing a broker account can be
actively auto-trading via its own session at any given moment - not the "each provider
trades independently and concurrently, sharing one balance" vision Priority #3
describes. Added `test_two_funds_on_the_same_broker_account_cannot_both_have_an_active_
session_today` as an explicit characterization test (asserts the CURRENT behavior, not
a judgment that it's correct long-term) - a regression guard so this doesn't get
silently lost, and a trigger to update this note if it's ever deliberately changed.

**Why this wasn't fixed autonomously**: relaxing `trading_sessions`' exclusivity model
is a change to core, safety-critical session machinery - heartbeat monitoring,
`recovery.py`'s resume-on-restart logic, and every implicit "at most one session"
assumption elsewhere in the codebase would all need a fresh audit before loosening it.
This is exactly the "fundamentally change existing production behavior" category
flagged for a deliberate decision, not a quick patch - surfaced clearly here instead of
either silently working around it or silently leaving it undiscovered.

**What WAS verified and IS real**: once two sessions are both active (today, that
requires two different broker accounts), the underlying risk-engine/database layer has
no cross-fund contamination bug under genuine OS-thread concurrency (50 rounds,
`ThreadPoolExecutor`, interleaved real database writes) - a necessary but not
sufficient condition for the full one-shared-account vision. Also confirmed along the
way: `risk_engine.compute_position_size` deliberately holds sizing flat within one
active session (subtracts the session's own still-live `realized_pnl` back out of the
fund's aggregate balance, to avoid double-counting - see the pre-existing
`test_current_sessions_own_pnl_is_not_double_counted`) - my first draft of the new test
assumed sizing would grow with each round's P&L update and failed until I understood
this existing, correct design.

**Recommended path when this is picked up**: the two realistic options are (1) scope
`start_trading_session`'s exclusivity check to `(broker_account_id, fund_id)` instead of
just `broker_account_id` - the narrower, more surgical change, since the execution layer
(`execution/browser_worker_pool.py`) already safely handles genuinely concurrent trades
within one browser context (Phase 5, verified live with two simultaneous real trades) -
or (2) move away from session-scoped Fund routing entirely toward a persistent
`fund_sources`-based routing model that doesn't require an "active session" concept per
Fund at all. Not decided here - flagged as a fork in the road, same discipline as the
already-documented SSE real-time-sync fork-in-the-road.

---

## 2026-07-16 — Phase 2 Priority #2: Portfolio Command Center redesign

Rebuilt `web/dashboard.html`'s top-level stats around real portfolio performance
(Priority #2's exact spec) rather than the previous engineering-flavored view:

- **New backend**: `core/fund_manager.get_portfolio_overview()` + `GET /api/funds/
  portfolio-overview` - total portfolio value, weekly/monthly P&L, overall ROI, overall
  win rate, current exposure (real open trades' stake, from `database.get_open_trades`),
  today's trades, active Funds/sessions count, and a full Fund Card per active Fund
  (provider, allocated capital, current equity, today's P&L, win rate, strategy name,
  active session name, goal progress %, status). Deliberately did NOT present weekly/
  monthly figures as a "growth %" - AXIM has no historical portfolio-value time series
  to compute one honestly from, so these are real dollar P&L over that window instead
  (Phase 2 mandate: "never invent analytics or statistics"). `trade_statistics.
  weekly_stats`/`monthly_stats` gained an optional `fund_id` param (additive, matching
  the pattern `daily_stats`/`lifetime_stats` already had) to make this possible.
- **New frontend**: the old "Your Funds" section was a bare name+P&L mini-list: real
  Fund Cards now, front and center, formatted per commercial-dashboard conventions
  (equity as the hero number, everything else in a clean stat table, a goal-progress
  bar when a session has a real target).
- 7 new tests (`tests/test_fund_manager.py::PortfolioOverviewTestCase`), all passing.

**Real, live UI verification performed** (not just server-side tests, per this
project's own "verify the actual UI" discipline): spun up an isolated snapshot-DB
preview instance (copied `data/axim.db`, port 8092, a temp directory - never touching
the live production instance or its real users), created a throwaway test account in
that COPY only, and drove it end-to-end with Playwright: logged in, loaded the
redesigned dashboard, and screenshotted it. Confirmed live: all 7 new top-level stats
render correctly with real numbers (Weekly P/L +$4.12, Monthly P/L -$42.82, Overall ROI
-28.4%, win rate 38.8%, etc.), and all 5 real Funds render as proper cards with every
required field. Two console errors appeared on the very first page load (401/fetch
failures) - re-tested by waiting through several of the page's own periodic refresh
cycles on an already-settled page and got zero errors, confirming the first-load
errors were a timing artifact of the test script's own rapid post-login navigation,
not a real bug in the shipped code. Preview instance and its temp directory torn down
afterward - nothing left running, nothing touched in production.

---

## 2026-07-16 — Phase 2 Priority #4: automatic scheduled provider re-analysis

Every provider's recommendation is no longer a one-time, point-in-time judgment.
`core/provider_reanalysis.py` re-runs the full onboarding/recommendation pipeline
(`core/provider_onboarding.py`) for any provider with a real, currently-synced
Telegram channel, on a daily schedule (`scripts/install_reanalysis_task.ps1`,
3:00 AM via Windows Task Scheduler, mirroring the existing soak-snapshot task).

- **`classify_change(old, new)`** (pure) decides whether a re-analysis is worth
  interrupting the owner about: a different best strategy now wins, win rate
  dropped 5+ percentage points, ROI dropped 10+ percentage points, or every
  official strategy became implausible on the fresh data. Small drift below
  those thresholds is treated as normal sample noise and produces no notes.
- **`reanalyze_all_known_providers()`** walks every existing `capital_recommendations`
  row, and for each one checks whether it has a real synced channel
  (`database.find_channel(title=source_label)`). The 5 research-derived static
  providers (OPT SIGNALS import, no live channel) are skipped with an explicit,
  reported reason rather than silently re-running analysis against a historical
  dump that can never produce a different answer - re-analysis is only meaningful
  where there's live history to actually refresh from. Providers that are
  reanalyzed and show a meaningful change get the owner notified via
  `database.create_notification`, visible in the Notification Center.
- 10 new tests (`tests/test_provider_reanalysis.py`): `classify_change`'s threshold
  logic (strategy-change, win-rate/ROI deterioration, no-longer-recommended,
  no-prior-recommendation), and the DB-driving orchestration (skip vs. reanalyze,
  notify vs. stay silent on a no-op re-analysis). Full suite: 894 tests, OK.

Not yet run in production - the scheduled task installer script exists but hasn't
been executed, since it needs a live authenticated Telegram session the same way
`POST /api/channels/sync` does, and installing a new always-on scheduled task is
exactly the kind of change worth a deliberate, separate call rather than bundling
it silently into this commit.

---

## 2026-07-16 (continued) — Phase 2 Priority #6: Money Management Studio audit

User handed over full autonomous engineering mode before stepping away for the day
(standing authorization: everything except live-trading risk, credentials/payment/
external accounts, irreversible data loss, or destructive ops outside the AXIM repos).
Continuing straight through the Phase 2 execution order without pausing for check-ins.

Audited `core/money_studio.py` + `web/risk.html` against the Priority #5 spec (exact
risk %, martingale, vault, growth-recalc thresholds per strategy) line by line: it
already matched exactly, including the honest, already-documented gaps (growth-
threshold recalculation and Alternating Compound's true per-trade cycle have no
real `core/risk_engine.py` equivalent yet, disclosed both in code comments and on the
strategy detail page rather than faked). The frontend already had the full commercial
UI - pros/cons, FAQ, worked examples, growth timeline, Custom Strategy Builder - from
the prior `ui-vision-upgrade` work. The actual gap was test coverage: neither the pure
math (worked examples, growth timelines, recovery-ladder steps) nor
`api/money_studio_routes.py`'s 3 endpoints had direct unit tests before today.

- Added `tests/test_money_studio.py` (18 tests: strategy cards/detail shape, exact
  dollar math for all 4 strategies' worked examples and growth-checkpoint timelines,
  `risk_profile_fields_for`'s real-engine field mapping for every strategy).
- Added `tests/test_money_studio_routes.py` (7 tests: list/detail/create-profile
  endpoints, including that "Use This Strategy" really wires up martingale/vault
  settings on the saved profile).
- Found and removed a real product-quality risk while auditing: `web/capital_strategies.html`,
  a leftover ~20-strategy catalog page from before the "4 official strategies" redesign
  (commit `6e8866a`), was still being served at `GET /capital-strategies` in
  `api/main.py` - unreachable from any nav/shell link (confirmed via repo-wide grep),
  but still live in production. Anyone who found the URL would see a contradictory,
  superseded strategy system next to the real Money Management Studio. Removed the
  page and its route. `core/capital_strategies.py` (the engine underneath, which still
  powers the real, tested `per_trade_vault_skim` vault mechanism Money Studio's Vault
  strategies use) and its API router/tests are untouched - only the dead page is gone.
- Full suite: 922 tests, OK.

---

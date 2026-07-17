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

## 2026-07-16 (continued) — Phase 2 Priority #7: commercial SaaS readiness assessment

This task is explicitly an assessment, not a build ticket, so it's reported as one.
Audited what already exists vs. what's genuinely missing, and *why* the missing
pieces aren't something to build blind under full autonomy:

**Already commercial-grade, confirmed by reading the actual code (not assumed):**
- **Onboarding**: `web/wizard.html` is a full 8-step first-run flow (Owner Account,
  Telegram, Pocket Option, Risk Profile, Channels, Session, Demo Test, Ready) - not a
  stub, a real guided setup.
- **Pricing/plan display**: `web/billing.html` + `core/billing.py`'s `PRICING_PLANS`
  render a real plan-comparison page (Free/Trial/Basic/Professional/Elite/Enterprise),
  correctly showing an honest "not connected to a live payment provider yet" banner
  rather than a fake "Subscribe" button that would silently do nothing.
- **Billing scaffold**: Stripe Checkout session creation + webhook handling
  (`core/billing.py`) is fully written and tested (`tests/test_billing.py`), gated
  behind `is_configured()` - it goes live the moment real `STRIPE_SECRET_KEY`/
  `STRIPE_WEBHOOK_SECRET` values are added to `.env`. Nothing left to build here
  without those credentials, which only the user can supply (an explicit stop
  condition, not a gap in the code).

**Genuinely missing - and deliberately not built without a product-direction call:**
- **Referral system**: does not exist anywhere in the codebase. Investigated whether
  it's safe to add unilaterally and concluded it isn't yet, for an architectural
  reason, not a laziness one: `core/database.py` has no tenant/organization concept
  at all - every account creation goes through `api/admin.py`'s admin-only
  `create_user`, and the standing product default ([[project-axim-default-assumptions]])
  is Tailscale-only private access with "no public ports/IP/domain by default." A
  referral system implies either (a) public self-serve signup, which contradicts that
  standing default and would itself require a DNS/hosting decision (an explicit stop
  condition), or (b) some other shape nobody has defined yet. Building a speculative
  multi-tenant referral/signup architecture on a guess would risk real wasted work if
  the actual commercial model turns out to be "each customer runs their own private
  install" rather than a shared hosted product - this is category-4 territory
  ("changes the core product vision"), not a routine engineering decision.
- **Public self-serve signup**: same reasoning - blocked by the standing private-network
  default, not by missing code.

**Verdict**: Phase 2's roadmap items #1 through #6 (Provider Intelligence Engine
through Money Management Studio) are complete and tested. Item #7's buildable,
credential-free pieces (onboarding, pricing display) were already done. What remains
undone is either blocked on user-supplied credentials or is a product-direction
decision worth a real conversation, not a guess - flagged here rather than silently
built or silently skipped.

Production check after today's changes: API root (`http://127.0.0.1:8090/`) returns
200, full test suite green (922 tests). Nothing regressed.

Restarted the `AXIM API` scheduled task (`Stop-ScheduledTask` + `Start-ScheduledTask`,
verified the old PID actually died first per the known Stop-ScheduledTask gap) so
today's accumulated Phase 2 work is actually live in production, not just committed.
Confirmed post-restart: root page 200, the removed `/capital-strategies` route now
correctly 404s, `/api/money-strategies` 401s (exists, requires auth). Listener left
untouched - nothing it depends on changed today.

User handed over full end-of-day autonomy ("keep working autonomously, ping me if you
hit a real blocker") after the above was reported as roadmap-complete. Continued
rather than stopping.

---

## 2026-07-16 (continued) — Priority #4 fix: false-positive re-analysis notifications

Rather than leave `scripts/reanalyze_all_providers.py` merely unit-tested, ran it for
real against live data - `core/telegram_channels.py`'s `fetch_channel_raw_history` uses
its own dedicated `axim_ui_session` Telethon session (already authenticated from
earlier onboarding work), confirmed independent of the live listener's own session, so
this was safe to run without touching production trading.

That real run found a genuine bug: 2 of the 4 providers with a live channel (TYLER VIP
CLUB, Pocket Option Signals) have signal formats the auto language-learner still can't
recognize - a known, already-disclosed limitation, not something new. But
`reanalyze_provider` treated that automation failure as if it were a real change,
sending the owner a notification worded like the provider's recommendation had gotten
worse, when nothing about the provider actually changed - only the automatic refresh
itself failed. Left uncorrected, this would have trained the owner to distrust real
deterioration alerts.

- Fixed: `reanalyze_provider` now returns an explicit `refresh_failed` flag, kept
  separate from `classify_change`'s real change notes. A failed automatic refresh is
  now visible in the run summary (for logs/a future admin view) but never sent as an
  owner notification, since the existing recommendation is completely untouched.
- Manually deleted the 2 erroneous notifications (ids 13, 14) the buggy first run had
  already written to the real production database, then re-ran the corrected script
  against the same live data and confirmed: zero new notifications, both
  recommendations byte-for-byte unchanged.
- Installed `scripts/install_reanalysis_task.ps1` as a real Windows Scheduled Task
  (`AXIM Provider Reanalysis`, confirmed next run Fri 2026-07-17 3:00 AM) - Priority #4
  is now actually scheduled and running, not just built and left dormant.
- 1 new regression test (`test_an_automation_refresh_failure_never_looks_like_a_change`).
  Full suite: 923 tests, OK.

---

## 2026-07-16 (continued) — Trade History unreachable from any nav link

User confirmed continued full autonomy again ("keep working autonomously, ping me if
you hit a real blocker"). Kept scanning for real, safe work rather than declaring done
after the roadmap-complete report: cross-checked every page `api/main.py` serves
against every nav/page-to-page link in the app (the same technique that found the
orphaned Capital Strategies page earlier today).

Found `web/trades.html` - a real, unique Trade History detail view (per-trade
screenshots, raw signal message, full stage timeline; nothing else in the app
duplicates it) - had zero links pointing to it anywhere. `web/shell.js`'s
2026-07-14 nav-IA-reorg comment claimed it was "kept reachable via a link from
Performance," but that link had never actually been added - a real product gap
that had gone unnoticed since the reorg, not a deliberate removal like Capital
Strategies was.

- Added a "View Trade History" link to `web/performance.html`'s header.
- Corrected the same stale `shell.js` comment, which also still implied Capital
  Strategies was "kept reachable via a link from Money Management Studio" - false as
  of this morning's cleanup; that page was superseded outright by the Money Studio
  redesign and removed, not relocated.
- Verified live against the running production server directly (HTML/JS are served
  straight from disk, no API restart needed): `curl` confirmed the new link renders
  on `/performance` and `/trades` still returns 200.
- Full suite: 923 tests, OK.

---

## 2026-07-16 (continued) — Priority #4 hardening: one provider can't take down the rest

Since `scripts/reanalyze_all_providers.py` now actually runs unattended every night at
3 AM (installed earlier today), re-checked its failure handling with that in mind, not
just its happy path. Found `reanalyze_all_known_providers()`'s per-provider loop had no
try/except around `reanalyze_provider` - and `core/provider_onboarding.py`'s own
docstring is explicit that it deliberately lets a *real* failure (a dropped Telegram
connection, not just an inconclusive "pattern not detected" result) propagate rather
than swallowing it. Left as-is, one provider hitting a genuine connection error at 3 AM
would have crashed the whole run and silently skipped every remaining provider that
night, with nothing but an uncaught traceback to show for it.

- Wrapped each provider's reanalysis in a try/except, logged via `core/logger.py`, and
  reported as a new `"error"` summary status - same treatment as `refresh_failed`
  (existing recommendation untouched, no misleading notification) - so the loop
  continues to the next provider instead of aborting.
- 1 new regression test proving a flaky provider's exception doesn't stop a healthy
  provider in the same run from being reanalyzed. Full suite: 924 tests, OK.

This closes out today's Priority #4 work: built, then actually exercised against live
data (which found and fixed the false-positive-notification bug), then hardened for
its real unattended failure mode (this fix) - not just built and left untested against
reality.

---

## 2026-07-16 (continued) — Real production bug: a zombie session blocking every new session

Kept auditing real production data rather than stopping after the roadmap-complete
report (per the user's continued "keep working autonomously"). Checked every real Fund
for the same class of stale-flag issue the earlier Tyler Live Trading correction found
(`live_enabled=1` with nothing backing it), and found something worse.

Fund #19 ("Primary Fund," archived 2026-07-12) still had `live_enabled=1` - but digging
into *why* surfaced a much bigger finding: its session #11 was still `status='active'`
in the database, `ended_at=None`, on `broker_account_id=11` - the system's ONLY real
broker account ("Pocket Option Demo (Primary)", still connected, still being checked
today). The fund had been archived on 2026-07-12 without its session ever being
stopped first. Since `core/database.py`'s `get_active_trading_session()` /
`get_active_trading_session_for_broker_account()` are unfiltered by fund status,
**this one zombie session had been blocking `start_trading_session`'s exclusivity
check - both the global fallback and the real broker-account-scoped check - for 4
days.** Right now, before this fix, starting a new trading session on ANY Fund would
have incorrectly failed with "a session is already active - stop it before starting
another," even though nothing was actually trading. Confirmed nobody had hit it yet
only because none of the 4 newer Demo Funds created this week had ever had a session
started (checked directly: zero `trading_sessions` rows for any of them).

- Manually corrected the stale data: stopped session 11 via `database.
  stop_trading_session` with a full audit-trail `stop_reason` (DEMO mode, 2 trades,
  -$1.00 realized - no live-money impact), cleared Fund 19's `live_enabled` flag.
- Fixed the root cause: `api/funds_routes.py`'s `archive_fund` now stops any of the
  Fund's active sessions and clears `live_enabled` at archive time, so this can't
  happen again. 4 new regression tests (`tests/test_funds_routes.py`), including one
  proving an unrelated Fund's active session is left untouched by someone else's
  archive action.
- Full suite: 928 tests, OK. Restarted the `AXIM API` scheduled task to deploy the
  route fix (Python code, unlike the earlier static HTML/JS fixes - verified old PID
  died, new PID came up, root page 200, `get_active_trading_session()` confirmed
  `None` post-restart).

This is the most consequential finding of today's autonomous session - a real,
currently-live blocker on the core trading-session-start path, found only by directly
auditing production data rather than trusting that "the tests pass" meant the system
was in a healthy state.

---

## 2026-07-16 (continued) — TYLER VIP CLUB gets a real named pattern

User checked in mid-session ("tell me exactly what you're doing right now") while this
was in progress - answered directly (file, feature, why it's not critical-path, ETA,
next step, blocker status) and was told to finish verifying this one piece, then stop.
Recorded here for completeness before stopping as instructed.

TYLER VIP CLUB (a real, currently-synced provider) had been reporting
`pattern_not_detected` on every automatic re-analysis attempt - its real vocabulary
("BUY/SELL NOW X (OTC)" signals, "WIN"/"Bad luck" results, "Raise your stake"/"Go back
to the initial trade size" recovery instructions) doesn't fit any of
`core/provider_language_learner.py`'s generic reusable templates. "Bad luck" in
particular isn't in `_classify_result_token`'s generic loss vocabulary at all.

Rather than write new speculative parsing logic, ported the already-validated hand-
built adapter from the OPT SIGNALS research branch
(`research/parser/adapters/tyler_vip_club.py` - grounded in an exhaustive read of that
provider's real 2906-message dump by the original research effort, not guessed) into a
new named pattern, `"tyler_vip_flow"`, added to the existing template-scoring system
alongside the generic ones. Provider-specific by design - its coverage on the other
providers' real corpora should stay near zero, which the new test suite explicitly
verifies (`test_detect_pattern_identifies_a_tyler_like_batch` asserts every other
template scores exactly 0.0 on Tyler-style text).

**Verified against the real channel's actual current history before committing**, not
just synthetic test fixtures: fetched TYLER VIP CLUB's live 2000-message history via
the same `axim_ui_session` used earlier today, confirmed `detect_pattern` now correctly
identifies `"tyler_vip_flow"` at 20.94% coverage (well above the 10% viability
threshold), and ran the full `analyze_and_onboard_provider` pipeline end-to-end: 323
real decided trades imported, a real backtest run, a real recommendation generated with
sane numbers (44.9% ROI, 54.2% win rate). A test-call mismatch (passing a slightly
different `source_label` string than the real channel title) briefly created a stray
duplicate `capital_recommendations` row in production - caught immediately, removed via
`database.delete_capital_recommendation`, confirmed only the original row remains and
zero notifications fired as a side effect.

8 new regression tests. Full suite: 936 tests, OK.

Deliberately not attempted tonight: the same port for Pocket Option Signals (the other
provider stuck on `refresh_failed`) - a natural, well-scoped next step using the exact
same technique (its hand-built adapter also already exists in the research branch), but
out of scope for this specific verification per the user's explicit "finish verifying
this one, then stop."

---

## 2026-07-16 (continued) — Mission correction: "Complete V1 first"

User issued a large correction: stop treating individual Telegram providers as the
primary scope (they're examples/test data) and finish V1's actual product
requirements - multiple Pocket Option accounts (including Live), Fund-to-account
routing, and a generalized, reusable provider-onboarding pipeline that doesn't need a
fresh research effort per provider. Standing full autonomy reconfirmed for this work.
Given the scope (genuinely weeks of normal engineering), tracked it as 4 explicit
tasks (#23-26) rather than attempting to fake one-shot completion.

**First step: audit what actually exists before building anything new.** This found
the "multiple broker accounts" requirement already substantially built, contrary to
the directive's own assumption:
- `core/broker_account_manager.py` already gives every CONNECTED broker account its
  own isolated `BrowserWarmupService`/`BrowserWorkerPool`/`TradeCoordinator` - separate
  `user_data_dir` (no cookie/session sharing), separate execution queue, one account
  failing to connect never touches another (`stop_all()`'s teardown is best-effort per
  account).
- `fund_manager.can_trade` already requires an independent `live_enabled` flag on BOTH
  the Fund and the broker account, plus the account being connected/active/live-
  capable, before authorizing live trading - a stale Fund-level flag alone was already
  provably insufficient (confirmed directly by reading the code, then proven with a
  new test using the exact historical "Tyler Live Trading" shape).
- `web/funds.html` already has a working broker-account-assignment picker; `api/
  broker_accounts_routes.py` already exposes add/connect/disconnect/archive as real
  web endpoints - `POST /connect` spawns `scripts/connect_broker_account.py`, which
  opens a real login browser window and writes the result back via polling, not
  something the user runs from a terminal.

**Fixed the real gaps found**, in two commits:

1. **Alternating Compound's real cycle** (`core/risk_engine.py`, `core/backtest_engine.py`,
   `core/money_studio.py`) - the directive correctly called out that this strategy's
   saved profile approximated its 2.5%/5%/2.5%/5% cycle as a flat 3.75% average, since
   the compounding model only ever stepped risk-percent against cumulative session P&L,
   never trade count. Added a new `"alternating_cycle"` compounding mode (`trades_count
   % len(cycle)`, completely ignoring P&L) - no schema migration needed, `mode`/
   `steps_json` were already generic TEXT columns. `core/backtest_engine.py` already
   reuses `risk_engine._base_amount` directly (one shared sizing engine for live and
   backtest, already true before tonight, confirmed by reading the code) - it only
   needed `trades_count` added to its session state. The already-seeded production
   template (id=35) was corrected directly, since `seed_money_studio_templates()` is a
   no-op once templates exist and wouldn't have picked up the fix on its own; every
   future automatic provider backtest now uses the real cycle, past historical
   snapshots are untouched. 6 new tests.

2. **Broker Accounts UI polish** (`web/broker.html`, `api/broker_accounts_routes.py`) -
   the page still framed itself as "your Pocket Option connection" (singular) with a
   legacy single-account status section sitting above the real multi-account table -
   relabeled it "AXIM Process & Default Connection" to disambiguate. Added a per-account
   Edit action (name/mode - the PATCH endpoint already supported it, just no UI),
   a Last Connected column (data already existed, never displayed), and an active-
   session indicator per assigned Fund in "Used by" (`has_active_session`, a small
   addition to `_with_funds`). 4 new tests (including the stale-live-flag proof).

Full suite after both: 945 tests, OK. API restarted to deploy (Python route/engine
changes, unlike the earlier static-HTML-only fixes) - confirmed root page and
`/broker` both 200 post-restart.

Remaining from this corrected mission: the Provider Onboarding Wizard (history-window
selector, preview/validate/correct step before committing an import) - the single
biggest gap, tracked as task #26, next up.

---

## 2026-07-17 — Provider Onboarding Wizard: history window + preview/correct

Replaced the old single "Analyze This Provider" black-box button (fetch + detect +
import + backtest + recommend, all atomic, no way to review anything first) with a
real preview-then-commit workflow.

- `core/telegram_channels.fetch_channel_raw_history` gained an optional `days` param
  (7/14/30/60/90, default 30) - an early break once a Telethon message (iterated
  newest-first) is older than the cutoff, still capped by the existing message-count
  limit either way.
- `core/provider_onboarding.preview_provider(chat_id, days)` runs the exact same
  fetch+detect pipeline `analyze_and_onboard_provider` does, but returns a sample of up
  to 30 parsed signal/result pairs (original message text, parsed asset/direction/
  expiry, confidence, warnings) WITHOUT writing anything to the database.
  `analyze_and_onboard_provider` gained `excluded_message_ids` - signals a reviewer
  unchecks in the preview are dropped from the committed import.
- New `POST /api/provider-onboarding/preview` route; `.../analyze` extended with
  `days` + `excluded_message_ids`.
- `web/telegram.html`'s "Analyze This Provider" button now opens a real wizard modal:
  history-window selector -> Preview (sample table, per-row exclude checkboxes,
  confidence, warnings) -> Import, Backtest & Recommend. Steps 5-7 of the spec
  (backtest/compare/recommend/create-fund) already existed via Strategy Lab's Provider
  Recommendations tab - reused, not rebuilt.

**Honest scope note, stated directly rather than glossed over**: this excludes bad
matches from the commit; it does NOT yet let a reviewer correct a wrong asset/
direction and have AXIM re-learn the provider's pattern from that correction. A
genuinely editable, database-stored parsing-rule system (the wizard spec's "hybrid"
ask: universal parser + database-driven provider rules + configurable pattern
definitions) is real, separate follow-up work - not built tonight, not claimed as
built. The pattern library remains code-based templates in
`core/provider_language_learner.py`.

14 new tests. Full suite: 955 tests, OK. **Verified live against real data** before
committing: `preview_provider` against TYLER VIP CLUB's real channel (2000 messages,
`tyler_vip_flow` pattern, 30 sample trades, 323 total decided trades) - confirmed
`imported_signals` count identical before and after (323/323, preview genuinely writes
nothing). Separately confirmed the `days` cutoff itself actually filters: a 30-day
window hit the 2000-message cap on this high-volume channel (oldest message 21 days
back), while a 1-day window correctly returned only 103 messages, all from the last
24 hours - proving the cutoff logic works, not just passed through inertly.

Restarted the `AXIM API` scheduled task to deploy (Python route/engine changes).
Confirmed post-restart: root page 200, `/telegram` 200, new preview endpoint 401
(exists, requires auth). Noticed the Telegram listener had also restarted cleanly
around the same time (new PID, clean startup log, no errors) - unrelated to this
work, not a crash.

This closes out the corrected V1-completion mission's 4 tracked tasks (#23-26).
Remaining, assessed honestly: (1) a handful of smaller Broker Accounts spec items not
yet built (a distinct "Test Connection" action separate from "Connect", explicit
per-account rate/session limits, a per-account Emergency Stop distinct from the
existing global one) - real gaps, smaller in scope than what's been closed tonight;
(2) "Real-Time Forward Analysis" (compare recent vs. historical performance, flag
format/confidence changes, recalculate on a schedule) is already substantially covered
by tonight's earlier Priority #4 work (`core/provider_reanalysis.py` + the nightly
scheduled task), not something built fresh under this directive - worth confirming
explicitly against the directive's exact wording rather than assuming full overlap;
(3) the database-driven editable parsing-profile architecture (Step 3 of the wizard
spec) remains a genuinely separate, larger initiative.

---

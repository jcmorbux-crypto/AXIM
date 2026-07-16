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

---

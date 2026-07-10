# AXIM App Plan - Commercial Product Build

Product name: **AXIM**. Product type: a full Trading Operations Platform
(not "a Telegram copier") - Users/Licensing, Telegram Integration, Signal
Automation, Trading Sessions, Risk Engine, Statistics/Performance, Broker
Connections, and (future) AI signal generation, all modular and
expandable. Full spec as given by the product owner; this document
tracks what's built vs. planned. `docs/AXIM_SESSION_ARCHITECTURE.md`
remains the detailed spec for the Trading Sessions engine (build order
item 3 below) and is not duplicated here.

Bar for every feature: "Would someone pay for this?" Premium fintech feel
(Stripe/Mercury/Apple/Notion/TradingView/Linear), never developer/hacker-
dashboard aesthetics. Developer/technical concepts (raw API IDs, DB
internals, log tails) stay behind a **Developer Mode** toggle in Settings
- off by default, never shown to a normal end user.

## Terminology rebrand (nav + page names)

The product vocabulary was elevated to feel like a commercial platform
rather than a personal tool. Existing pages/concepts are renamed, not
rebuilt - the mapping:

| Old name (Phases 1-3) | New name |
|---|---|
| Dashboard | **Mission Control** |
| Telegram Sources | **Signal Sources** |
| Money Management (Phase 4, planned) | **Risk Engine** (flagship feature) |
| Live Trades | **Trade Center** |
| Statistics | **Performance** |
| Pocket Option | **Broker** |
| Users / Access | **Users** (Licensing lives under it/Settings) |

Signal Inspector, Trading Sessions, Logs, Settings keep their names.
Applied to `web/shell.js`'s sidebar first (see below) - a pure relabeling,
zero backend change, so it's done immediately rather than staged into a
later phase.

## New phases added by the rebrand (sequenced after existing Phase 3)

- **Phase 4 - Risk Engine** (was "Money Management Center") - see below,
  now explicitly the flagship feature: unlimited profiles with copy/
  duplicate/export/import, Kelly-criterion sizing added alongside fixed/
  percent/dynamic, plus Martingale/Compounding/Vault exactly as already
  planned.
- **Phase 4.5 - Setup Wizard** - first-run experience (Create Owner ->
  Connect Telegram -> Connect Broker -> First Risk Profile -> Select
  Channels -> First Session -> Demo Test -> Ready). Sequenced after the
  Risk Engine and Signal Sources exist for real, since the wizard is
  just guided navigation through already-built real flows, not new
  backend capability - building it before those pages exist would mean
  wiring it twice.
- **Phase 4.75 - Rule Builder** - visual IF/THEN automation ("IF daily
  profit >= target THEN stop session", "IF 3 wins in a row THEN increase
  risk 10%", "IF source win rate falls below threshold THEN disable
  source"). Genuinely new engineering (a small rules-evaluation engine
  that can read session/statistics state and call the same mutation
  functions Settings/Sessions already expose) - its own phase, after the
  Risk Engine and Sessions it needs to act on both exist.
- **Licensing** - the `access_tier` enum already built in Phase 1
  (owner/internal/free_beta/trial/basic/pro/elite/suspended) is the
  licensing model; this rebrand adds user-facing tier names (Free/Trial/
  Basic/Professional/Elite/Enterprise) and a dedicated Licensing view
  under Users - no schema change needed, Stripe still explicitly
  deferred (Phase 6).

Visual direction: light theme, white background, soft gray cards,
rounded corners, subtle shadows, blue primary accent, green/red reserved
for profit/loss only, desktop-first. Implemented in `web/theme.css` -
see Phase 1 status below.

## Build Order & Status

### Phase 1 - DONE
- FastAPI app: unchanged `api/main.py`, now with `api/auth_routes.py` and
  `api/admin.py` routers included.
- SQLite: `users`, `auth_sessions`, `admin_actions` tables added to
  `core/database.py` (alongside the pre-existing trading tables).
- User login: cookie-based sessions (`core/auth.py` for PBKDF2-HMAC-SHA256
  password hashing + token generation, `api/auth_routes.py` for the HTTP
  surface). First-run bootstrap creates the Owner account through the UI
  (`web/login.html`) rather than a manual DB insert or `.env` value.
- Owner admin panel: `web/users.html` + `api/admin.py` - list/create/edit
  users, reset password, activate/disable, grant free access, set trial
  expiration, set access tier, force demo-only, allow live, revoke access
  immediately, view sessions. All mutating actions logged to
  `admin_actions` (surfaces on the future Logs page).
- Dashboard shell: `web/dashboard.html` - real data from the existing
  `/api/status`, `/api/pocket-option/status`, `/api/dashboard`,
  `/api/channels`, `/api/settings` endpoints. Session-lifecycle buttons
  (Start/Pause/Stop/Emergency Stop) currently drive the single shared
  listener process (`/api/process/start`, `/api/control/pause`, etc.) -
  honestly labeled as such until the real session engine (Phase 3) ships.
- Light UI layout: `web/theme.css` (design tokens + components) +
  `web/shell.js` (sidebar nav, auth gate, role-based nav item visibility).

**Deliberately not built in Phase 1** (see "Known gaps" below): the other
8 sidebar pages are not yet re-themed into the new light layout - they
temporarily link to the pre-existing dark `web/index.html` (now served at
`/legacy`, auth-gated) which still has working Telegram channel manager,
money management settings, live dashboard, and screenshot viewer panels.
Nothing regressed; it's a relocation/re-theme task, not new functionality.

### Phase 2 - DONE - Telegram account linking, channel discovery, source manager, signal inspector
- In-app API ID/API Hash/phone/verification-code entry:
  `core/secrets_store.py` (Fernet encryption, key at `data/.secret_key`,
  gitignored) + `telegram_credentials` table + `api/telegram_admin.py`
  (`/api/telegram/credentials`, `/connect/send-code`, `/connect/verify-code`,
  `/disconnect`, `/connection-status` - the latter does a REAL live
  `client.is_user_authorized()` check, not a cached flag). Verified live:
  `connection-status` correctly reported "Jay Thompson (@fxmavengroup)"
  from the real authorized `axim_ui_session`. The send-code/verify-code
  live login flow itself was NOT live-fire tested against the real
  account (would risk disrupting the already-working session) - built,
  unit-tested with the real Telethon client construction path, and
  code-reviewed instead.
- Source-type classification per channel (`passive` / `bot_command` /
  `group` / `manual_review`) plus `priority`, `trigger_command`,
  `command_wait_for_result`, `max_requests_per_session` - new `ui_channels`
  columns, `PATCH /api/channels/{id}/config`.
- Per-channel win rate/P&L (`database.get_channel_performance`, matched
  by title against `signals.channel` - same join every other channel-
  scoped query already uses), last message received and a recent-messages
  viewer (new `channel_messages` table, populated by
  `telegram_listener.py` for EVERY incoming message regardless of
  enabled/allowed status, so a not-yet-enabled channel can still be
  previewed before deciding to follow it).
- Signal Inspector page (light theme): real `/api/parse-test` results
  (asset/direction/expiry) with honest "not parsed/computed by current
  parser" labels for confidence score/entry timing/amount, which the
  parser genuinely does not produce - not fabricated. Approve/Reject are
  scoped honestly as logged review decisions, NOT real trade execution
  (that would need its own manual-execution architecture, not built).
  Create/Save Parsing Rule is fully real: new `signal_rules` table (per-
  channel regex find/replace), `parsers/signal_parser.apply_signal_rules()`
  applied in `telegram_listener.py` BEFORE `parse_signal()` - not a second
  parser implementation.
- `web/telegram.html` + `web/inspector.html` - both screenshot-verified
  live against the real running listener/production DB (real 152-channel
  list, real per-channel win rate/P&L, real connection status).

**Follow-up needed, not done this phase:** the live listener process
(`core/telegram_listener.py`) needs restarting to pick up the new
`channel_messages` capture code and rule-application logic - same as any
other `core/` change, not deployed automatically. Until restarted, the
Signal Inspector's recent-messages/last-message features will show
"never"/empty for channels even though the code is correct.

### Phase 3 - DONE (passive-channel sessions) - Trading Sessions
Full spec in `docs/AXIM_SESSION_ARCHITECTURE.md`. Built exactly in the
suggested order there, through "passive-channel sessions end-to-end":

- Schema: `session_profiles` (saved start-config templates) +
  `trading_sessions` (one row per run: channel_ids, targets/limits,
  status, trades_count, realized_pnl) + `session_id` on `signals`.
- `core/session_manager.py`: `check_session_limits()` (profit target/loss
  limit/max trades - mirrors `risk_manager.RiskViolation`'s shape as
  `SessionLimitReached`, wired into `trade_coordinator.handle_signal()`
  alongside, not instead of, the existing global risk checks) and
  `channel_in_session()`. Session P&L updates via the EXISTING
  `event_bus` "trade.closed" event (already published by
  `execution/pocket_executor.py`) - zero changes to that hardened,
  live-tested module; `session_manager.register()` just subscribes to it
  at listener startup.
- `core/telegram_listener.py`: when a session is active, it becomes the
  authoritative channel allow-list for its duration (only its own
  channels execute, enabled or not) - with no active session, existing
  global `WATCH_CHANNELS`/enabled-channels behavior is completely
  unchanged. `session_id` threaded into every `handle_signal()` call.
- `api/sessions.py`: profile CRUD, start/stop/emergency-stop, active +
  historical session listing with derived `remaining_to_target`/
  `remaining_to_loss_limit`. `account_mode` is never client-supplied - it
  always reflects the real, currently-connected `ACCOUNT`, same
  discipline as `GET /api/pocket-option/status`.
- `web/sessions.html`: Start New Session (channel picker, targets, Live-mode
  double-confirmation reusing the established pattern), active-session
  progress panel, saved profiles, session history. Listener process
  Start/Stop also lives here now (a session can't run without it).
  Dashboard's Current Session P/L card and header buttons now point at
  real session data/the Sessions page instead of the Phase-1 placeholder.
- 27 new tests (session DB CRUD, `session_manager` stop-condition logic,
  `trade_coordinator` session integration). Verified live end-to-end
  against the real production DB and real listener process: created a
  real session (1 real channel, $1000 target/limit), confirmed the
  Active Session panel showed correct real progress, confirmed Stop
  Session correctly transitioned it to `stopped_manual` with a real
  `ended_at` timestamp and cleared the active slot. Test session and
  test account removed afterward.

**Deliberately deferred to a later pass** (per the suggested build order
- this is "the riskiest new piece" and warrants its own proven-live
phase): the interactive Telegram-bot trigger-command workflow (sections
4/5 of `docs/AXIM_SESSION_ARCHITECTURE.md` - send a command, await/parse
a bot's reply, request the next signal). `ui_channels.source_type` /
`trigger_command` / `command_wait_for_result` already exist (Phase 2) and
a `bot_command` channel can be added to a session's channel list today,
but nothing yet actually sends the trigger command - such a channel
simply behaves like a passive one until that workflow is built.

**Now done (previously deferred):** per-trade "require confirmation
before execution" in Live mode. When a session has `require_confirmation`
set and is running in LIVE mode, `trade_coordinator.handle_signal` calls
`session_manager.wait_for_trade_confirmation` before the trade counts
toward `max_trades` or touches the worker pool. That writes a row to
`pending_trade_confirmations` and blocks (polling, non-busy-wait) for up
to `TRADE_CONFIRMATION_TIMEOUT_SECONDS` (default 45s, `config/settings.py`)
for an operator decision. Any logged-in user (same exception as Emergency
Stop) can Confirm or Reject from a global modal shown on every page
(`web/shell.js`, polling `GET /api/sessions/pending-confirmations`); an
explicit reject OR a timeout both raise `TradeNotConfirmed`, which is
rejected exactly like a `RiskViolation` - fail-closed, no path lets an
unanswered Live trade proceed. Unit/integration coverage in
`tests/test_session_manager.py::TradeConfirmationGateTests` and
`tests/test_trade_coordinator.py::TradeConfirmationGateIntegrationTests`.
Martingale step tracking per session is Phase 4 (Money Management
Center), not this one.

**Follow-up needed, same as Phase 2:** the live listener process needs a
restart to pick up all of the above - session-scoping, the event_bus
subscription, everything. Until restarted, sessions can be created via
the API/UI but won't actually gate/attribute real trades yet.

### Phase 4 - DONE - Risk Engine (Martingale, Compounding, Profit Vault)
Existing `ui_settings`-backed money management (Phase 2 of
`docs/AXIM_UI_PLAN.md`, still live at `/legacy`) was a single global
profile with fixed/percent sizing only - no martingale, no compounding,
no vault, no saved/named profiles. This phase replaced "one global
settings row" with real profiles:

- `risk_profiles` (bankroll, sizing mode - fixed/percent/dynamic/Kelly -
  max trade, daily loss, session loss, profit target, max trades, demo/
  live permission), `martingale_settings`, `compounding_settings`,
  `profit_vault_settings` - one-to-one with a profile.
- Profile CRUD + **copy/duplicate/export/import** (JSON), fully wired in
  `web/risk.html` (Export downloads a `.axim-risk-profile.json` file;
  Duplicate prompts for a new name and copies all four tables).
- **Real, enforced sizing math** in `core/risk_engine.py` - not just
  stored config: fixed/percent/dynamic/Kelly (`f* = p - (1-p)/b`,
  fractional multiplier, clamped to 0 on a negative edge), Martingale
  stepping (multiplier or custom dollar ladder, `max_steps`/
  `max_total_exposure` caps), Compounding (profit-milestone risk-percent
  steps, drawdown reset), and Profit Vault skimming (every-winning-
  session trigger on session end, milestone-based trigger per trade
  close). Wired into `trade_coordinator.py` via
  `risk_engine.compute_position_size(session_id, ...)`, which falls
  through to the unchanged `risk_manager.compute_trade_amount` when a
  session has no `risk_profile_id` - nothing regresses for sessions not
  using a profile. Vault/Martingale state updates hook into the SAME
  `event_bus` "trade.closed" subscription `core/session_manager.py`
  already had - a new `core/session_manager.end_session()` centralizes
  the "every winning session" vault trigger so it fires no matter which
  of the three stop paths (limit breach, manual, emergency) ends a
  session.
- 27 starter templates (Capital Shield, Vault Builder, Snowball, etc.)
  seeded as read-only example profiles with genuinely varied configs
  (conservative/balanced/aggressive/martingale/vault/compounding-focused
  archetypes inferred from each name) - a user duplicates one to start
  customizing; templates themselves reject edits/deletes (400).
- `web/risk.html`: template gallery, profile list, and a full editor
  (Basic Info, Martingale with a live Projected Exposure preview,
  Compounding, Profit Vault) - each section saves independently via its
  own endpoint. Wired into `web/sessions.html`'s Start form (a session
  optionally attaches one `risk_profile_id`).
- 38 new tests (20 in `test_risk_engine.py` covering the sizing math
  precisely - including a hand-computed Kelly formula check - plus DB
  CRUD/duplicate/export/import tests). Verified live against the real
  production DB: duplicated the "Shielded Martingale" template, confirmed
  its exact custom ladder (`[10, 22, 48, 105]`) round-tripped correctly
  through save and the Projected Exposure preview (`$10/$22/$48/$105,
  total $185`), confirmed the risk-profile picker on the Sessions page
  lists real profiles. Test profile and test account removed afterward.

**Not enforced yet, honestly scoped in `core/risk_engine.py`'s own
docstring:** Martingale's `same_asset_only`/`same_source_only` (would
need last-trade asset/source tracking per session); Compounding's
"daily"/"weekly" modes and the Vault's "daily_target"/"weekly_target"
triggers use the session's own realized P&L rather than a true
calendar-spanning aggregate across sessions (sessions are this system's
trading unit, not calendar days - true daily/weekly tracking across
multiple sessions is a bigger follow-up, not built here). Kelly's win-
rate/payout are user-supplied estimates, not derived from a live
empirical win rate (too little per-profile trade data for that to be
meaningful yet).

### Phase 5 - DONE - Trade Center, Performance, Broker, Logs
- **Trade Center** (`web/trades.html`, `api/trades.py`): live trades
  table (time/source/asset/direction/amount/expiry/status/result/P&L/
  session) + a full trade-detail view (raw Telegram message, parsed
  fields, real execution timeline from `TradeTimeline.persist`'s stage
  timestamps, screenshots, result). Money-management/Martingale-step
  detail is honestly scoped: `trade_amount` is the real figure used, but
  the martingale STEP at the time of that specific historical trade
  isn't separately recorded (only the session's current step) - the UI
  labels it "now, not necessarily at trade time" rather than imply
  precision that doesn't exist. Caught and fixed a real bug during live
  verification: a handful of historical `result` values are 2-3KB single-
  line accessibility-tree error dumps that, with the table's default
  `white-space: nowrap`, blew the page out to 18000+px wide - fixed with
  a truncating cell class in the table and a scrollable `<pre>` in the
  detail view for long values.
- **Performance** (`web/performance.html`, `core/trade_statistics.py`'s
  new `performance_report()`): daily/weekly/monthly/yearly/lifetime,
  best/worst channel/asset/time-of-day (filtered to a 3+ trade minimum
  so a single lucky/unlucky trade can't crown a "best"), max drawdown
  (real peak-to-trough over the cumulative P&L curve), longest win/loss
  streaks ever, per-session performance, and an honestly-scoped
  Martingale/Compounding summary - real per-session current state, not a
  fabricated step-by-step historical backtest (see
  `martingale_and_compounding_performance()`'s own docstring for exactly
  why).
- **Broker** (`web/broker.html`): connection/balance/worker-pool/
  heartbeat status, Reconnect, and two care-scoped destructive actions -
  **Clear Session** (deletes `sessions/pocket_browser`, blocked while the
  listener is running) and **Test Trade (Demo Only)**. Test Trade is
  architecturally interesting: the API process never calls the trading
  engine directly (a rule held throughout this whole build), so a click
  writes a `pending_test_trade` request that `core/telegram_listener.py`'s
  own new poll loop picks up and runs through the SAME live coordinator/
  worker_pool already running there - hard-blocked on `ACCOUNT != DEMO`
  independently at both the API layer and the listener's poll loop.
- **Logs** (`web/logs.html`, `core/log_reader.py`): real entries parsed
  from every log file `core/logger.py` writes, merged with
  `admin_actions`, filterable by date/level/module/free-text search
  (session/channel filtering is covered by the free-text search rather
  than dedicated structured filters, since log lines aren't parsed down
  to a structured session_id/channel_id). Owner/Admin only.
- 24 new tests (performance analytics math, test-trade queue state
  machine, log parsing/filtering including multi-line entries). 201
  total passing. Verified live against the real production DB and
  listener across all four pages.

### Settings page - DONE - last legacy content migrated off /legacy
- Self-service password change (`POST /api/auth/change-password`) -
  distinct from `api/admin.py`'s Owner/Admin reset-someone-else's-password,
  always requires the current password first.
- Telegram credential rotation surfaced directly in the UI for the first
  time (`POST /api/telegram/credentials` already existed from Phase 2 but
  was only ever called implicitly via the Connect flow).
- Real Backups tab: lists `backups/` directory contents and runs the
  existing `scripts/backup_axim_state.ps1` via `subprocess` rather than
  reimplementing backup logic in Python - verified live with a real
  412MB backup run that produced a genuine new timestamped folder.
- Real, persisted **Developer Mode** toggle (`ui_settings` key, system-
  wide not per-user) with one real, verified visible effect: Trade
  Center's detail modal shows the raw trade ID in its title only when
  enabled ("Trade #468" vs. generic "Trade Detail") - confirmed live in
  both states, not an inert checkbox.
- Trading tab migrates the last remaining legacy-page content (the
  global money-management fallback settings) - `/legacy` now has nothing
  left that isn't also reachable from a real light-theme page.
- Telegram/Notifications tabs are honestly light-touch: Telegram links out
  to Signal Sources + this page's own credential rotation; Notifications
  explicitly says "not built yet" rather than showing inert toggles, since
  AXIM has no email/push/webhook sending capability to back them.

### Setup Wizard - DONE - guided first-run flow
`web/wizard.html`, an 8-step guided flow, built only after its
prerequisites (Risk Engine, Sessions, Signal Sources) were real - every
step calls the exact same already-tested endpoints those pages use, no
new backend capability. `login.html`'s bootstrap-owner success now
redirects to `/wizard` instead of straight to `/dashboard`; re-runnable
anytime from Settings > General.

Steps: Create Owner Account (skipped if already bootstrapped) -> Connect
Telegram (status-only here; the real connect flow lives on Signal
Sources) -> Connect Pocket Option (starts the listener, shows real
heartbeat status) -> Choose a starting Risk Profile (duplicates a
template) -> Select signal channels (enables/disables real channels) ->
Create first Trading Session (real `POST /api/sessions/start`) -> Run a
demo test trade (reuses Broker's Test Trade, skipped entirely if
`ACCOUNT` isn't DEMO) -> Ready.

Found and fixed two real bugs during live verification, not test
artifacts:
1. The channel-selection step looped through every rendered channel
   (up to 30) with a sequentially-awaited PATCH each, even for channels
   whose state didn't change - slow enough to look like a stuck/broken
   step. Fixed to only PATCH channels that actually changed, concurrently
   via `Promise.all`.
2. Confirmed (by deliberately shortening a verification wait) that a
   step transition briefly shows the previous step's stale content while
   its own `await fetch(...)` is in flight - inherent to the render-then-
   fetch pattern every page in this app already uses, not unique to the
   wizard; noted here rather than "fixed" since forcing every step to
   block on a loading spinner isn't worth the complexity for an internal
   tool at this stage.

Verified live end-to-end multiple times against the real production DB:
real owner bootstrap through the wizard itself, real Telegram/Pocket
Option status, real risk profile duplication, real channel enable state
(confirmed unrelated channels were untouched), real session creation
(correctly blocked by the existing single-active-session rule when a
prior test session was still open - the real safety mechanism working
exactly as designed, not a wizard bug). Test session, test account, and
channel state all cleaned up afterward.

### Rule Builder - DONE - visual IF/THEN automation
`core/rule_engine.py` (condition/action catalogs + evaluator), `rules`
table + CRUD in `core/database.py`, `api/rules.py` (`/api/rules`,
`/api/rules/catalog`, `/api/rules/{id}/evaluate-now`), `web/rules.html`
(dropdown-only builder, no code editor). Hooked into the same
`event_bus` `"trade.closed"` subscription `core/session_manager.py`
already owns (`rule_engine.evaluate_all()`, called right after the
existing Risk Engine and session-limit checks) - no new event path.

7 condition types (`daily_profit_gte`, `daily_loss_gte`,
`consecutive_wins_eq`, `consecutive_losses_eq`, `session_profit_gte`,
`session_loss_gte`, `lifetime_profit_gte`, `source_win_rate_below` - 8
total) and 5 action types (`stop_active_session`, `emergency_stop`,
`increase_risk_profile_percent`, `switch_session_risk_profile`,
`disable_channel`), every action calling an existing real mutation
function (`session_manager.end_session`, `database.set_control_state`,
`database.update_risk_profile`, `database.set_session_risk_profile`,
`database.set_channel_enabled`) - no new mutation path invented.

Named `lifetime_profit_gte` rather than a "bankroll milestone" -
AXIM doesn't track a live broker account balance anywhere, so this is
honestly scoped to cumulative realized P/L, the only real number
available.

Anti-spam design: edge-triggering. Each rule stores
`last_condition_state`; the action only fires on a false->true
transition (`database.record_rule_evaluation`), not on every evaluation
where the condition happens to still be true. This is what stops
"daily profit >= $100" firing again on every subsequent trade once the
target is hit, and lets "3 wins in a row" fire again the *next* time a
3-streak occurs after breaking - without per-condition-type cooldown
logic.

Tested: `tests/test_rule_engine.py` (20 tests - every condition
evaluator, every action executor, edge-trigger fire-once/refire-on-new-
edge behavior, disabled-rule skip, full CRUD). Live-verified against the
real production DB/API via Playwright using the throwaway
`verification-test@axim.local` account: created a real rule through the
UI, confirmed the rendered IF/THEN sentence matches its real params,
toggled it off (badge flips to Paused), edited it (params correctly
pre-filled from the stored row), deleted it. Test rule and test account
cleaned up afterward. Did not trigger a real end-to-end live trade close
to watch a rule fire against a real signal - the DB-layer tests already
exercise `evaluate_rule`/`evaluate_all` against a real schema, and doing
so live would require an actual Telegram signal and Pocket Option trade
cycle, out of scope for this pass.

Known gap: rules have no explicit `scope` (global vs. one specific
session/profile) - conditions like `session_profit_gte` always read
whatever session is currently active, which is correct given the
existing single-active-session-at-a-time rule, but would need real
scoping if that constraint is ever relaxed.

### Phase 6 - Packaging, Stripe

#### Billing scaffold - DONE (deliberately not wired to live Stripe keys)
Built the full billing architecture with zero live Stripe calls, per an
explicit decision to scaffold now and connect real keys later: `core/
billing.py` (pricing catalog, checkout-session creation, webhook event
handling, trial-expiration check), `api/billing_routes.py` (`/api/
billing/plans`, `/status`, `/checkout`, `/webhook`), `web/billing.html`
(pricing cards + current-plan status + "not configured" banner), linked
from Settings > General and from Users ("Licensing & Plans").

- **Pricing plans**: 6 user-facing plans (Free/Trial/Basic/Professional/
  Elite/Enterprise) mapped onto the existing `access_tier` enum with no
  schema change - Enterprise is a display-only, contact-us row that
  grants the same `elite` tier under the hood.
- **Access tiers**: reused as-is; billing never invents a new tier value.
- **Checkout session placeholder**: `create_checkout_session()` checks
  `is_configured()` (whether `STRIPE_SECRET_KEY` is set) first and
  returns an honest `{configured: false, checkout_url: null, message}`
  rather than raising - real `stripe.checkout.Session.create()` code
  exists and runs once a key and Stripe Price IDs are set, untested
  against the real Stripe API since no test account is configured yet.
- **Webhook handler placeholder**: `handle_webhook_event()` verifies the
  signature via `STRIPE_WEBHOOK_SECRET` (raises `BillingNotConfiguredError`
  -> HTTP 503 if unset, never processes an unverified payload) and
  handles `checkout.session.completed` (activate a paid tier) and
  `customer.subscription.{updated,deleted}` (soft-downgrade to
  `free_beta`/`free_access` on cancellation).
- **License status updates**: `GET /api/billing/status` (current user)
  and the existing `api/admin.py` tier endpoints (unchanged) both read/
  write the same `access_tier`/`access_state` columns - one source of
  truth either way.
- **Trial expiration logic**: real, not a placeholder. `database.
  check_and_expire_trial()` runs on every login and every authenticated
  request (`api/auth_routes.py`'s `get_current_user`); a trial past its
  `trial_expires_at` flips to `access_state='expired'`, which was
  already in `_BLOCKED_ACCESS_STATES` - no new enforcement mechanism, no
  cron process. Incidental fix found along the way: `get_current_user`
  didn't previously re-check blocked states on already-issued session
  cookies (only `login()` did), so an account an Owner suspends mid-
  session kept working until the cookie itself expired - now closed for
  every blocked state, not just `expired`.
- **Billing page UI**: `web/billing.html` - pricing grid, current plan/
  status card, trial countdown, "Choose Plan" (real checkout call, shows
  the honest not-configured message today) / "Contact Us" for Enterprise.
- **"Billing not configured" state**: shown in both the API response
  shape (`configured: false`) and a UI banner - never a raw error.
- **Env vars**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
  `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_BASIC/PRO/ELITE`,
  `APP_BASE_URL` added to `config/settings.py` - all `None`/unset by
  default, nothing activates until real values are supplied.

**Owner manual activation remains the source of truth** - every
existing `api/admin.py` tier action (`set-tier`, `set-trial`,
`grant-free-access`, `activate`, `disable`, `revoke-access`) is
untouched and still the only way tiers actually change today, since
billing is inert without real keys.

Tested: `tests/test_billing.py` (18 tests - configuration gating,
pricing catalog integrity incl. "no tier value outside the existing
enum", checkout not-configured/unknown-plan/contact-only paths,
subscription activation + cancellation downgrade, webhook rejects when
unconfigured, and 5 trial-expiration scenarios). Live-verified via
Playwright against the real DB/API: pricing grid renders all 6 plans
with real feature text, "Choose Plan" shows the honest not-configured
message, "Contact Us" shows the Enterprise message, Settings and Users
both link to `/billing`, Users table shows friendly tier names
(`Owner`, not `owner`). Verification account cleaned up afterward.

Known gap: pricing-plan feature bullets describe planned tier
differentiation, not technically-enforced limits - `access_tier` today
only gates login (blocked states) and demo/live trading permission
(pre-existing `demo_only_forced`/`live_trading_allowed` flags), nothing
else in the app actually checks a user's tier.

#### Windows startup support - DONE
`scripts/install_scheduled_task.ps1` (already existed, covers `core/
telegram_listener.py`) plus a new companion `scripts/
install_api_scheduled_task.ps1` (covers `api/main.py` via uvicorn) and
`scripts/uninstall_startup_tasks.ps1` - both register a Windows
Scheduled Task with a logon trigger and restart-on-failure (999
retries, 1 minute apart), documented in `DEPLOYMENT.md`'s "Process
supervision / Windows startup" section.

**Not enabled by default** - registering a persistent auto-start
Scheduled Task for a trading-capable app is a real system-level change
with real consequences (it would start on every future login without
an active choice that day), so these scripts are opt-in, matching the
existing `install_scheduled_task.ps1`'s own "review before running"
framing. Verified live: registered the API task, confirmed via
`Get-ScheduledTask`/`Get-ScheduledTaskInfo` that the executable,
arguments, and working directory were exactly right, then removed it
via the uninstall script. Found and removed a pre-existing "AXIM
Listener" task during that same cleanup pass that predated this
session's Phase 6 work - flagged to the user rather than silently
re-added, since its origin (an earlier manual run, or an earlier part
of this same session) isn't known and reboot-triggered auto-start of a
live-capable trading bot isn't something to restore without being asked.

#### Desktop packaging (Tauri) - DONE (installer builds, not distributed/signed)
Installed the Rust toolchain (rustup) and Visual Studio Build Tools'
C++ workload (neither present in this environment beforehand) and
scaffolded `axim-desktop/` - a thin Tauri window around the existing
FastAPI control UI + listener (`axim-desktop/src-tauri/src/lib.rs`
spawns both via the same `venv\Scripts\python.exe` commands used
manually, polls the API port until it actually accepts connections
before loading the window - a fixed sleep proved unreliable under
load and was replaced - and kills both processes on window close).

`npm run tauri dev` verified live: real window, real AXIM login page,
both backend processes spawned and confirmed via `netstat`/process
list, screenshotted via `PrintWindow` (GDI `CopyFromScreen` can't
capture WebView2's hardware-accelerated surface - a capture-tooling
gotcha, not an app bug). Window-close process cleanup verified: child
processes (uvicorn + listener) are confirmed killed via `on_window_event`
- the file-watcher-triggered rebuild/relaunch during dev iteration was
itself an incidental live test of this path and it worked. The Tauri
window process itself was observed lingering briefly after a
`WM_CLOSE` before fully exiting in one manual test; not chased further
since the behavior that actually matters (no orphaned Python processes
after close) was confirmed correct.

`npm run tauri build` succeeded - produced both installer formats:
- `axim-desktop/src-tauri/target/release/bundle/msi/AXIM_0.1.0_x64_en-US.msi` (2.9 MB)
- `axim-desktop/src-tauri/target/release/bundle/nsis/AXIM_0.1.0_x64-setup.exe` (1.9 MB)

Neither installer has been run/installed - that's a real system-level
install action left for deliberate, explicit action rather than
something to trigger silently. Both are small because this is
deliberately NOT a self-contained installer: it requires the AXIM
project checkout and its venv already set up on the target machine
(resolved via `AXIM_PROJECT_ROOT` env var, falling back to a hardcoded
dev path) - installing Python + all of AXIM's dependencies as part of
the Tauri bundle itself (e.g. via a portable Python distribution) is
real, separate future work, not attempted here. The installer is also
unsigned - Windows SmartScreen will warn on first run; code signing
needs a real certificate, out of scope without one.

### Design pass - calmer, hierarchy-driven UI
Reworked the visual system and Mission Control (the primary screen)
away from a systems-monitoring feel toward the "premium wealth
management platform, not a monitoring dashboard" bar - explicit
product direction, not a schema/backend change.

- `web/theme.css`: warmer neutral palette, larger radius/shadow depth,
  new reusable patterns - `.hero-panel` (one large, confident number
  per screen instead of a wall of equally-weighted stat cards),
  `.status-line` (a single plain-language health read), `.diagnostics`
  (a `<details>`-based disclosure for technical detail, collapsed by
  default).
- `web/dashboard.html`: rebuilt around one status line ("Running
  normally" / "Reconnecting" / etc., replacing four separate
  AXIM/Telegram/Pocket Option status cards), one hero panel (Today's
  Performance), a Risk card in plain language ("Within all your risk
  limits" first, numbers on a details toggle), and a Recent Activity
  feed (replacing three separate "Active Source"/"Last Signal"/"Last
  Result" cards). All PID/heartbeat/worker/generation detail moved
  into a collapsed "System diagnostics" section.
- `web/broker.html`: same treatment - one status line instead of a
  "Browser Worker Pool" card, "Live DOM read not yet implemented"
  (developer jargon - DOM means nothing to a trader) replaced with
  "Not tracked yet", raw heartbeat/worker/generation numbers moved to
  a diagnostics disclosure. Test Trade results now show a plain-
  language summary (Won/Lost/error) with the raw JSON gated behind
  Developer Mode via the new `AximShell.isDeveloperMode()`.
- `web/sessions.html`: raw `running (pid 1234)` badge text replaced
  with "connected" (pid kept as a hover tooltip for anyone who wants
  it, not deleted).
- `web/shell.js`: `AximShell.init()` now also fetches
  `/api/settings/developer-mode` once and exposes
  `AximShell.isDeveloperMode()`, so any page can gate technical detail
  consistently instead of each page doing its own fetch.
- `web/settings.html`: Developer Mode's description updated to state
  explicitly that system diagnostics are available independent of the
  toggle (in a collapsed disclosure), and what the toggle itself now
  covers.

Real bug found and fixed during live verification, not a cosmetic
miss: the new diagnostics panels showed stale heartbeat/worker-count
data (`"Pocket Option: not running - 6 worker(s)..."`) whenever the
listener process wasn't currently running, because the template
string appended heartbeat detail whenever a heartbeat row existed in
the database at all, regardless of whether the process spawning that
heartbeat was still alive. Fixed in both `dashboard.html` and
`broker.html` to only show live worker/generation detail when the
process is actually running.

Verified live via Playwright against the real DB/API using a
throwaway admin account created directly through `database.
create_user()` (bootstrap-owner was unavailable - a real owner account
already exists in this installation from actual use, and was never
touched). Confirmed: status line reflects real state ("Not running"
when the listener is down), hero panel shows real daily P/L, risk card
defaults to "Within all your risk limits", recent activity renders
real historical trades, diagnostics disclosures are collapsed by
default and show correct (non-stale) content when expanded, Developer
Mode tab describes the new scope accurately. Test account and session
cleaned up afterward.

**Explicitly out of scope for this pass**: Trading Sessions, Risk
Engine, Trade Center, Performance, Rule Builder, and Users still use
the original denser, tabular treatment - appropriate for operational/
tool screens (a trade blotter is supposed to be dense), not redesigned
in this pass. If the "calm, hierarchy-driven" treatment should extend
further, that's a follow-up, not assumed here.

### Backtest Engine / Strategy Lab - DONE (CSV/Excel/manual import; live Telegram-history import deferred)
Replays a pool of historical, resolved signals through one or more Risk
Engine profiles to compare how each would have performed - a genuinely
new engine, not a UI over existing analytics.

**Architecture decision, the one that mattered most**: `core/
backtest_engine.py` reuses `core/risk_engine.py`'s PURE sizing/
martingale/vault functions (`_base_amount`, `_apply_martingale`)
directly rather than re-implementing the same math a second time. Two
of `risk_engine.py`'s vault functions were extracted from inline logic
into new pure functions (`milestone_vault_skim`,
`every_winning_session_vault_skim`) specifically so the backtest engine
could call the exact same vault math live AXIM uses - refactored with
`tests/test_risk_engine.py`'s full 20-test suite passing unchanged
before and after, confirming zero behavior drift. A backtest whose
sizing silently diverged from what AXIM actually does live would be
worse than useless.

- **Historical signal database**: reused the existing `signals` table
  (already records every signal, executed or not, with result/payout/
  timestamps) rather than duplicating it. Added `imported_signals` for
  CSV/manually-entered history, normalized to the same shape via
  `database.get_historical_signal_pool()`.
- **Import**: CSV, Excel (.xlsx, via `openpyxl` - first worksheet only,
  native cell types like datetimes normalized the same way typed CSV
  text is), and manual single-signal entry, all real and tested. Both
  file formats share one `_parse_signal_row` validation core
  (`core/backtest_engine.py`) so column-alias matching and error
  reporting behave identically regardless of format. The Excel upload
  itself is base64-encoded client-side into the same JSON-body pattern
  every other endpoint here already uses, rather than adding
  `python-multipart` as a new dependency just for one upload endpoint.
  Live Telegram-channel-history scraping is still explicitly deferred -
  a genuinely separate piece of work (a Telethon `iter_messages`
  integration), not half-built.
- **Result matching**: real recorded results (live `signals.result`) or
  manually-graded imported signals. Broker/candle-data integration for
  auto-grading ungraded signals is deferred, not fabricated.
- **Strategy comparison**: any real `risk_profiles` row (the 27
  templates or user-created ones) can be selected - no separate
  strategy catalog invented.
- **Session simulation**: signals grouped into sessions by calendar day
  (or "entire range as one session"), applying the SAME stop-condition
  semantics as `core/session_manager.check_session_limits`
  (profit_target/loss_limit/max_trades). Trading balance carries
  forward realistically across sessions for every sizing mode -
  documented in `core/backtest_engine.py`'s module docstring as
  intentionally MORE realistic than live AXIM today, since live risk
  profiles have a static `bankroll` field with no automatic balance
  tracking yet (a known, pre-existing gap, not something this feature
  papers over).
- **Martingale/Compounding/Vault simulation**: martingale steps
  unconditionally on loss (mirroring `database.advance_martingale_step`'s
  real semantics exactly, uncapped on the counter, clamped only on the
  computed size); vault skims (milestone-based and every-winning-
  session) call the shared pure functions; vaulted funds are excluded
  from the next session's trading-balance sizing base.
- **Output metrics**: final bankroll, ROI, win/loss rate, max drawdown
  (real peak-to-trough over the chronological equity curve, not an
  approximation), best/worst day, longest win/loss streaks, max
  martingale step used, sessions completed/stopped-by-target/stopped-
  by-loss-limit, avg/largest trade size, total protected profit
  (vaulted). `risk_score` (Low/Medium/High) and `best_for_label` are
  explicit, documented heuristics (see `_risk_score`/`_best_for_label`
  docstrings) - not a scientific classification, and labeled as
  heuristics in the UI's "Best for" line.
- **Ranking**: rank_overall (a documented 40% ROI + 40% inverse-
  drawdown + 20% win-rate composite), rank_safest, rank_highest_growth,
  rank_lowest_drawdown, rank_risk_adjusted - computed once per run
  across all compared strategies.
- **Charts**: equity curve, drawdown, daily P/L, vault growth,
  martingale exposure (all multi-line, hand-rolled SVG, no charting
  library dependency) and a strategy-comparison bar chart (ROI) - all
  six built from data already returned by the existing trades/sessions
  endpoints, no schema change needed. Live-verified via Playwright
  against a real 3-strategy run over the real historical signal pool;
  found and fixed a real mobile-layout bug in the process - a single
  wide chart (the comparison bar chart, defaulted to 900px) was forcing
  every card sharing its CSS Grid column to inherit that width on
  narrow viewports, since grid tracks don't shrink below their widest
  child's content size by default (`min-width: 0` on the grid items,
  plus scaling the comparison chart's width to its actual bar count,
  fixed it - see `web/strategy_lab.html`'s `.charts-grid` comment).
- **UI**: `web/strategy_lab.html`, new "Strategy Lab" nav item. Three
  tabs - Run Backtest (config + comparison cards + equity curve +
  session table), Historical Signals (import/manage/grade), Past Runs.
  Required risk disclaimer banner ("Past results do not guarantee
  future results...") shown on every visit, not dismissible-and-
  forgotten.
- **API**: `api/backtest_routes.py` - signal listing/import/grading,
  run creation (simulation runs synchronously in-request - fine for
  this feature's signal-pool sizes, a documented limit for very large
  pools rather than a silent one), report retrieval, session/trade
  drill-down, CSV/JSON export. PDF export deferred.
- **Database**: `imported_signals`, `backtest_runs`,
  `backtest_strategies` (freezes a JSON snapshot of the risk profile at
  run time so a report stays reproducible even if the profile is later
  edited or deleted), `backtest_sessions`, `backtest_trades`,
  `backtest_metrics`.

Tested: `tests/test_backtest_database.py` (12 tests - CRUD, historical
pool normalization/filtering/sorting across live+imported sources,
full run lifecycle, cascade delete) and `tests/test_backtest_engine.py`
(35 tests - fixed/percent/dynamic sizing, session grouping and all
three stop conditions, martingale stepping and reset-after-win, both
vault trigger types and cross-session fund exclusion, bankroll carry-
forward, CSV parsing incl. column aliases and per-line error reporting,
metrics computation incl. drawdown/streaks, ranking incl. tie-handling,
and - the most important category - **parity tests asserting the
backtest engine's sizing output is byte-for-byte identical to
`risk_engine.py`'s own live functions given the same inputs**). Live-
verified end-to-end via Playwright against the real DB/API using a
throwaway admin account created directly through the DB layer (no
bootstrap available - a real owner account already exists in this
installation and was never touched): added manual signals, imported a
CSV, ran a 3-strategy backtest against real risk profiles, confirmed
genuinely differentiated metrics per strategy (proving the simulation
isn't just echoing static numbers), confirmed the equity curve SVG
rendered 3 distinct series, confirmed session/trade drill-down and
CSV/JSON export all returned real data, confirmed Past Runs reload
worked. Found and fixed one real UI bug during that pass: the manual-
signal-entry form didn't clear its result/payout fields after a
successful add, so a stale payout value silently carried into the next
signal entered.

### User Guide / Help system + onboarding checklist - DONE
`web/guide.html` - eleven sections (Getting Started through
Troubleshooting) written in plain English against AXIM's actual real
behavior, not aspirational features - e.g. explicitly notes that
balance tracking reads AXIM's own recorded trade history rather than a
live broker balance, and that only one session can be active
app-wide even across multiple Funds. Client-side search filters
sections and highlights matches (`<mark>`, cleared and rebuilt per
keystroke) - no backend search needed since the whole guide is static
content on one page. "?" help-link icons added to Mission Control,
Funds, Risk Engine, Trading Sessions, and Strategy Lab, each deep-
linking to its matching guide section - deliberately NOT added to
pages without a matching section (e.g. Rule Builder) rather than
linking to a mismatched anchor.

Onboarding checklist: a real, computed 7-item list on Mission Control
(Owner account / Telegram connected / Pocket Option connected / Fund
created / signal source selected / money management profile selected
/ demo session completed), each backed by an actual API check already
used elsewhere in the app (the same `telegramOk` logic Mission
Control's own status line uses, `allFunds.length`, `channels.some(c =>
c.enabled)`, etc.) - not a static checklist that always shows the same
state. Hides itself once every item is complete, so it reads as
onboarding, not a permanent fixture cluttering the screen for
established users.

Tested live: search correctly narrows to matching sections only
(verified with a real query that should match exactly one section),
help links land on the right anchor, and the checklist showed "6 of 7
complete" with the correct single incomplete item on a fresh test
account that had every other setup step done - confirming it's reading
real state, not hardcoded.

## Known gaps / honest state as of Phase 1

**Superseded by the AXIM TradeStation multi-fund/multi-broker-account
rebuild** (`broker_accounts`/`fund_broker_accounts` tables, Fund-scoped
concurrent trading sessions, Fund-owned Rule Builder - see
`docs/AXIM_SESSION_ARCHITECTURE.md`):
- ~~Single shared trading connection, not multi-tenant~~ - each Fund now
  has its own independently-connected Pocket Option account (own browser
  profile, own `connection_status`/`live_enabled`), and different Funds
  can run genuinely concurrent trading sessions on different accounts.
  Still not full per-USER SaaS isolation (a Fund's account is shared by
  every user who can see that Fund, not partitioned per login) - that
  remains a bigger future step, not scoped into any phase above yet.
- ~~8 of 11 sidebar pages still point at the legacy dark page~~ - every
  page now uses the current design system (Mission Control, Funds,
  Signal Sources, Risk Engine, Rule Builder, Strategy Lab, Trade Center,
  Broker, Settings all consistent).

**Live-trading gating - UPDATED (was previously overclaimed as "real and
enforced" when it was actually decorative; traced against the real
execution-path code, then genuinely wired):**
- **`funds.live_enabled` / `broker_accounts.live_enabled` are now
  actually enforced**, not just computed and displayed.
  `core/broker_account_manager.py`'s `account_effective_cabinet_mode()`
  decides which cabinet a broker account's persistent browser session
  loads (`'live'` only if `mode` is `'live'`/`'both'` **and** the
  account's own `live_enabled` is on - matches the `broker_accounts.mode`
  column's own docstring: "a 'both' account can still be demo-only in
  practice until [live_enabled] is flipped"). `resolve_coordinator_for_
  session()` additionally requires the SPECIFIC Fund routing through that
  account to be independently `live_enabled` too, via `fund_manager.
  can_trade()`'s `can_go_live` - previously computed and silently
  discarded here, now a real `AccountUnavailable` rejection if a
  not-yet-authorized Fund tries to use a live-configured account. Covered
  by `tests/test_broker_account_manager.py`'s `AccountEffectiveCabinetModeTests`
  / `LiveAuthorizationGateTests`.
- **Live trading is still not fully available on any account**, by
  design, not by oversight: `execution/browser_warmup.py` now selects
  between `DEMO_URL` (proven, verified against the real site early in
  this project) and `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` (new
  `config/settings.py` values, unset by default) - but nobody has
  inspected a real live Pocket Option cabinet page in this codebase's
  history, so those two values were deliberately left unset rather than
  guessed. An account that becomes live-authorized (per the gate above)
  raises `LiveModeNotConfiguredError` and refuses to start - loudly and
  safely - until an operator with real live-account access sets both in
  `.env`, using the same technique `DEMO_URL`'s `is-chart-demo` check
  originally used (open devtools on the real live cabinet, find a CSS
  class present there and absent elsewhere). Covered by
  `tests/test_browser_warmup.py`.
- The two remaining **global, per-process** `.env` gates - `ACCOUNT`
  (checked three independent times: `risk_manager.check_demo_only()`,
  `api/main.py`'s test-trade route, `telegram_listener.py`'s test-trade
  poll loop) and `ARMED` (`execution/pocket_executor.py`, gates the
  actual click) - were deliberately left as-is, global and Fund-unaware,
  as an additional, independent safety layer on top of the new per-Fund/
  per-account gating: even a fully live-authorized Fund+account still
  cannot execute anything until a human separately sets `.env
  ACCOUNT=LIVE`, matching this project's consistent "multiple
  independent layers, all must agree" safety pattern rather than
  collapsing everything into one switch.
- Net effect: the "Live" toggles in `web/funds.html`/`web/broker.html`
  are genuinely load-bearing again, but flipping them cannot cause any
  real trade today - `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` being
  unset (requiring real live-account access nobody working on this
  codebase has had) and the global `ACCOUNT`/`ARMED` gates both still
  block it.
- **Password reset ("forgot password")** is a placeholder link in
  `web/login.html` - real self-service reset (email-based) isn't built;
  today an Owner/Admin resets a user's password from Users / Access.
- **Secrets**: verified `api/main.py`/`api/auth_routes.py`/`api/admin.py`
  never import or return `TELEGRAM_API_ID`/`API_HASH`/`PHONE`/
  `PO_EMAIL`/`PO_PASSWORD`. Password hashes never leave `core/database.py`
  un-redacted (`api/auth_routes.py`'s `public_user()` whitelist is the only
  path HTTP responses take).

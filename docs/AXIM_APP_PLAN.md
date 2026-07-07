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

**Also not done:** per-trade "require confirmation before execution" in
Live mode is stored on the session (`require_confirmation`) but not
enforced - the execution engine doesn't pause for a blocking UI
confirmation mid-signal, which would be a real architecture addition
(and its own live-risk conversation) rather than a small feature.
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

### Phase 6 - Packaging, Stripe
Not started. Desktop packaging (Tauri), Windows startup support,
and payments are explicitly deferred - the access-tier/access-state
schema built in Phase 1 is ready to support a Stripe integration later
without another schema migration. Backup/restore itself is no longer
"not started" - see the Settings page section above.

## Known gaps / honest state as of Phase 1

- **Single shared trading connection, not multi-tenant.** Every user
  account controls/views the SAME Telegram session and Pocket Option
  browser - there is no per-user broker isolation. The admin panel's
  "user's Telegram connection status" / "Pocket Option status" /
  "trade count" / "P/L summary" fields honestly report the shared
  connection state and `None` for the not-yet-real per-user trade
  count/P&L, rather than fabricate numbers. Real per-user isolation is a
  bigger future SaaS step, not scoped into any phase above yet.
- **Demo/live toggle still not wired to actually flip `ACCOUNT`** -
  same deliberate hold from `docs/AXIM_UI_PLAN.md`, pending an explicit
  decision on how live-trading activation should work per-user.
- **8 of 11 sidebar pages still point at the legacy dark page** - see
  Phase 1 status above. Functionally complete, visually inconsistent
  with the new theme until Phases 2/4/5 relocate them.
- **Password reset ("forgot password")** is a placeholder link in
  `web/login.html` - real self-service reset (email-based) isn't built;
  today an Owner/Admin resets a user's password from Users / Access.
- **Secrets**: verified `api/main.py`/`api/auth_routes.py`/`api/admin.py`
  never import or return `TELEGRAM_API_ID`/`API_HASH`/`PHONE`/
  `PO_EMAIL`/`PO_PASSWORD`. Password hashes never leave `core/database.py`
  un-redacted (`api/auth_routes.py`'s `public_user()` whitelist is the only
  path HTTP responses take).

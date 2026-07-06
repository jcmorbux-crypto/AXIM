# AXIM App Plan - Commercial Product Build

Product name: **AXIM**. Product type: Telegram Signals Copier +
Session-Based Trading Platform + Bankroll/Risk Engine. Full spec as given
by the product owner; this document tracks what's built vs. planned.
`docs/AXIM_SESSION_ARCHITECTURE.md` remains the detailed spec for the
Trading Sessions engine (build order item 3 below) and is not duplicated
here.

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

### Phase 2 - Telegram account linking, channel discovery, source manager, signal inspector
Not yet started. Existing `core/telegram_channels.py` (dedicated
`axim_ui_session` Telethon session) already does dialog sync; this phase
adds:
- In-app API ID/API Hash/phone/verification-code entry (currently
  requires running `python core/telegram_channels.py` once, interactively,
  from a terminal - not yet wrapped in the UI)
- Encrypted-at-rest storage for the Telegram session file
- Source-type classification per channel (Passive/Bot Command/Group/
  Manual Review) - new `ui_channels` columns
- Per-channel win rate/P&L, priority, last message/last parsed signal,
  recent-messages viewer
- Signal Inspector page (light theme) - the existing `/api/parse-test`
  endpoint already does the real parsing; this phase adds the
  Approve/Reject/Create-Parsing-Rule/Save-Rule-for-Channel actions, none
  of which exist yet (channel-specific parsing rules are a known gap
  flagged back in `docs/AXIM_UI_PLAN.md`)

### Phase 3 - Trading Sessions
Full spec in `docs/AXIM_SESSION_ARCHITECTURE.md`. Not started. Suggested
order there: session/profile DB schema -> session-scoped risk checks in a
new `core/session_manager.py` -> passive-channel sessions end-to-end ->
interactive Telegram-bot signal sourcing -> the martingale/compounding/
vault fields below get attached to session profiles.

### Phase 4 - Money Management Center, Martingale Manager, Compounding Engine, Profit Vault
Not started. Existing `ui_settings`-backed money management (Phase 2 of
`docs/AXIM_UI_PLAN.md`, still live at `/legacy`) is a single global
profile with fixed/percent sizing only - no martingale, no compounding,
no vault, no saved/named profiles. This phase replaces "one global
settings row" with `money_profiles` + `martingale_settings` +
`compounding_settings` + `profit_vault` tables, profile CRUD, and the
starter template names (Capital Shield, Vault Builder, Snowball Mode,
etc. - full list in the product spec) as seed data.

### Phase 5 - Live Trades, Statistics, Logs, Pocket Option status page
Partially exists today (dark theme, `/legacy`): live trades table with
screenshots, daily/weekly stats, recovery health, latency percentiles,
Pocket Option heartbeat status. This phase re-themes those into their own
light-theme pages (`web/trades.html`, `web/statistics.html`,
`web/pocket-option.html`) and adds the analytics not yet built (profit by
channel/asset/strategy/session, martingale performance, compounding
growth, drawdown, best/worst channel, best time of day, streaks) plus a
real Logs page reading from `admin_actions` + the existing log files
(`logs/lifecycle.log`, `logs/ui.log`, etc.) with the filter set from the
spec (date/severity/module/user/session/channel).

### Phase 6 - Packaging, Stripe
Not started. Desktop packaging (Tauri), Windows startup support,
backup/restore (scripts already exist: `scripts/backup_axim_state.ps1`),
and payments are explicitly deferred - the access-tier/access-state
schema built in Phase 1 is ready to support a Stripe integration later
without another schema migration.

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

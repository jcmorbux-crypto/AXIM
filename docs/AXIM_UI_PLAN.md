# AXIM UI Implementation Plan

## Goal
A usable desktop/web UI to control AXIM without editing code or `.env`:
Telegram source manager, signal parsing settings, money management,
Pocket Option connection panel, live dashboard, start/stop/pause controls,
and safety confirmations before anything live-money-adjacent.

## Architecture

- **Backend:** FastAPI (`api/`), a separate process from
  `core/telegram_listener.py` - not merged into it. The listener stays
  exactly as hardened/tested tonight; the API controls it through shared
  state, not by becoming part of it.
- **State store:** new tables in the existing `data/axim.db` (not JSON) -
  `ui_channels`, `ui_settings`, `ui_control_state`. Two processes (API +
  listener) need concurrent-safe access; SQLite already is that, JSON
  files aren't without extra locking work.
- **Telegram channel listing:** a second, dedicated Telethon session
  (`axim_ui_session`) - the running listener holds an exclusive lock on
  `axim_session.session`, so a live dialog list needs its own session,
  same pattern already used for `core/source_observer.py`. One extra
  interactive login, same account, done once.
- **Control surface:** the listener polls `ui_control_state`/`ui_channels`
  from the DB (a couple of extra cheap SELECTs per incoming message, not a
  new IPC mechanism). The API writes to those tables. Start/stop reuses
  the Windows Scheduled Task already registered
  (`scripts/install_scheduled_task.ps1`).
- **UI:** one self-contained HTML/JS file, no build step - same philosophy
  as the existing `dashboard/index.html`. Ships today; can become a real
  SPA or get wrapped in Tauri/Electron later without touching the backend.

## Phases

1. **Backend API + Telegram channel manager** (this pass)
   - DB tables: `ui_channels`, `ui_control_state`
   - `core/telegram_channels.py`: dedicated-session dialog sync
   - `telegram_listener.py`: reads `ui_channels` (enabled flag) instead of
     only the static `.env` `WATCH_CHANNELS`; seeded from it on first run
     so behavior doesn't regress. Checks `ui_control_state` before
     executing (pause/emergency-stop). Records `last_signal_at` per
     channel.
   - `api/main.py`: channel list/sync/enable-disable, status, pause/
     resume/emergency-stop, start/stop (via Scheduled Task)
   - `web/index.html`: channel manager + status/control panel

2. **Money management + Pocket Option panel - DONE (partially)**
   - `ui_settings` table + `GET`/`PUT /api/settings`: starting bankroll,
     fixed/percent trade sizing, max trade amount, daily loss limit, daily
     profit target, max trades/day, cooldown, consecutive-loss stop,
     minimum payout, duplicate-signal window - all wired into
     `risk_manager.py` dynamically (`_setting()`, falls back to the
     static `.env`-derived default), so a change takes effect on the very
     next signal, no restart. Verified live against the real running
     listener.
   - New risk concepts added: `check_max_trades_per_day` (off by default,
     0 - a genuinely new cap, unlike the existing per-hour one),
     `check_daily_profit_target` (the upside mirror of the existing
     drawdown breaker, off by default), `compute_trade_amount` (fixed vs.
     percentage-of-bankroll sizing, bankroll = starting_bankroll +
     lifetime realized P/L).
   - **Demo/live toggle: deliberately NOT wired to actually flip
     `ACCOUNT`.** The UI shows current demo/live status
     (`GET /api/pocket-option/status`), but flipping real-money trading on
     is exactly the kind of decision this project's whole safety
     discipline has treated as requiring a real, explicit conversation -
     not a checkbox with a confirm dialog. Held back pending that
     conversation rather than silently building the write-path.
   - Pocket Option panel: session/worker status via a real heartbeat
     (`ui_listener_heartbeat`, written every 30s by the listener, read by
     the API) - verified live (fresh, non-stale heartbeat after a
     restart). **Balance display: not yet implemented** - no DOM selector
     for it has been discovered/verified yet; the API/UI honestly report
     `null`/"not yet implemented" rather than fabricate a number. Manual
     reconnect currently restarts the whole listener process (stop+start)
     - a lighter-weight "just reconnect the browser" action would need a
     dedicated IPC path into the running process, not built yet.
   - Old, superseded plan text below kept for reference:
     read from the browser layer's own state

3. **Live dashboard + signal parsing tool - DONE**
   - `GET /api/dashboard`: reuses `trade_statistics.full_report()`,
     `timeline_report.generate_report()`, and
     `database.get_recovery_event_stats()` directly - not a
     re-implementation. Supersedes `core/dashboard_server.py` as the
     primary UI (that stdlib dashboard still runs standalone if wanted).
   - `POST /api/parse-test`: runs the real `parsers/signal_parser.parse_signal()`
     against a pasted message. Verified live with a real Go+-style
     message (`"Currency pair: EUR/NZD OTC\nSignal: BUY\nExpiration: S55"`)
     - correctly returned `asset: "EUR/NZD OTC"`, `direction: "BUY"`.
   - `GET /api/screenshots/{trade_id}` + `/{filename}`: screenshot viewer,
     strict filename allowlist (`prepared.png`/`clicked.png` only, closes
     off path traversal entirely rather than sanitizing an arbitrary
     path). Verified live: clicking a screenshot link in the Recent
     Trades table opens a real trade screenshot (confirmed order placed,
     Gold OTC Sell $1, 88% payout) in a modal.
   - `web/index.html`: Live Dashboard section (today/7-day/signal-handling
     stat cards, recovery health table, stage-transition latency table,
     recent trades table with WIN/LOSS/DRAW badges and screenshot links)
     + Signal Parser Test panel (textarea, Test Parse button, JSON result)
     + a click-to-zoom screenshot modal. All screenshot-verified against
     the real running listener's live data (135 closed trades in the
     last 7 days, 35.6% win rate, real recovery-event counts).
   - Noted in passing, not yet acted on: the real Pocket Option DOM does
     show the account balance in its top bar (confirmed via a captured
     trade screenshot: "Q1 Demo / USD 49,973.97") - a concrete, findable
     target for a future `execution/pocket_dom.py` balance-reading
     selector, but not implemented this phase (would need its own live
     verification against the trading-adjacent code, not rushed in here).

4. **Controls end-to-end + safety**
   - Start/Stop/Pause/Test-mode/Emergency-stop all wired to real effect,
     not just UI state
   - Confirmation dialog before flipping to live trading (`ACCOUNT=LIVE`),
     showing current risk settings
   - Secrets (`.env` values) never sent to the UI/API responses

5. **Package as desktop app**
   - Tauri wrapping the same web UI + a bundled Python backend, once the
     web version is fully featured - no UI rewrite needed for this step

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

2. **Money management + Pocket Option panel**
   - `ui_settings` table + endpoints for starting bankroll, fixed/percent
     trade sizing, max trade amount, daily loss limit, daily profit
     target, max trades/day, cooldown, consecutive-loss stop, demo/live
     toggle - wired into `risk_manager.py` so changes take effect without
     a restart
   - New risk concepts not yet in `risk_manager.py`: bankroll-percentage
     position sizing, daily profit target (stop-on-target, not just
     stop-on-loss), max trades/day (currently only per-hour exists)
   - Pocket Option panel: session/balance/worker status via a lightweight
     read from the browser layer's own state

3. **Live dashboard + signal parsing tool**
   - Port `core/dashboard_server.py`'s existing stats/timeline/recovery/
     recent-trades logic into FastAPI endpoints (supersedes the old
     stdlib dashboard rather than running two)
   - Test-parse tool: paste a sample message, see `parse_signal()`'s
     output live, using the exact real parser
   - Screenshot viewer for `logs/trades/*.png`

4. **Controls end-to-end + safety**
   - Start/Stop/Pause/Test-mode/Emergency-stop all wired to real effect,
     not just UI state
   - Confirmation dialog before flipping to live trading (`ACCOUNT=LIVE`),
     showing current risk settings
   - Secrets (`.env` values) never sent to the UI/API responses

5. **Package as desktop app**
   - Tauri wrapping the same web UI + a bundled Python backend, once the
     web version is fully featured - no UI rewrite needed for this step

# Troubleshooting

Common problems and what actually fixes them, drawn from real issues
hit while building and running this project - not a generic list.

## Install / startup

**`playwright install chromium` step was skipped, server crashes or
trades never open a browser.** This is a separate download from `pip
install -r requirements.txt` - easy to miss. Run it explicitly:
```powershell
playwright install chromium
```

**Server crashes immediately on a fresh install with a Telegram-related
error, before you've even linked Telegram in the UI.** Should not
happen on this Release Candidate (fixed) - Telegram credentials are now
read lazily only when actually needed, not at import time. If you see
this, you're likely on an older build; get the current package.

**`ModuleNotFoundError` or similar on startup.** The virtual environment
isn't activated, or dependencies weren't installed into it:
```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Telegram

**Wizard step 2 never receives the login code.** Check the phone number
format includes the country code (`+1XXXXXXXXXX`), and check Telegram's
own app/SMS for the code - it comes from Telegram directly, not AXIM.

**Channels/Signal Sources search shows nothing.** Your Telegram account
needs to already be a member of / have visibility into a chat before
AXIM can list it as a search result. Join the channel in Telegram
first, then re-sync.

## Pocket Option

**Wizard step 3 never reaches "connected."** The login window may need
you to click through it manually - check for a Chrome window that
opened behind your other windows (Alt-Tab to find it). There is no
automated login; a human must complete it once per broker account.

**A second Chrome window opens and fights the first one, with repeated
"Opening in existing browser session" errors in `logs/axim.log`.** This
means two listener processes are running against the same broker
account's browser profile at once - almost always caused by starting
the listener twice (e.g. once by hand, once via a Scheduled Task, or
once via AXIM Trader's local mode while a listener was already
running elsewhere). Only one should ever run per install. Stop the
extra one; AXIM Trader's local mode now checks for an
already-fresh listener heartbeat before spawning a new one, but a
manually-started terminal process isn't visible to that check.

## Running / operating

**Orphaned Chrome processes after stopping the listener.**
```powershell
powershell -File scripts\cleanup_axim_chrome.ps1
```
This only ever targets AXIM's own Chrome profile (`--user-data-dir`),
never any other Chrome window on the machine.

**The listener stopped and nothing brought it back.** If you started it
by hand in a terminal, closing that terminal (or the terminal crashing)
stops it with nothing watching to restart it. Run it under supervision
instead:
```powershell
powershell -File scripts\install_scheduled_task.ps1
```
This registers a Windows Scheduled Task using
`scripts\run_listener_supervised.ps1`, which restarts the listener on
any exit, clean or forced - not just Task Scheduler's own (unreliable
for forced-kill scenarios) restart setting.

**A risk threshold change (e.g. from the UI) doesn't seem to apply.**
UI-set thresholds (`ui_settings` table) take effect on the very next
signal, no restart needed - if it looks stale, confirm you're looking
at the right Fund/session, since some settings are per-profile.

**Port `8090` already in use.** Something else (a previous unclean
shutdown, or another AXIM instance) is already listening. Find and stop
it, or change `API_BIND_PORT` in `.env` and use that port instead.

## AXIM Trader (desktop / remote client)

**"Failed to start: could not reach ..." error on launch.** The
configured server address is wrong, the AXIM Server isn't running, or
(for a remote connection) Tailscale isn't connected on one or both
devices. Click "Change server settings" to re-enter the address; verify
the AXIM Server itself is reachable first (open its URL directly in a
normal browser from the same network).

**Local mode won't start on this device.** Local mode requires an
existing AXIM project checkout with its own `venv/` already set up on
*this* machine (see `INSTALL.md`) - it is not a bundled, self-contained
installer. If you're on a second device (a laptop), you almost
certainly want "Connect to a remote AXIM Server" instead, pointed at
your Mini PC.

## Still stuck

Check `logs/axim.log` (the unified log) and `logs/lifecycle.log`
(trade-pipeline-specific) - both are rotating files under `logs/`, and
almost every real issue leaves a trace in one of them.

# Installing AXIM Core (Release Candidate 1)

This gets the AXIM Core Server installed and running on a Windows
machine. It stops once the server is up and reachable in a browser -
for actually configuring your first Fund and firing a trade, see
`FIRST_TRADE.md` next. For the fastest possible path with no
explanation, see `QUICK_START.md` instead.

## What you need before starting

- A Windows machine to act as the **AXIM Server** - the machine that
  runs the Telegram listener and the real Pocket Option browser
  session. A "Mini PC" left running is the typical setup; it does not
  need to be powerful.
- **Python 3.12+** installed (`python --version` to check).
- A **Telegram account**, with API credentials from
  [my.telegram.org](https://my.telegram.org) → API development tools
  (you'll need the `api_id` and `api_hash` shown there).
- A **Pocket Option account**. Use the **demo** account unless you have
  already read `LIVE_CHECKLIST.md` and made a deliberate decision to go
  live - this is the single most important rule in this whole document.

## 1. Get the files onto the machine

If you received a packaged zip (`AXIM-Core-Server-v*.zip`), unzip it
anywhere - `C:\AXIM` is the assumed location throughout these docs, but
any path works. If you're working from a git checkout, you already have
this.

## 2. Set up Python

```powershell
cd C:\AXIM
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` downloads the actual browser AXIM drives
to open trades. This is a separate step from `pip install` - easy to
miss, and AXIM will fail at startup without it.

## 3. Create `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` in a text editor and fill in three values:

```
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1XXXXXXXXXX
```

Leave everything else as-is for now, especially:

- **`ACCOUNT=DEMO`** - a structural safeguard checked independently in
  multiple places (`risk_manager.check_demo_only()`, the browser
  startup check). AXIM refuses to run against a live cabinet while this
  says `DEMO`, full stop.
- **`ARMED=false`** - the master kill switch for whether a trade can
  ever actually be clicked, as opposed to prepared and logged. Leave
  this `false` until you have read `LIVE_CHECKLIST.md` in full.

Your Telegram channels and Pocket Option login are set up **inside the
app** in the next step, not by hand-editing this file.

## 4. Start the server

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Leave this running - it's the server process. Open
**http://127.0.0.1:8090** in a browser on the same machine.

You should see the AXIM login/bootstrap screen. That confirms the
install worked.

## 5. Run the test suite (optional but recommended)

```powershell
python -m pytest tests/ -q
```

Confirms the code you just installed actually works on this machine
before you rely on it - non-browser regression suite (parsing, risk
rules, sizing, orchestration). Should show all passing (one test is
intentionally skipped depending on configuration).

## Next step

Continue to **`FIRST_TRADE.md`** - it picks up exactly here (the login
screen open in your browser) and walks through the Setup Wizard:
creating your account, linking Telegram, connecting Pocket Option,
and firing one real demo trade.

## Running unattended (survives logoff/reboot)

Not required to get started, but worth doing once you're happy with a
manual run:

```powershell
powershell -File scripts\install_scheduled_task.ps1       # the Telegram listener
powershell -File scripts\install_api_scheduled_task.ps1   # the API/web UI
```

Both register genuine Windows Scheduled Tasks under your account with
automatic restart-on-failure - read the script before running it, same
as any system-level change. Full detail in `DEPLOYMENT.md`.

## Adding a second device (AXIM TradeStation Remote Client)

To monitor/control AXIM from a laptop over a private Tailscale network
(no public port, no public IP), install `AXIM TradeStation` on that
second device and choose "Connect to a remote AXIM Server" on first
launch. See `docs/AXIM_REMOTE_ACCESS.md` for the full Tailscale setup.

## If something goes wrong here

See `TROUBLESHOOTING.md`.

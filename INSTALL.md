# AXIM Installation & First-Run Guide

This covers getting AXIM running end to end: install → log in → connect
Telegram → connect Pocket Option → create a Fund → configure money
management → run a Demo session → (when you're ready) switch to Live →
monitor remotely. It supersedes the old CLI-first setup flow this
document used to describe - that flow still works underneath (see
"Engine/CLI layer" at the bottom), but the web app is how setup and
day-to-day operation actually happens today.

## Prerequisites

- Python 3.12+
- Windows (developed and tested on Windows; paths and process-management
  commands throughout the docs assume PowerShell)
- A Telegram account with API credentials ([my.telegram.org](https://my.telegram.org) → API development tools)
- A Pocket Option account (a demo account is sufficient, and is what
  this project is built and tested against - see "Switching to Live"
  before ever using a real-money account)

## 1. Clone and set up a virtual environment

```powershell
cd C:\AXIM
python -m venv venv
.\venv\Scripts\Activate.ps1
```

## 2. Install dependencies

```powershell
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` downloads the actual browser binary
Playwright drives - a separate step from `pip install`, easy to miss,
and AXIM will fail at startup without it.

## 3. Configure `.env`

Copy the existing `.env` structure (see `config/settings.py` for the
full list of variables it reads) and fill in your own values. At
minimum, the listener process needs real Telegram API credentials to
start at all:

```
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1XXXXXXXXXX

ACCOUNT=DEMO
ARMED=false
PREVIEW_ONLY=true
```

Everything else - which Telegram channels to follow, which Pocket
Option account(s) to connect, Funds, money management profiles - is
configured through the web UI once it's running (step 5), not `.env`.
`WATCH_CHANNELS` still works as a static allow-list if you set it, but
the Signal Sources page's channel manager is the live, no-restart-needed
way to do the same thing today.

**Do not set `ARMED=true` in `.env`.** This is a hard project
convention, not a suggestion - `ARMED` is the one switch that lets a
trade actually be clicked (as opposed to prepared-and-logged). See
`docs/AXIM_LIVE_READINESS_REVIEW.md` for the full reasoning.

**Keep `ACCOUNT=DEMO`** until you've deliberately worked through
"Switching to Live" below. `risk_manager.check_demo_only()` and
`BrowserWarmupService`'s startup check both independently refuse to
proceed if the account isn't in demo mode - a structural safeguard, not
just a config flag.

## 4. Verify with the test suite

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

This runs the automated (non-browser) regression suite. It does not
open a browser or touch Pocket Option. Expect all tests to pass (one is
intentionally skipped depending on `COOLDOWN_AFTER_LOSS_SECONDS`'s
configured value).

## 5. Start the API server and open the web UI

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Open `http://127.0.0.1:8090` in a browser. First run walks you through
an 8-step Setup Wizard (`/wizard`).

### First-run checklist

Each step is individually skippable and revisitable later from its own
page if you want to come back to it:

- [ ] **1. Owner account** - local-only authentication, no external
      account or billing required.
- [ ] **2. Connect Telegram** - a login code sent to your Telegram
      account, entered right in the browser (the same interactive login
      the old CLI flow needed a separate manual script for - no longer
      required).
- [ ] **3. Connect Pocket Option** - opens a real, visible Chromium
      window for you to log in through manually (Pocket Option has no
      documented safe programmatic login); AXIM detects success and
      keeps a persistent browser profile afterward, normally a one-time
      step per broker account.
- [ ] **4. Choose a starting Risk Profile** - ready-made templates
      covering position sizing, Martingale, Compounding, and Profit
      Vault - pick one (changeable anytime in Risk Engine) or skip to
      use global default sizing.
- [ ] **5. Select signal channels** - choose which of your synced
      Telegram chats AXIM should follow.
- [ ] **6. Create your first Fund and Trading Session** - a Fund name,
      a session name, and the session's stop conditions (profit
      target/loss limit/max trades, all optional/skippable).
- [ ] **7. Run a demo test trade** - places one real trade on your DEMO
      account through AXIM's actual live pipeline, so you can see the
      whole flow work end to end before relying on a real signal
      arriving. (Skipped automatically if the account is already in
      LIVE mode - not offered there for safety.)
- [ ] **8. Done** - Mission Control from here on.

### Demo validation procedure

Once a Demo session is running, confirm the real signal-to-trade path
end to end before trusting the system with anything:

1. Send (or wait for) a real signal from your connected source.
2. Signal Inspector shows the raw message and what the parser actually
   extracted from it - confirm asset/direction/expiry look right.
3. Trade Center shows the resulting trade once it executes (or the
   rejection reason if a risk rule blocked it - not a bug, the system
   working as designed).
4. Once the trade's expiry passes, confirm its result (win/loss/draw)
   is recorded and Performance reflects it.
5. Confirm the session's stop conditions actually work: either let it
   run to a stop condition, or use "Stop Session" (Mission Control or
   the Trading Sessions page) and confirm it stops requesting new
   signals and settles any trade already in progress.

If every step above produces what you expect, the pipeline is verified
working for your specific signal source and account - not assumed.

## Switching to Live

Do this deliberately, not as a default. Before considering it:

- Read `docs/AXIM_LIVE_READINESS_REVIEW.md` and
  `docs/AXIM_PRODUCTION_READINESS_REPORT.md` (both carry status banners
  pointing to what's current - read those banners first) for what's
  been validated and what hasn't.
- A Fund only trades Live if BOTH the Fund's own Live switch AND its
  attached broker account's Live switch are enabled - two independent
  gates, deliberately not one.
- Starting a Live session shows a confirmation modal disclosing the
  Fund, broker account, current balance, trade size, loss limit, and
  maximum Martingale exposure, and requires typing "START LIVE" - not a
  bare browser confirmation dialog.
- Emergency Stop (Mission Control, always visible) immediately halts
  signal processing and new executions and marks every active session
  stopped - available to any logged-in user, not just Owner/Admin, so
  anyone who spots a problem can act on it right away.

## Remote monitoring (Remote Client)

AXIM Server runs continuously on one machine (a Mini PC, typically) -
the entire web UI above is reachable from any other device on your own
private Tailscale network, without opening any public port. See
`docs/AXIM_REMOTE_ACCESS.md` for the full step-by-step setup (install
Tailscale, point a laptop's browser or the AXIM desktop app at the
server's Tailscale address). The Remote Client only ever monitors and
controls - trades always execute on the server.

## Running unattended (process supervision, backups)

See `DEPLOYMENT.md` for registering both AXIM processes (the listener
and the API) as Windows Scheduled Tasks that survive reboots and
restart automatically on a crash, plus the backup/retention script for
`data/axim.db` and session state.

## Live readiness checklist

Before increasing stakes or running unattended against a Live account,
work through `docs/AXIM_RELEASE_CHECKLIST.md` in full - it's the
current, actively-maintained source of truth for what's verified and
what's still an open or accepted-limitation item. `docs/AXIM_ROADMAP.md`
has the full dated history behind each entry if you want the reasoning,
not just the checkbox.

## Engine/CLI layer (what the web UI drives underneath)

Everything above is the recommended path. The underlying engine can
still be run and inspected directly if you want to understand or debug
what's actually happening:

```powershell
python core/telegram_listener.py
```

Runs until you stop it (Ctrl+C for a clean shutdown - see
`USER_GUIDE.md`'s "Stopping AXIM correctly" for why this matters more
than it sounds). This is the same process the web UI's Mission Control
Start/Stop controls and the Scheduled Task installer manage - the API
process (`api.main:app`) never runs trading logic itself, it only reads
and writes shared state in `data/axim.db` that this listener process
acts on.

See `USER_GUIDE.md` for day-to-day operation detail and `DEPLOYMENT.md`
for running everything unattended.

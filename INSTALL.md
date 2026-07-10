# AXIM TradeStation Installation

## Prerequisites

- Python 3.12+
- Windows (this build has been developed and tested on Windows; paths and
  process-management commands throughout the docs assume PowerShell)
- A Telegram account with API credentials ([my.telegram.org](https://my.telegram.org) → API development tools)
- A Pocket Option account (demo account is sufficient and is what this
  project is built and tested against)

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

`playwright install chromium` downloads the actual browser binary Playwright
drives - this is a separate step from `pip install`, easy to miss, and AXIM
will fail at startup without it.

## 3. Configure `.env`

Copy the existing `.env` structure (see `config/settings.py` for the full
list of variables it reads) and fill in your own values. At minimum:

```
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1XXXXXXXXXX

PO_EMAIL=your_pocket_option_email
PO_PASSWORD=your_pocket_option_password

WATCH_CHANNELS=your_signal_source_username_or_title

ACCOUNT=DEMO
ARMED=false
PREVIEW_ONLY=true
```

**Do not set `ARMED=true` in `.env`.** This is a hard project convention,
not a suggestion - `ARMED` is the one switch that lets a trade actually be
clicked (as opposed to prepared-and-logged). Every test in this project that
needed `ARMED=true` set it via `os.environ["ARMED"] = "true"` inside an
isolated Python process, never in the checked-in config. See
`docs/AXIM_LIVE_READINESS_REVIEW.md` for the full reasoning.

**Keep `ACCOUNT=DEMO`.** `risk_manager.check_demo_only()` and
`BrowserWarmupService`'s startup check both independently refuse to proceed
if the account isn't in demo mode - this is a structural safeguard, not just
a config flag, and switching it is a decision this document deliberately
does not walk you through.

## 4. First run

```powershell
python core/telegram_listener.py
```

On first run, Telethon will prompt for a login code sent to your Telegram
account (interactive - run this once in a terminal you can respond in, not
purely in the background). A session file (`axim_session.session`) is saved
afterward so subsequent runs don't need this.

You should see, in order:
```
AXIM Telegram Listener Starting...
Watching for chat titles containing: [...]
AXIM: starting persistent Pocket Option session...
browser_warmup: demo mode verified (is-chart-demo present)
...
AXIM: starting browser worker pool (N worker(s))...
AXIM: worker pool ready, running startup recovery...
Connected to Telegram
```

A visible Chromium window will also open and log into Pocket Option - this
is expected; AXIM drives a real, visible browser rather than a headless one.

## 5. Verify with the test suite

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

This runs the automated (non-browser) regression suite - parser, risk
rules, trade orchestration, worker pool logic. It does not open a browser
or touch Pocket Option. Expect all tests to pass (one is intentionally
skipped if `COOLDOWN_AFTER_LOSS_SECONDS` is configured to 0).

Live-browser tests (`tests/manual_click_test*.py`,
`tests/latency_benchmark.py`, `tests/production_stress_test.py`, etc.) are
manual, one-off scripts - run only when you explicitly want to drive the
real demo account, and never concurrently with a running
`telegram_listener.py` (they share the same persistent browser profile at
`sessions/pocket_browser` and cannot run at the same time).

## 6. (Optional) Control UI

A local web UI covers channel management and start/stop/pause without
editing `.env`. Needs its own, separate Telegram login the first time
(a second session, so it can list dialogs live even while the listener
holds its own session open):

```powershell
python core/telegram_channels.py           # one-time interactive login + first sync
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Then open `http://127.0.0.1:8090`. See `USER_GUIDE.md`'s "Control UI"
section for what it can do.

See `USER_GUIDE.md` for day-to-day operation and `DEPLOYMENT.md` for running
this unattended.

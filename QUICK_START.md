# Quick Start

The fastest path from a fresh unzip to a running server. No
explanation - see `INSTALL.md` for the reasoning behind each step, or
`TROUBLESHOOTING.md` if something here doesn't work.

```powershell
cd C:\AXIM
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
notepad .env
```

In `.env`, fill in:

```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+1XXXXXXXXXX
```

(Get these from [my.telegram.org](https://my.telegram.org) → API
development tools.) Leave `ACCOUNT=DEMO` and `ARMED=false`.

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
```

Open **http://127.0.0.1:8090** → create your Owner account → the Setup
Wizard walks you through Telegram, Pocket Option, your first risk
profile, channels, and your first Fund.

Next: **`FIRST_TRADE.md`** for the guided walkthrough of that wizard,
ending with one confirmed real demo trade.

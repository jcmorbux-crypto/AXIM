# AXIM TradeStation

AXIM watches a Telegram channel for trading signals, sizes each trade using
risk rules you control, and places trades on Pocket Option automatically -
via a real, visible browser session, not an API integration. Nothing trades
until you tell it to: every account starts in Demo mode, and going Live
requires deliberate, explicit confirmation at multiple levels.

```
Telegram message -> parse_signal() -> Risk Engine checks -> Pocket Option
  (select asset/expiry/amount, click) -> outcome tracking -> recorded in
  data/axim.db -> Mission Control / Performance / Strategy Lab
```

## Architecture

AXIM runs as a permanent server (the "AXIM Server") that owns every broker
and Telegram connection, executes every trade, and hosts the database - and
any number of Remote Clients (a desktop app today, with web/mobile clients
possible later) that monitor and control it without ever executing a trade
themselves. Remote access uses [Tailscale](https://tailscale.com) - no
public internet exposure by default. See `docs/AXIM_REMOTE_ACCESS.md`.

## Getting started

- **[INSTALL.md](INSTALL.md)** - prerequisites and first-time setup
- **[USER_GUIDE.md](USER_GUIDE.md)** - running AXIM and how the trade
  pipeline works
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - unattended/long-term operation,
  process supervision, backups
- **[docs/AXIM_REMOTE_ACCESS.md](docs/AXIM_REMOTE_ACCESS.md)** - connecting
  a Remote Client over Tailscale
- In-app: **Help & Guide** (search the running app's own help center for
  plain-language answers, no developer jargon)

## Capabilities

Mission Control, Funds, Trading Sessions, Trade Center, Strategy Lab,
Automation Studio (visual IF/THEN rules), Signal Sources, Broker Accounts,
Performance, Notifications, User Management, Settings - all documented in
the in-app Help & Guide, and kept in sync in real time across every
connected client.

## Project status

Pre-release (`0.9.0-dev`). `docs/AXIM_ROADMAP.md` has the full build
history and current state; `docs/AXIM_PRODUCTION_READINESS_REPORT.md` and
`docs/AXIM_RELEASE_CHECKLIST.md` document exactly what has and hasn't been
validated before enabling Live trading.

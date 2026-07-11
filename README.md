# AXIM TradeStation

AXIM watches a Telegram channel for binary-options trading signals, parses
them, runs them through a risk-rule pipeline, and (only when explicitly
armed) places the trade on Pocket Option via a real, visible browser -
then tracks the outcome once it closes. It's a private, single-operator
(or small trusted group) trading system, not a public SaaS product: the
control API binds to `127.0.0.1` by default, with remote access opt-in
over a private Tailscale mesh, never the public internet.

```
Telegram message -> parse_signal() -> risk_manager checks -> pocket_executor
   (select asset/expiry/amount, click) -> track_outcome (reads the Closed
   trades list once expiry has passed) -> recorded in data/axim.db
```

Multi-Fund architecture: Funds own broker accounts, risk profiles, and
trading sessions, so multiple bankrolls/strategies can run side by side
under one install. A guided Setup Wizard, multi-user auth with an Owner
admin panel, a Strategy Lab for backtesting historical signals, and an
Automation Studio (visual IF/THEN rule builder) all sit on top of that
same core pipeline.

## Getting started

**`docs/AXIM_SETUP_GUIDE.md`** is the current, authoritative path from a
fresh machine to a running server - start there. It covers the guided
first-run wizard (owner account, Telegram/Pocket Option connection, risk
profile, signal channels, first session), running unattended, and adding
a Remote Client on a second device.

Before relying on it for anything beyond deliberate demo testing, work
through:

1. `docs/AXIM_DEMO_VALIDATION_CHECKLIST.md` - confirms *this specific
   install* actually works end to end against the demo account.
2. `docs/AXIM_LIVE_READINESS_CHECKLIST.md` - the honest, evidence-based
   gate before ever considering real money. Read this before touching
   `ACCOUNT` or any broker account's `live_enabled` flag.

## Other docs

| Doc | Covers |
|---|---|
| `USER_GUIDE.md` | Day-to-day operation (older, single-connection-era detail - `AXIM_SETUP_GUIDE.md` reflects the current flow more accurately) |
| `INSTALL.md` | Low-level manual install steps (same caveat - superseded by the Setup Guide for the guided flow) |
| `DEPLOYMENT.md` | Running unattended: process supervision, backups, monitoring, resource sizing |
| `docs/AXIM_REMOTE_ACCESS.md` | Connecting a second device over Tailscale |
| `docs/AXIM_ROADMAP.md` | Development history - what was built, when, and why |

## Safety conventions

- `ARMED` (the switch that lets a trade actually be clicked) should never
  be `true` in a checked-in `.env` - only ever set per-process for a
  deliberate, watched test.
- `ACCOUNT=DEMO` and per-broker-account `live_enabled=false` are the
  defaults; going live is a deliberate, per-account decision, not a
  global flip.
- Every non-trivial change in this codebase is expected to be verified
  against real behavior (a real demo trade, a real browser, a real
  failure injected on purpose) - not just assumed correct from reading
  the code.

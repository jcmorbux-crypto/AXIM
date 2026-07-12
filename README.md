# AXIM Trader

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

## Getting started (Release Candidate 1)

Start here, in order:

1. **`INSTALL.md`** - get the server installed and running.
2. **`FIRST_TRADE.md`** - the guided Setup Wizard walkthrough, ending
   with one confirmed real demo trade. (`QUICK_START.md` if you want
   the condensed, no-explanation version of both.)
3. **`DEMO_CHECKLIST.md`** - confirm *this specific install* actually
   works end to end before relying on it for real signal volume.
4. **`LIVE_CHECKLIST.md`** - the honest, evidence-based gate before
   ever considering real money. Read this before touching `ACCOUNT` or
   any broker account's `live_enabled` flag.
5. **`TROUBLESHOOTING.md`** - if anything above doesn't come up right.

`docs/AXIM_SETUP_GUIDE.md`, `docs/AXIM_DEMO_VALIDATION_CHECKLIST.md`,
and `docs/AXIM_LIVE_READINESS_CHECKLIST.md` are the fuller versions of
2-4 above, with exact log lines, code references, and the full
evidence trail behind every claim - read those when you want the detail
or something needs debugging, not just the pass/fail summary.

## Other docs

| Doc | Covers |
|---|---|
| `USER_GUIDE.md` | Day-to-day operation (older, single-connection-era detail - `docs/AXIM_SETUP_GUIDE.md` reflects the current flow more accurately) |
| `DEPLOYMENT.md` | Running unattended: process supervision, backups, monitoring, resource sizing |
| `docs/AXIM_REMOTE_ACCESS.md` | Connecting a second device (AXIM Trader) over Tailscale |
| `docs/AXIM_CAPITAL_STRATEGIES.md` | The Capital Strategies (tm) catalog - Investment Houses, what's real vs. catalog-only |
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

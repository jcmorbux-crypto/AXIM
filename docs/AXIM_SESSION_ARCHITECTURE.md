# AXIM Session Architecture (Product Direction Change)

Supersedes the "always-on continuous listener" framing used everywhere
else in `docs/AXIM_UI_PLAN.md`. AXIM does not run forever in the
background reacting to whatever arrives - it runs **structured, bounded
trading sessions**:

```
Start -> Execute Signals -> Track Results -> Stop at Target or Stop at Risk Limit
```

This document is the product/architecture spec for that pivot. It does
not itself change any code - see "Impact on the existing codebase" and
"Suggested build order" at the bottom for what implementing this touches
and in what order.

## 1. Session Start

Before AXIM executes a single trade, the user configures a session:

- Strategy profile (a saved bundle of parsing rules, risk settings)
- Telegram signal channel(s) to follow this session
- Money management profile (see section 9)
- Profit target
- Loss limit
- Demo or Live confirmation
- (then) AXIM starts monitoring and executing signals for this session

## 2. Session Completion

A session ends - stops accepting/executing new signals - the moment any
one of these becomes true:

- Profit target reached
- Loss limit breached
- Maximum trades reached
- User manually stops the session
- Broker/Telegram connection fails
- Emergency stop triggered

Whichever condition fires first ends the session; the others are simply
never reached. All are terminal for that session - none of them auto-
restart a new session.

## 3. No Cooldown Requirement

**Changed as of this doc: cooldown-after-loss is no longer an
always-on-by-default risk rule.** AXIM keeps trading within an active
session after a loss - it does not pause automatically between losses.
A cooldown is only applied if the user explicitly configures one as a
custom rule for that session/profile.

Implementation note: `risk_manager.check_cooldown_after_loss()` already
treats a limit of `0` as fully disabled (see `core/risk_manager.py`) -
the static default in `config/settings.py` changed from `300` to `0` to
match this (done alongside this doc). The mechanism itself is untouched
and still available as an opt-in per-session/profile setting.

## 4. Bot-Generated Signal Sessions

Not every Telegram source is passive copy-the-message. AXIM must support
four distinct signal source types:

1. **Passive signal channels** - existing behavior: AXIM reads messages
   as they arrive and parses them. No AXIM-initiated messages.
2. **Interactive bot channels** - the source is a bot that only produces
   a signal when prompted. AXIM must send a configured trigger command
   (e.g. `/start`, `/signal`, `/next`, `Generate next trade`,
   `Begin session`) and wait for/parse the bot's reply.
3. **Manual signal input** - the operator pastes/types a signal directly
   into the UI (reuses the already-built `POST /api/parse-test` parsing
   path, but for real execution instead of a dry-run preview).
4. **Future AI-generated signals** - not built yet, but the source-type
   enum and session pipeline must not assume "comes from Telegram" is the
   only possibility, so this slots in later without another pivot.

## 5. Interactive Signal Bot Workflow

For a Bot Command Channel source, the per-signal loop is:

1. AXIM opens the selected Telegram bot/channel (already-connected
   Telethon client, not a new login)
2. Sends the configured trigger command
3. Waits for the bot's response (with a timeout)
4. Parses the signal from that response (same `parse_signal()` used
   everywhere else - the parser doesn't need to know or care whether the
   message was pushed or requested)
5. Executes the trade (same `trade_coordinator.handle_signal()` path)
6. Tracks the result (same outcome-tracking pipeline)
7. If the session is still active (no stop condition hit), requests the
   next signal - repeats from step 2
8. Stops requesting once the profit target is reached or the loss limit
   is breached (i.e., session completion, section 2, gates step 7 - not
   a separate mechanism)

## 6. Session Control Rules

The session-start UI must collect explicit answers to:

- How many signals per session? (maps to "max trades")
- Should AXIM request the next signal automatically? (bot-channel only)
- Should AXIM wait for the trade result before requesting the next
  signal? (bot-channel only - avoids requesting signal N+1 while signal
  N is still open)
- Should AXIM stop after profit target?
- Should AXIM stop after loss limit?
- Should AXIM stop after X total trades?
- Should AXIM require confirmation before each signal in Live mode?
  (a stricter, per-trade version of the existing Live-mode confirmation
  already built for Start - see `web/index.html`'s `confirmStart()`)

## 7. UI: Trading Sessions Page (new)

A dedicated page, separate from the existing single-listener Control
page (`web/index.html`). Contents:

- **Start New Session** form:
  - Session profile (saved or ad-hoc)
  - Signal source type: Passive Telegram channel / Interactive Telegram
    bot / Manual signal / AI signal engine
  - Trigger command field (shown only for Interactive Telegram bot)
  - Profit target, loss limit, max trades
  - Demo/Live toggle
  - Require-confirmation toggle
- **Active session** panel:
  - Progress (trades completed / max trades)
  - Current P/L vs. profit target and loss limit
  - Remaining target (how much more profit/loss before the session ends)
  - Stop Session button (graceful - finishes any open trade, stops
    requesting/accepting new signals)
  - Emergency Stop button (same semantics as the existing global one -
    immediate, no "finish the open trade" grace period)

## 8. Signal Source Manager (changes to the existing Channel Manager)

The existing Telegram Channel Manager (`ui_channels` table,
`web/index.html`'s channel table) gets a new per-channel classification,
independent of Telethon's own dialog `kind` (user/group/channel):

- **Passive Channel**
- **Bot Command Channel**
- **Group Chat**
- **Manual Review Channel**

For a channel typed as **Bot Command Channel**, additional per-channel
config:

- Trigger command
- Command interval behavior (fixed delay vs. wait-for-reply vs.
  wait-for-result - see section 6)
- Wait for result before next command (bool)
- Max requests per session
- Parse response preview (reuses the parse-test UI/endpoint against the
  bot's actual last reply, not a pasted sample)

## 9. Money Management (tied to sessions, not global)

Money management moves from "one global set of numbers"
(`GET`/`PUT /api/settings` as built in Phase 2) to **profiles** that a
session selects at start:

- Fixed amount
- Percent of active trading bankroll
- Martingale enabled/disabled
- Martingale steps
- Martingale multiplier
- Dynamic compounding
- Profit vault (skim realized profit out of the tradeable bankroll
  rather than letting it compound back in - protects gains from being
  risked again in the same or a later session)
- Loss limit
- Session risk cap

The existing global `ui_settings` values (Phase 2) become the *default*
profile; sessions can override any of them without touching the global
default other sessions/the always-on fallback still use.

## 10. Core Product Principle

> AXIM should not simply "run forever." AXIM should run structured
> trading sessions: Start -> Execute Signals -> Track Results -> Stop at
> Target or Stop at Risk Limit.

Every future UI/backend feature gets checked against this: does it fit
inside a session's start/run/stop lifecycle, or does it quietly
reintroduce "runs forever until someone remembers to stop it"?

---

## Impact on the existing codebase (not yet built - planning only)

This is a genuine architectural pivot, not an additive feature, because
the current system is built around one continuous process
(`core/telegram_listener.py`) with global pause/resume/emergency-stop/
test-mode state (`ui_control_state`) and global risk settings
(`ui_settings`) - there is no concept of "a session" anywhere in the
schema or the trading loop today. Concretely, this touches:

- **New DB tables**: `trading_sessions` (one row per session: profile,
  source config, targets/limits, status, started_at/ended_at,
  trades_completed, realized_pnl), `session_profiles` (saved
  start-config templates), likely a `session_id` column added to
  `signals` so trades attribute to the session that produced them.
- **`core/telegram_listener.py`**: currently a single always-running
  loop reading from `WATCH_CHANNELS`/`ui_channels` with no start/stop
  boundary other than the whole OS process. Needs a session-awareness
  layer: which signals belong to the active session(s), and enforcing
  session-level stop conditions (profit target/loss limit/max trades) in
  addition to the existing global ones.
- **`core/trade_coordinator.py`**: risk checks are currently all
  global/daily (`check_max_daily_loss`, `check_max_trades_per_day`,
  etc.). Session-scoped checks (this session's profit target, this
  session's loss limit, this session's max trades) are a new, parallel
  set of checks, not a replacement - a session's limits and the account's
  global daily limits should both apply.
- **New module**: something like `core/telegram_bot_trigger.py` for the
  interactive bot workflow (send command, await reply, timeout handling)
  - a genuinely new capability, not a variation on
    `core/telegram_listener.py`'s passive `event.chat_id` handling.
- **`core/risk_manager.py`**: cooldown default change (done, section 3).
  Money-management profile selection replaces reading `ui_settings`
  directly as "the" settings.
- **`web/index.html`**: new Trading Sessions page (likely its own HTML
  file, e.g. `web/sessions.html`, linked from the existing Control page,
  rather than growing one already-large single-page file further).

## Suggested build order

1. Schema: `trading_sessions` + `session_profiles` tables, `session_id`
   on `signals`.
2. Session-scoped risk checks in a new `core/session_manager.py`
   (mirrors `risk_manager.py`'s shape: small pure functions, one
   `RiskViolation`-style exception), wired into
   `trade_coordinator.handle_signal()` alongside (not instead of) the
   existing global checks.
3. Passive-channel sessions end-to-end first (start a session, trade
   normally within it, hit a stop condition, session ends) - this alone
   delivers sections 1/2/3/6/7/9/10 without touching Telegram-bot
   interaction at all.
4. Interactive Bot Command Channel workflow (sections 4/5/8) - the
   send-command/await-reply loop - as its own phase after passive
   sessions are proven live, since it's the riskiest new piece (a new
   kind of Telegram interaction the account has never done before).
5. Martingale/compounding/profit-vault money-management profile fields
   (section 9's more advanced options) - additive on top of the existing
   fixed/percent sizing already built.

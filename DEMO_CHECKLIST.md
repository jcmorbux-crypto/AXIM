# Demo Checklist

Run this after `FIRST_TRADE.md`, before trusting AXIM with real signal
volume - even on the demo account. This is the condensed, quick-pass
version; the full version with exact log lines, pass/fail criteria per
item, and the reasoning behind each one is
`docs/AXIM_DEMO_VALIDATION_CHECKLIST.md` (11 items) - read that one if
anything here fails or you want the detail.

- [ ] **Automated test suite passes**: `python -m pytest tests/ -q`
- [ ] **`.env` has `ACCOUNT=DEMO` and `ARMED=false`** - check this
      every time, not just once.
- [ ] **Listener starts clean**: watch `logs/axim.log` for `demo mode
      verified`, `worker pool ready`, and `Connected to Telegram` - no
      `refusing to proceed` messages.
- [ ] **Channel allow-list actually filters**: a message from a
      followed channel reaches the parser; a message from an unfollowed
      one is blocked, not silently allowed through.
- [ ] **One real end-to-end demo trade** progresses through a real
      `win`/`loss`/`draw`, with the Pocket Option demo balance changing
      to match (same as `FIRST_TRADE.md` step 7, done again deliberately
      here).
- [ ] **A risk rule actually rejects** a trade when triggered, not just
      logs a warning (e.g. set `MAX_TRADE_AMOUNT` unreachably low for one
      test signal, then restore it).
- [ ] **Restarting the listener mid-trade recovers cleanly** - an open
      trade still closes with a real result after a restart, not stuck.
- [ ] **Mission Control reflects reality** - no stale/zero numbers where
      real activity just happened.
- [ ] **Emergency Stop actually stops everything** - fire it from any
      active session, confirm every active session shows
      `stopped_emergency` and no further trades execute until manually
      resumed.
- [ ] **Multiple Funds stay independent** - if you're running more than
      one Fund, confirm a stop/loss-limit event on one Fund's session
      never touches another Fund's session.
- [ ] **Clean shutdown leaves no orphaned Chrome**:
      `scripts\cleanup_axim_chrome.ps1` after stopping the listener.

## Sign-off

- [ ] All items passed on: ______ (date) on this machine's own install.

Next: once this passes, `LIVE_CHECKLIST.md` covers what's needed before
ever considering real money - it is not a quick checklist by design.

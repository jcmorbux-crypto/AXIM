# Your First Trade

Picks up where `INSTALL.md` (or `QUICK_START.md`) leaves off: the AXIM
login screen open in a browser at `http://127.0.0.1:8090`. Walks
through the guided Setup Wizard to one real, confirmed demo trade.

## Step 0: Create your Owner account

The first time nobody has logged in, you'll see a bootstrap screen
instead of a login form. Enter an email and password - this becomes
the **Owner** account, with full admin rights. There is no separate
registration step; this is it.

## The Setup Wizard

After bootstrapping, the Wizard opens automatically. It has 8 steps:

**1. Owner Account** - already done in Step 0 above, confirmed here.

**2. Telegram** - link your Telegram account right in the browser
(enter phone number → enter the code Telegram sends you → done). This
uses its own session, separate from anything the listener process
might already be running, so linking never interrupts existing
activity.

**3. Pocket Option** - click **Add Account**, name it, then click
**Connect**. A real, visible Chrome window opens pointed at Pocket
Option's login page. **Log in by hand in that window** - this is the
one manual step nobody can automate for you (Pocket Option has no
supported programmatic login). The wizard polls until the window shows
`connected`, then you can close it.

**4. Risk Profile** (Money Management) - pick one of the built-in
starting templates (e.g. "Balanced Builder") or duplicate one to make
your own. This controls how much AXIM stakes per trade. Defaults are
conservative - you can change this any time later from the **Risk
Engine** page.

**5. Channels** - search for and follow the Telegram channel(s)/bot(s)
that send you trading signals. Type part of the name to filter your
already-synced chats. Only channels you explicitly follow here can
ever trigger a trade - this allow-list fails closed (nothing followed
= nothing trades).

**6. Session** - this step creates your first **Fund** (a named
portfolio with its own bankroll, broker account, and channels) and
starts your first trading session against it.

**7. Demo Test** - click to fire **one real test trade** through the
actual pipeline (not a simulation) against your connected demo
account. Watch it progress from `prepared` → `clicked` → `opened` →
`closed` with a real win/loss/draw result. This is the proof that
Telegram → AXIM → Pocket Option is wired correctly end to end.

**8. Ready** - done. You land on **Mission Control**, the main
dashboard, from here on.

## What "working" looks like

- The Chrome window from step 3 stays open and shows the Pocket Option
  demo cabinet (look for a "DEMO" indicator on the page itself).
- Mission Control's recent-signals list shows your step-7 test trade
  with a real result, not stuck at "pending."
- The Pocket Option demo balance in that Chrome tab changed by the
  trade's stake amount.

## Where to go next

- **Add more Telegram channels or a second Fund**: Signal Sources /
  Funds pages, same patterns as the wizard.
- **Explore Capital Strategies**: the nav's "Capital Strategies" page
  browses the full named-strategy catalog (Foundation, Titan
  Allocation, Apex Ascension, and more) - most are configured through
  the same Risk Engine page you used in step 4.
- **Before trusting it with real signal volume**: work through
  `DEMO_CHECKLIST.md`.
- **Before ever considering real money**: read `LIVE_CHECKLIST.md` in
  full - it is deliberately not a quick checklist.

## If something doesn't come up right

See `TROUBLESHOOTING.md`.

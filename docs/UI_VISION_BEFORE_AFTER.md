# AXIM Trader — Before / After Comparison

**Before** = production, `web/dashboard.html` et al., branch `master`, port 8090.
**After** = this program's prototype, `web_v2/*.html`, branch `ui-vision-upgrade`, port 8091 (preview only).

---

## Primary navigation

| | Before | After |
|---|---|---|
| Top-level destinations | 15 (Mission Control, Multi-Fund Manager, Trading Sessions, Signal Sources, Signal Inspector, Money Management Studio, Capital Strategies, Automation Studio, Strategy Lab, Trade History, Performance, Broker, Users, Logs, Settings) + Help/Guide | 6 (Home, Portfolio, Signals, Sessions, Strategy, Settings) |
| Grouping logic | Grew feature-by-feature over the project's history — each new capability got its own top-level nav item | Grouped by user mental model (P6): "what do I own," "where do trades come from," "what's happening now," "how should AXIM decide" |
| Capability lost | None — every production feature has a home in the new IA (see Migration Plan §1) | — |

## Home / Mission Control

**Before:** already a strong, previously-redesigned screen — one status line, one hero panel (Today's Performance), a Risk card, Recent Activity. This program's research *validated* that earlier redesign rather than finding fault with it.

**After:** structurally the same idea, rebuilt on the new design system: semantic-only color (P3), collapsed risk detail (P2), a real in-progress state pattern (P4) ready for wiring to live actions. The "hero number, one status line" instinct production already had is confirmed correct by the Robinhood/Coinbase research, not reinvented.

**Verdict:** smallest delta of any screen — production was already close to right here.

## Funds / Broker / Performance → Portfolio

**Before:** three separate top-level pages answering the same underlying question ("how is my money doing") — a user has to know *which* of three pages holds the number they want.

**After:** one destination, tabbed. Coinbase's own card-sort research is the direct justification: users open a portfolio app to check their assets and performance, as one motion, not three.

**Verdict:** largest structural simplification in the whole redesign.

## Signal Sources → Signals

**Before:** a channel list with win-rate/P&L shown as one column among several in a settings-style table — functionally present, visually secondary.

**After:** each source rendered as its own card, win rate as the dominant number — a direct implementation of the DubClub structural analogy (P8), the single most important reframe from the research phase. A source with no data yet says so honestly ("Not enough signals yet to judge honestly") rather than showing a blank or a zero.

**Verdict:** same data, fundamentally different prominence — this is the screen where the research most changed the outcome.

## Trading Sessions + Trade History → Sessions

**Before:** two pages — one for the live/active view, one for historical records — that are really one continuous timeline split by implementation detail (`status='active'` vs. everything else).

**After:** one page, one timeline, active sessions visually distinguished (a lit status dot) rather than architecturally separated.

## Mobile

**Before:** production's responsive CSS (`web/theme.css`) is genuinely solid — collapsible sidebar, mobile nav toggle, responsive tables — confirmed working via screenshot earlier this project. It adapts the *desktop* layout down.

**After:** designed mobile-first for its actual mobile use case (P5) — monitoring + 3 emergency controls in the thumb zone, a 4-item bottom nav (Home/Signals/Sessions/More) instead of a hamburger-collapsed 15-item sidebar. Configuration-heavy screens (Portfolio detail, Strategy) are reachable but not pretending to be optimized for phone-width data entry.

**Verdict:** production's mobile CSS is competent; V2's mobile *information architecture* is a genuine rethink, not just a breakpoint.

## What did NOT change philosophically
- Semantic-only green/red (P3) — an existing, correct AXIM rule, kept as a hard constraint.
- Never fabricate a number (P9) — an existing, hard-won AXIM discipline, inherited unconditionally.
- No monetization interstitials during a working flow (P7) — production already avoids this; V2 makes it an explicit rule so it can't regress.

---

*Screenshots backing this comparison are in the scratchpad captured during this session — `home_fixed.png`, `portfolio_fixed.png`, `signals.png`, `sessions_fixed.png`, and the three responsive breakpoints. Not committed to the repo (binary screenshots don't belong in git history); available on request or via a fresh Playwright run against the running preview server.*

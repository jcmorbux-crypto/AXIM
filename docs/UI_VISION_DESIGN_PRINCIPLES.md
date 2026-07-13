# AXIM Trader V2 — Design Principles & Information Architecture

Built from the UX Research Report. Every rule below traces back to a specific finding, not a personal preference.

---

## Part 1 — Design Principles

### P1. One decision per screen
No screen asks for more than one meaningful decision. A "meaningful decision" is anything that changes real money behavior (risk %, trade amount, which channels to watch) — cosmetic fields (a session's display name) don't count against this budget.
*Source: Robinhood onboarding, Crypto.com redesign principle.*

### P2. The math is one click away, never zero
Every advanced number (Martingale ladder, Kelly formula, Compounding curve, Capital Strategy internals) is collapsed by default behind a labeled disclosure. The *decision* ("Conservative starting point") is what a new user sees first; the *formula* is for the user who goes looking for it.
*Source: Robinhood's collapsible regulatory disclosures.*

### P3. Color is semantic and exclusive
Green and red mean exactly one thing anywhere in the product: price/P&L direction. They are never repurposed for buttons, badges, nav states, or anything else. Blue is the one brand/action color. This was already an AXIM rule — V2 makes it a hard constraint, not a convention.
*Source: Coinbase Design System, Robinhood brand system.*

### P4. Every long action shows its own progress
Starting a session, connecting a broker account, running a backtest, saving a risk profile — every one of these gets a visible in-progress state with a plain-language description of what's happening, not a bare spinner. Silence during a wait is the #1 trust-killer in the fintech research.
*Source: Fintech trust research (Mind the Product, Wonderment).*

### P5. Mobile is for monitoring and control, not data entry
On a phone, AXIM's job is: check status, see recent activity, and reach Pause/Stop/Emergency Stop instantly. Configuration-heavy tasks (building a risk profile, writing an automation rule) are desktop-first by design, not crippled-but-technically-present on mobile.
*Source: TradingView mobile thumb-zone research.*

### P6. Five or six primary destinations, everything else is secondary
A first-time user should be able to hold the entire top-level map of the app in their head. Advanced/power-user surfaces exist, but they live *inside* a primary destination, never bloat the primary nav itself.
*Source: Coinbase's card-sort finding — 5 pages cover what users actually do; the rest moved to a menu.*

### P7. No monetization or marketing interstitials inside a working flow
Setup, configuration, and trading flows never interrupt themselves with upsells, cross-sell prompts, or promotional content. If AXIM ever needs to show a billing/upgrade prompt, it happens at a natural boundary (Settings, a dedicated Billing page) — never mid-task.
*Source: Crypto.com's reviewed misstep (debit-card promo slides during onboarding).*

### P8. Sources are judged the way DubClub judges cappers
A signal source's win rate and realized P&L are first-class, prominent information — exactly as visible as a DubClub capper's public track record — not a secondary stat buried in a settings table. This is the single most useful reframe from the whole research pass: AXIM's real product is "tell me honestly whether this source is worth following," and the UI should say so as loudly as the trading UI itself.
*Source: DubClub structural analysis (§1 of the Research Report).*

### P9. Never fabricate confidence
If a number isn't real (a confidence score the parser doesn't compute, a projection with no real basis), the UI says so in plain language instead of inventing a plausible-looking placeholder. This is an existing, hard-won AXIM discipline from the production codebase — V2 inherits it unconditionally, it is not up for redesign.

### P10. Density is a function of audience, not a universal good — added after live feedback
The first Dashboard pass ("Home") applied P1/P6 (one decision, one hero number) to a screen where they didn't belong, and it read as "an admin panel with trading widgets." The research itself explains why: *"Bloomberg Terminal users expect maximum data density... while retail investors on platforms like Robinhood need progressive disclosure"* (Lollypop, 2026). The determining factor isn't the product category, it's **who opens the screen and why**.

AXIM's Dashboard is opened by a *returning daily trader* checking a running operation — closer to a Bloomberg/TradingView audience than a first-time Robinhood signup. It is now a dense, three-column trading-terminal layout (nav+sources / live activity / funds+risk+controls), with real motion (a live-pulse connection indicator, flash-on-update for changing values) rather than a static card stack.

**This does not repeal P1, P2, or P6.** The Setup Wizard, Risk Profile creation, and other configuration flows are opened by a user making a one-time or infrequent *decision* — those stay exactly as spread-out and disclosure-first as originally specified. P10 says density is *earned* by a screen's specific audience and task, not applied uniformly across the whole product.
*Source: Lollypop trading-app design guide (2026), Bloomberg/TradingView workflow research, live product feedback during this session.*

---

## Part 2 — Information Architecture

### Before (production, 15 primary nav items)
Mission Control · Multi-Fund Manager · Trading Sessions · Signal Sources · Signal Inspector · Money Management Studio · Capital Strategies · Automation Studio · Strategy Lab · Trade History · Performance · Broker · Users · Logs · Settings · Help/Guide

Functionally complete, but violates P6 outright — a first-time user cannot hold 15 top-level destinations in their head, and several of these are really *views of the same underlying concept* split apart by implementation history rather than by user mental model.

### After (V2, 6 primary destinations)

```
┌─ Home           one hero number, status line, today's activity, 3 quick actions
├─ Portfolio      Funds · Broker Accounts · Balances · Performance  (tabs)
├─ Signals        Sources & win-rate transparency · Signal Inspector (advanced tab)
├─ Sessions       Active Sessions · History  (was Trading Sessions + Trade History)
├─ Strategy       Money Management · Capital Strategies · Automation Studio · Strategy Lab  (tabs)
└─ Settings       Account · Users · Logs · Billing · Help/Guide
```

**Why these six, specifically:**

- **Home** answers Coinbase's #1 finding directly: "how did I do, is everything running." Nothing else belongs here.
- **Portfolio** groups by *what the user owns* (Funds, the broker accounts backing them, and how they've performed) — currently split across 3 separate nav items that all answer "how is my money doing."
- **Signals** groups by *where trades come from* — implements P8 directly. Signal Inspector (a debugging tool) becomes an advanced tab inside Signals, not a peer of it.
- **Sessions** groups by *trading activity, live and historical* — Trading Sessions and Trade History are the same underlying concept (a session's life, and its record after it ends) that production splits by implementation detail (active vs. closed), not by user mental model.
- **Strategy** is the deliberate "advanced zone" — Money Management, Capital Strategies, Automation Studio, and Strategy Lab all answer "how should AXIM decide what to do," just at different levels of sophistication. Grouping them together, behind one clearly-labeled destination, is what makes the *other five* destinations safe to keep simple (P1, P6) without losing any real capability.
- **Settings** is the standard catch-all for account/admin/reference material — unchanged in spirit from production, just now genuinely the *only* place non-trading concerns live.

### What did NOT change
- Every production capability still exists somewhere in V2 — this is a reorganization, not a feature cut. Nothing in the Research Report suggested AXIM has features it should remove; the finding was consistently about *grouping and disclosure*, never deletion.
- The underlying data/API contracts are untouched — V2 is a new presentation layer over the same backend, per the assignment's own rule ("use the existing backend only as the data source").

### Mobile-specific IA (P5)
Mobile shows: **Home**, **Sessions** (with Pause/Stop/Emergency Stop always reachable), and **Signals** (read-only transparency view). Portfolio, Strategy, and Settings are reachable but not optimized for phone-width configuration — they render responsively (nothing breaks) but aren't the primary mobile experience, matching P5 explicitly rather than pretending a phone is a small desktop.

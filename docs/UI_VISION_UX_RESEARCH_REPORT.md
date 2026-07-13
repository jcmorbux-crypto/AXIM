# AXIM Trader — UX Research Report

**Branch:** `ui-vision-upgrade` (isolated worktree, zero contact with production)
**Date:** 2026-07-12
**Scope:** Public material only — reviews, UX case studies, design-system documentation, App/Play Store reviews. No proprietary artwork, layouts, or branding was copied; every finding below is a *principle*, not a pixel.

---

## 1. What was researched

| Product | Category | Material found |
|---|---|---|
| Robinhood | Consumer stock trading | Onboarding flow analysis, Google Design case study, IXD@Pratt design critique |
| Coinbase | Consumer crypto exchange | Official Coinbase Design System (cds.coinbase.com), UX redesign case studies |
| TradingView | Charting / trading terminal | Mobile UX case studies, usability-issue reviews, design-system breakdowns |
| Telegram | Messaging (AXIM's own signal source) | Multiple independent UX teardown/redesign case studies |
| Pocket Option | Binary options broker (AXIM's own execution target) | User reviews, platform comparisons |
| Crypto.com | Consumer crypto exchange | Onboarding UX comparison studies, App Store review synthesis |
| Whop | Creator commerce / subscription platform | Multiple 2026 platform reviews |
| DubClub | Subscription picks marketplace (sports betting cappers) | TechCrunch coverage, platform review, official site |
| Nebor Trade | — | **Not found.** No public product by this name was locatable. Not fabricated — flagged honestly rather than invented. |

Two of these — **Telegram** and **Pocket Option** — are not just inspiration, they're AXIM's own upstream signal source and downstream execution venue respectively. Their UX patterns are doubly relevant: AXIM's users already have muscle memory from both.

**DubClub is the single closest structural analog to AXIM.** It is not a trading platform — it's a marketplace where subscribers pay to follow an expert's picks and see a transparent win/loss record. That is *exactly* AXIM's own Signal Sources concept (a Telegram channel is functionally a "capper"), which is a genuinely useful reframe: AXIM isn't only a trading app, it's a "did this source actually work" transparency tool.

---

## 2. Cross-cutting principles (found independently across 3+ products)

### 2.1 One decision per screen
Robinhood's onboarding asks for exactly one piece of information per screen. Crypto.com's redesign explicitly cites "limiting one action per screen" as a named principle. This isn't a stylistic choice — it's measured: cognitive-load research (NN/g, Mailchimp) shows decision-bunching is the #1 cause of form abandonment. **AXIM's current 8-step wizard already does this structurally — the opportunity is applying the same discipline to Fund creation, Risk Profile setup, and Session start, which currently front-load 6-10 fields on one screen.**

### 2.2 Progressive disclosure, not feature removal
Every minimalism-praised product (Telegram, Coinbase) was explicit that minimalism is *not* deleting features — it's hiding advanced ones behind a deliberate second click. Telegram's "core functions immediately accessible, advanced settings neatly organized within menus" is the exact pattern AXIM already uses for Developer Mode — but it's inconsistently applied. Martingale ladders, Compounding curves, and Capital Strategy internals are currently all first-screen citizens even for a user who just wants "start trading."

### 2.3 Semantic color, used nowhere else
Coinbase's design system is explicit: **green/red are reserved exclusively for price direction**, never for anything else (not buttons, not status badges, not navigation). Robinhood ties its entire brand identity to the same rule. AXIM's current `theme.css` already documents this discipline ("green/red reserved for profit/loss only") — this is a case where research *confirms* an existing AXIM decision rather than challenging it. Keep it, state it as a hard rule in the new design system too.

### 2.4 The dashboard answers one question, not twenty
Coinbase's own UX research found something specific and worth internalizing: **most users open the app to check on their assets and see how they performed** — a single mental model. Their redesign response was 5 main pages, everything else moved to a side menu. AXIM's own Mission Control redesign (done earlier this project) already converged on the same idea — one hero number, one status line — which the research validates as correct, not something to re-litigate.

### 2.5 Real-time feedback as a trust mechanic, not a nicety
Fintech-trust research is blunt: users don't leave because of missing features, they leave because they're *unsure whether something worked*. Progress indicators, confirmation states, and visible "processing" moments are trust infrastructure, not polish. This directly targets a real AXIM gap: right now, starting a session, connecting a broker account, or running a backtest gives no persistent "this is in progress and here's what's happening" feedback beyond a spinner.

### 2.6 Thumb-zone architecture for mobile trading specifically
TradingView's mobile redesign research is specific to *trading* (not general mobile UX): critical actions live in the natural thumb reach zone; secondary controls move to gesture-based interaction (swipe, long-press). This matters more for AXIM than for a generic fintech app, because AXIM's mobile use case is monitoring + emergency control (Pause/Stop/Emergency Stop), not data entry — those three controls should be the ones in the thumb zone on every mobile screen, not buried in a menu.

### 2.7 Compliance and complexity, presented as opt-in disclosure
Robinhood collapses regulatory disclosures by default, letting users engage only if they choose to. This is the same principle AXIM should apply to Martingale exposure math, Kelly formulas, and Capital Strategy internals: present the *decision* ("Conservative / Balanced / Aggressive") by default, put the *math* one disclosure-click away.

---

## 3. What NOT to imitate (explicitly, from the research itself)

- **Coinbase's own critical case study** ("How Coinbase Uses Design Against Their Own Users") documents dark patterns around fee transparency during onboarding — the opposite of what AXIM should do with real-money risk settings. AXIM's own discipline (never fabricate a number, never hide a risk parameter) is already stronger than this and should stay that way.
- **Crypto.com's post-signup upsell slides** (debit card cashback promos before showing the actual product) — reviewed as a genuine UX misstep. AXIM's Setup Wizard should stay strictly on-task: connect → configure → trade, no monetization interstitials.
- **TradingView's most-repeated complaint** is "layout complexity" and "unclear navigation paths" on mobile specifically — a caution, not a pattern to copy. A charting terminal's complexity is inherent to its job; AXIM's job (automated execution, not manual charting) has no excuse to inherit that complexity.
- **Whop's storefront** trades aesthetics for conversion-first design and is explicitly described as "not a design playground." AXIM is not a marketplace; this pattern doesn't transfer and shouldn't be referenced for anything beyond "modular dashboard, one sidebar handles everything" as an information-architecture idea.

---

## 4. Direct implications for AXIM Trader V2

1. **Reframe Signal Sources as "capper transparency," DubClub-style** — win rate and P&L per source should be as prominent as a DubClub capper's record, not a secondary stat on a settings page.
2. **Apply Coinbase's card-restraint rule literally**: 5-6 primary destinations, everything else behind a menu. AXIM's current 15-item sidebar violates this — the redesign's Information Architecture (next deliverable) should collapse this.
3. **Give every long-running action a visible in-progress state** — session start, broker connect, backtest run — matching the fintech-trust research finding.
4. **Design the mobile experience around monitoring + 3 emergency controls in the thumb zone**, not a shrunk desktop layout.
5. **Every risk/strategy screen defaults to the decision, not the math** — collapsed disclosure for Martingale/Kelly/Capital-Strategies internals, matching Robinhood's disclosure pattern.
6. Keep AXIM's own already-correct decisions: semantic-only green/red, one hero metric on the primary screen, no monetization interstitials during onboarding.

---

## Sources

- [How the Robinhood UI Balances Simplicity and Strategy on Mobile](https://worldbusinessoutlook.com/how-the-robinhood-ui-balances-simplicity-and-strategy-on-mobile/)
- [5 ways that Robinhood is winning with great UX](https://medium.com/@jvh_544/5-ways-that-robinhood-is-winning-with-great-ux-cb3a9844b8f7)
- [Robinhood App: Invest with Material Design Ease — Google Design](https://design.google/library/robinhood-investing-material)
- [Design Critique: Robinhood (iOS App) – IXD@Pratt](https://ixd.prattsi.org/2025/02/design-critique-robinhood-ios-app/)
- [Case Study: Coinbase UX Redesign](https://jpux.medium.com/case-study-coinbase-ux-redesign-9fa4038f5d52)
- [How Coinbase Uses Design Against Their Own Users: a UX Case Study](https://medium.com/defidesign/how-coinbase-uses-design-against-their-own-users-a-ux-case-study-75b3160fc2)
- [Few Guesses, More Success: 4 Principles to Reduce Cognitive Load in Forms — NN/g](https://www.nngroup.com/articles/4-principles-reduce-cognitive-load/)
- [Scaling TradingView's UI/UX for Traders](https://rondesignlab.com/cases/tradingview-platform-for-traders)
- [Trading App Design: The Complete Guide to UI, UX & System Architecture — Lollypop](https://lollypop.design/blog/2026/june/trading-app-design/)
- [Telegram UI/UX: Design Deep Dive](https://createbytes.com/insights/telegram-ui-ux-review-design-analysis)
- [Case Study: Redesigning the Telegram App Using Heuristic Design Principles](https://medium.com/@alenajesuis/case-study-redesigning-the-telegram-app-using-heuristic-design-principles-77cd69f6106e)
- [Pocket Option Review — Pros and Cons from Real User Experiences](https://m.pocketoption.com/en/reviews/)
- [Is Pocket Option a Good Trading Platform?](https://pocketoption.com/blog/en/interesting/trading-platforms/is-pocket-option-a-good-trading-platform/)
- [Crypto App Onboarding UX: Crypto.com vs Okcoin](https://medium.com/design-bootcamp/crypto-app-onboarding-ux-crypto-com-vs-okcoin-a-tale-of-two-experiences-a0acce4e86af)
- [Crypto.com Review 2026 — Coin Bureau](https://coinbureau.com/review/crypto-com-review)
- [Whop App Review: Honest Look at Features, Fees, and Marketplace Quality](https://fritz.ai/whop-app-review/)
- [Whop Review 2026 — CreatorStackClub](https://www.creatorstackclub.com/software/whop)
- [DubClub — Premium Sports Betting Picks from Expert Handicappers](https://dubclub.win/)
- [DubClub wants amateur sports bettors to win more — TechCrunch](https://techcrunch.com/2024/09/04/dubclub-wants-amateur-sports-betters-to-win-more/)
- [A Guide to UX Design for Fintech in 2026](https://www.wondermentapps.com/blog/ux-design-for-fintech/)
- [Designing Trust: UX Principles in Fintech Apps](https://dev.to/pocketportfolioapp/designing-trust-ux-principles-in-fintech-apps-2gfo)
- [UX that builds trust: What top fintechs get right](https://www.mindtheproduct.com/ux-that-builds-trust-what-top-fintechs-get-right/)
- [Colors — Coinbase Design System](https://cds.coinbase.com/getting-started/colors)
- [Robinhood Brand Color Codes](https://www.brandcolorcode.com/robinhood)
- [Dashboard Design: best practices and examples — Justinmind](https://www.justinmind.com/ui-design/dashboard-design-best-practices-ux)
- [Fintech UX Design: 10 Best Practices for Dashboards](https://www.wildnetedge.com/blogs/fintech-ux-design-best-practices-for-financial-dashboards)

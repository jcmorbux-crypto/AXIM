# AXIM Trader UI v2 — Migration Audit

Written before any implementation, per the UI v2 design package's own
"First action: inspect, do not rewrite blindly" requirement.

## Real stack (not what the design package's reference code assumes)

The design package's `07_REFERENCE_CODE/` (`MetricCard.reference.jsx`,
`view-model-contracts.ts`) illustrates the intended component/data
contracts in React + TypeScript. **AXIM Trader has no React, no
TypeScript, no build step, and no bundler.** Every page is a
server-rendered static HTML file in `web/*.html`, each with its own
inline `<script>` (vanilla JS, `fetch`-based), served directly by
FastAPI (`api/main.py`) with no compilation step. Shared chrome lives
in two files every authenticated page includes:

- `web/theme.css` — the one design-token/component stylesheet.
- `web/shell.js` — sidebar/mobile-nav rendering + `AximShell.init()`
  (auth gate, user chip, notification bell) every page calls once on
  load.

Per the master prompt's own Engineering Rules ("Use the repository's
existing framework and conventions"), this migration reuses this exact
model — the `.jsx`/`.ts` reference files are translated to vanilla
JS/CSS patterns already established across `web/*.html`, never a
parallel React app. There is no `npm run build`/lint/type-check step to
run as part of this migration (the checklist's "Build, lint,
type-check" items don't apply to this stack — verification here is:
`python -m unittest discover` for the backend, live Playwright browser
checks for the frontend, matching how every other UI change this
session has been verified).

## Current page inventory → new IA

| Current file | Current route | Current nav label | New IA screen | Notes |
|---|---|---|---|---|
| `dashboard.html` | `/dashboard` | Home | `/dashboard` | Already close to the v2 hierarchy (Portfolio Command Center, built 2026-07-15) - needs restyle to new tokens/cards, not a rebuild. |
| `funds.html` | `/funds` | Funds | `/portfolio` | Funds/broker-accounts/allocations - matches "Portfolio" screen purpose exactly; URL kept per "preserve existing URLs where practical." |
| `telegram.html` | `/telegram` | Sources | `/signals` | Provider intake/parsing/profiles (Universal Signal Intelligence Engine, shipped 2026-07-18) - matches "Signals" screen purpose. |
| `risk.html` | `/risk` | Money Management (under More) | `/money-management` | Already the "5 official strategies + Custom Builder" design (shipped 2026-07-18) - needs restyle + promotion to primary nav per v2 IA. |
| `strategy_lab.html` | `/strategy-lab` | Strategy Lab | `/backtesting` | Setup/progress/results/comparison - matches "Backtesting" screen purpose. |
| `automation.html` | `/automation` | Automation Studio (under More) | `/strategies` (partial) | Entry/execution rule builder - closest existing match to "Strategies"; the v2 screen also implies asset/direction/provider filter rules already covered by Money Management's Custom Builder. |
| `performance.html` | `/performance` | Performance | `/performance` | Direct match. |
| `trades.html` | `/trades` | (linked from Performance, not in nav) | `/performance` (ledger tab) | Trade ledger - v2 folds this into Performance per its own spec ("trade ledger" listed under `/performance`). |
| `broker.html` | `/broker` | Broker Accounts (under More) | `/bots` (partial) + `/portfolio` | Broker account health/connection maps to "Bots" (connected bot/session health) in spirit; account CRUD stays portfolio-adjacent. |
| `sessions.html` | `/sessions` | Sessions | `/bots` (partial) | Active trading session controls - v2's "Bots" screen purpose ("connected bot health, sessions, controls, logs") is this page's real analog. |
| `inspector.html` | `/inspector` | Signal Inspector (under More) | `/signals` (tab) | Folds into the new Signals screen as a detail/inspection view. |
| `settings.html` | `/settings` | Settings (under More) | `/settings` | Direct match; gains theme toggle persistence. |
| `logs.html`, `users.html`, `billing.html`, `guide.html` | various | under More | unchanged | Not in the v2 screen inventory - stay as-is, reachable the same way (progressive disclosure under "More"), per "routes may differ in the current repository." |
| `login.html`, `wizard.html`, `reset_password.html` | pre-auth | n/a | unchanged | Outside the authenticated shell; not part of this migration. |

There is no `/analytics` route today - the v2 spec lists it as "deeper
diagnostics" distinct from `/performance`; deferred (not yet a real,
separately-justified page - avoiding a hollow placeholder per this
project's own standing "no placeholder screens" rule).

## Baseline

Full backend test suite (`python -m unittest discover` in `tests/`):
1149 tests, passing, 0 failures, as of the last full run this session
(2026-07-18, commit `31792db`). No frontend build/test tooling exists
to baseline separately - this stack has none.

## Migration approach for this pass

Given the scale (a full visual re-skin of ~19 real, live, working
pages, most already built and verified working this session with a
DELIBERATE existing "premium wealth management, restrained color"
design language - see `web/theme.css`'s own header comment), this is
being executed as the blueprint's own "small, testable commits"
sequence, not a single pass:

1. **Design tokens + theme provider** (this commit): add real dark-mode
   support (did not exist at all before this) and a new `--brand`
   (purple, `#6C3AED`) primary-action token, additive alongside the
   existing `--blue` (kept as the distinct "info/trust" semantic the
   v2 package itself specifies) - a surgical change to the ONE shared
   `theme.css`/`shell.js` pair every page already includes, so every
   page's buttons/nav/focus-rings pick up the new brand color and
   dark-mode capability at once, without a per-page rewrite.
2. App shell/navigation restyle to match the v2 board's visual
   language (rounded active-nav treatment, theme toggle control).
3+. Page-by-page visual migration, dashboard first (highest priority
   per the spec's own primary-hierarchy section), continuing in
   priority order in subsequent commits.

Every stage is verified live in the browser (screenshots, both
themes) before commit, and the full backend suite is re-run after any
change that touches shared files multiple pages depend on.

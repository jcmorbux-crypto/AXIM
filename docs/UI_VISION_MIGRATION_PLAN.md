# AXIM Trader V2 — Migration Plan

**Status: no merge has occurred, none is proposed by this document.** This plan exists so a future decision to migrate is informed, not so migration happens automatically. Per the program's own rule: *no merge until UI review complete, usability testing complete, production tests still pass, and this plan is explicitly approved.*

---

## 1. What "migration" actually means here

V2 is a **new presentation layer over the existing backend**, not a backend rewrite. Every V2 screen so far (`Home`, `Portfolio`, `Signals`, `Sessions`) is read data pulled through the *same* `core/database.py`, `core/fund_manager.py`, and `core/trade_statistics.py` functions production already uses. Migrating does not mean touching `core/`, `api/`, or `execution/` — it means eventually pointing real write actions (Start Session, Pause, Emergency Stop, etc., currently inert preview buttons) at the *same* API endpoints `web/*.html` already calls.

## 2. Phased approach (recommended, not started)

### Phase 0 — Where we are now
V2 is a static, read-only prototype against a database *snapshot*, served by a throwaway preview server (`preview_server.py`) that doesn't exist in production and never will — it's scaffolding for this design process only, not a component to migrate.

### Phase 1 — Wire V2 pages to the real, live production API (still isolated)
Point `web_v2/*.html`'s `fetchPreview()` calls at production's real endpoints (`/api/status`, `/api/funds`, `/api/channels`, etc.) instead of `preview_server.py`'s snapshot-backed ones. This can happen entirely within the `ui-vision-upgrade` branch, served on a still-separate port, reading LIVE data — genuinely useful for usability testing with real numbers, still zero risk to production because it only ever calls existing, already-shipped, already-tested GET endpoints.

### Phase 2 — Wire the "advanced zone" (Strategy tab set)
Build out the `strategy.html`/`settings.html` stubs into full tab implementations covering Money Management Studio, Capital Strategies, Automation Studio, Strategy Lab, Users, Logs — the parts of the IA not yet built in this pass.

### Phase 3 — Enable real actions
Wire Start Session / Pause / Emergency Stop / broker-account actions to their real POST endpoints. This is the first phase where V2 can *do* something, not just *show* something — and the first phase that needs the same care production changes always get (test coverage, live verification against demo, never touching a real session without explicit sign-off).

### Phase 4 — Side-by-side operation
Both `web/*.html` (production) and `web_v2/*.html` sit behind the same API, reachable at different paths (e.g. `/` for production, `/v2` for the new IA), so the operator can switch at will and compare against real daily use before committing to a cutover.

### Phase 5 — Cutover (requires explicit approval)
Only after Phase 4 has run long enough to build real confidence: `web_v2/` becomes the default, `web/` moves to a `/legacy` path exactly as the original dark-theme UI did during AXIM's own Phase 1 rebrand — same precedent, same reversibility.

## 3. What does NOT change, ever, as part of this migration
- `core/`, `api/`, `execution/` — the trading engine, risk gates, database schema, and safety checks are completely out of scope. V2 is forbidden from introducing a second way to place a trade.
- The three-gate live-trading lock (`ARMED`, `ACCOUNT`, per-account `live_enabled`) — V2 cannot bypass, shortcut, or duplicate this.
- Existing automated test suite — every phase above requires the full suite (717+ tests as of this writing) to stay green.

## 4. Rollback plan
Because V2 never touches `core/`/`api/`/`execution/`, rollback at any phase is just "stop serving `web_v2/`, keep serving `web/`" — there is no data migration to reverse, since V2 never had its own write path until Phase 3, and even then it uses the exact same endpoints/tables production already writes to.

## 5. Effort estimate (rough, for planning only)
| Phase | Scope | Rough estimate |
|---|---|---|
| 1 | Point V2 at live API | 2-4h |
| 2 | Build Strategy/Settings tabs | 12-20h (4 sub-products' worth of UI) |
| 3 | Wire real actions + safety review | 8-14h |
| 4 | Side-by-side operation period | Calendar time, not engineering hours |
| 5 | Cutover | 2-4h + a full regression pass |

Not a commitment — a planning estimate, to be revisited once Phase 1 is actually underway and real unknowns surface.

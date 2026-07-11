# Live Trading Checklist

Read this before ever setting a broker account's `live_enabled` flag or
`.env`'s `ACCOUNT` to anything other than `DEMO`. This is a summary;
the full checklist with evidence, code references, and a complete
security audit is `docs/AXIM_LIVE_READINESS_CHECKLIST.md` - read that
one in full before going live, not just this page.

## Where things stand as of this Release Candidate

**Not ready for live trading yet - by design, not oversight.** Every
mechanical safety system is built, wired, and verified:

- [x] Three independent safety gates all agree before a live click can
      happen: global `ARMED`, global `ACCOUNT=DEMO`, and each broker
      account's own `live_enabled` flag.
- [x] Drawdown circuit breaker, max trade amount, max trades/hour,
      consecutive-loss cooldown all fail closed (missing data rejects,
      never silently allows).
- [x] Process recovery, listener supervision, and Capital Strategies'
      sizing engine all verified not to touch or weaken any of the
      above.
- [x] Security audit clean: no SQL injection, password hashing is
      solid (PBKDF2-HMAC-SHA256, 600k iterations), session/reset tokens
      are cryptographically random and single-use, no secrets leak in
      API responses.

**What genuinely remains - two things, in order:**

1. **You personally inspect your real live Pocket Option cabinet** and
   set `LIVE_URL`/`LIVE_MODE_VERIFICATION_CLASS` in `.env`. This is
   deliberately not automated - AXIM refuses to guess what a live
   cabinet looks like, and nothing in this project's history has ever
   inspected one. No one else can do this step.
2. **Run a fresh observation window with real, non-relaxed risk
   thresholds** (`.env.example`'s defaults: `MINIMUM_PAYOUT=90`, real
   `MAX_TRADES_PER_HOUR`, etc.) and read the resulting win rate net of
   actual payout. Any number you've seen from an earlier stress/soak
   test with relaxed thresholds is not a preview of real production
   performance - it exists to prove the pipeline runs continuously
   without crashing, not to answer "does this have an edge."

Only after step 2 shows a real edge net of payout should you consider
flipping a single broker account's `live_enabled` on, at the smallest
possible stake, watched deliberately - the same discipline every demo
test in this project has followed.

## What NOT to do

- Do not set `ARMED=true` in a checked-in/shared `.env`. It should stay
  `false` except for a specific, deliberate, watched session.
- Do not treat a demo win rate under relaxed thresholds as evidence of
  a real edge.
- Do not skip step 1 above. There is no safe default live URL.

See `docs/AXIM_LIVE_READINESS_CHECKLIST.md` for the full picture,
including exactly what was verified this Release Candidate and how.

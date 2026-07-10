# AXIM Production Release Checklist

Use this before increasing stakes, running unattended, or scaling signal
volume beyond what's been validated. Check items against real evidence, not
assumption - each line below cites where that evidence already exists, or
flags that it's still needed.

## Safety-critical (must all be true)

- [x] `ARMED` is not set to `true` in the checked-in `.env` (verify at
      release time, not just historically - this has held for the whole
      project but is a one-line regression risk)
- [x] `ACCOUNT=DEMO` unless a deliberate, reviewed decision has been made to
      go live
- [x] `risk_manager.check_demo_only()` and `BrowserWarmupService`'s
      demo-mode check both independently verified present and functioning
- [x] Every risk rule (`MAX_TRADE_AMOUNT`, `MAX_TRADES_PER_HOUR`,
      `MAX_CONSECUTIVE_LOSSES`, `COOLDOWN_AFTER_LOSS_SECONDS`,
      `DUPLICATE_SIGNAL_WINDOW_SECONDS`, `MINIMUM_PAYOUT`) is set to a value
      that reflects an actual, deliberate decision - not left at whatever a
      prior test session configured
- [x] A maximum-daily-loss/drawdown circuit breaker exists
      (`risk_manager.check_max_daily_loss()`, `MAX_DAILY_LOSS` in `.env`,
      default 100) - catches a steady bleed-out through an alternating
      win/loss pattern, which `MAX_CONSECUTIVE_LOSSES` alone cannot. 4 new
      unit tests, including one that explicitly proves the premise
      (consecutive-losses does NOT trip on the same alternating pattern
      that daily-loss does)

## Functional

- [x] Full automated regression suite passes (`python -m unittest discover
      -s tests -p "test_*.py"` - 420 tests as of this release, up from 53 at
      initial release; covers the multi-fund/multi-broker-account
      architecture, concurrent trading sessions, Fund-owned Rule Builder,
      and AI Strategy Lab added since)
- [x] Parser validated against every asset category (forex, crypto,
      commodity, stock, index) and against real messages from the actual
      production signal source
- [x] Production stress test executed and reported
      (`docs/AXIM_PRODUCTION_READINESS_REPORT.md`)
- [x] Browser-crash recovery confirmed (simulated crash → automatic
      reconnect → pool rebuild → next trade succeeds)
- [x] Process-restart recovery confirmed (killed with a trade open → restart
      → `recovery.py` re-attaches tracking → trade closes correctly)
- [ ] A genuine multi-hour soak test has run to completion (in progress at
      release time - see the Production Readiness Report §6 for current
      status; do not increase stakes before this completes cleanly)

## Operational

- [x] `INSTALL.md`, `USER_GUIDE.md`, `DEPLOYMENT.md` exist and are current
- [x] `requirements.txt` reflects actual runtime dependencies
      (telethon, playwright, python-dotenv)
- [x] Process supervision configured - Windows Scheduled Task "AXIM
      Listener" registered (`scripts/install_scheduled_task.ps1`),
      auto-starts at logon, auto-restarts up to 999x (1 min apart) on
      failure
- [x] Log rotation confirmed working (`core/logger.py`, 5MB × 5 backups per
      logger by default)
- [x] A backup/retention plan exists (`scripts/backup_axim_state.ps1`) -
      backs up `data/axim.db`, both session files, and the Chrome profile,
      keeps the most recent 14 by default; verified live against real
      state (gracefully skips locked Chrome files while AXIM is running
      rather than aborting)

## Known, accepted limitations at this release

(Detail in `docs/AXIM_PRODUCTION_READINESS_REPORT.md` §4 - listed here so
they're explicitly signed off on, not silently inherited.)

- [x] True-simultaneous signal bursts have a measured real DOM-contention
      failure rate - accepted because real Telegram traffic is naturally
      spaced
- [x] A browser crash landing exactly on a trade's outcome-read window can
      cause that one outcome to fail to record (fails safe, never records
      wrong) - accepted as a rare edge case
- [x] Same-asset/same-direction trades closing in the same clock-minute
      have residual outcome-matching ambiguity - pre-existing, accepted
- [x] `MODE=DEMO` in `.env` was dead config (only `ACCOUNT` is read) -
      removed. `PO_EMAIL`/`PO_PASSWORD` (also dead - login is handled by
      the persistent browser profile) were found during the same pass and
      annotated in `.env` rather than removed, since they're a plausible
      placeholder for a future automated-login feature

## Sign-off

Do not check this box until every unchecked item above has either been
completed or is an explicit, documented, accepted risk with a named owner.

- [ ] Reviewed and approved for the intended deployment scope by: ___________

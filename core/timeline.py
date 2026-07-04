"""
Per-trade observability: a named-stage timeline plus measured (not
estimated) time-category totals for every trade.

Stages (absolute wall-clock timestamps, not monotonic elapsed-ms): a single
trade's timeline can span two different OS processes - prepare_trade and
the first several stages run in the live listener process, but a trade
resumed by core/recovery.py after a restart continues its own
track_outcome in a brand new process. Monotonic clocks aren't comparable
across that boundary; wall-clock timestamps are, so deltas are computed at
analysis time (core/timeline_report.py) by parsing ISO timestamps.

Categories (waiting / browser / database / logging) are genuinely measured
via time_category, a context manager that adds elapsed wall time to
whichever TradeTimeline is "current" for this asyncio task - set via
activate(), and inherited automatically by any asyncio.create_task() spawned
while it's active (Python's contextvars propagate into child tasks by
value-copy-of-reference at creation time). This lets database.py,
pocket_dom.py, and core/logger.py record their own category time without
any of their many existing call sites needing to change or thread a
timeline object through.

"Active execution time" is NOT separately instrumented - it is the
residual (total trade duration minus the sum of the 4 measured categories).
This mirrors how "wall time = CPU time + I/O wait + ..." is normally
computed: instrumenting every line of pure-Python logic individually would
be impractical and add its own overhead. The subtraction is arithmetic on
four genuinely measured quantities, not a guess.
"""
import contextvars
import functools
import time
from datetime import datetime

STAGES = [
    "signal_received",
    "signal_parsed",
    "risk_evaluated",
    "asset_selected",
    "expiry_set",
    "amount_set",
    "clicked",
    "confirmation_detected",
    "trade_settled",
    "outcome_recorded",
]

MEASURED_CATEGORIES = ("waiting", "browser", "database", "logging")

_current = contextvars.ContextVar("axim_current_timeline", default=None)


def get_current_timeline():
    return _current.get()


class TradeTimeline:
    def __init__(self, trade_id=None):
        self.trade_id = trade_id
        self.stage_timestamps = {}  # stage -> ISO 8601 string
        self.category_totals_ms = {c: 0.0 for c in MEASURED_CATEGORIES}

    def mark(self, stage):
        if stage not in STAGES:
            raise ValueError(f"Unknown timeline stage: {stage!r}")
        ts = datetime.now()
        self.stage_timestamps[stage] = ts.isoformat()
        return ts

    def add_time(self, category, elapsed_ms):
        if category not in MEASURED_CATEGORIES:
            raise ValueError(f"Unknown timeline category: {category!r}")
        self.category_totals_ms[category] += elapsed_ms

    def activate(self):
        """Makes this the current timeline for this asyncio task context
        (and any child task created via asyncio.create_task while it's
        active). Returns a token - pass to deactivate() to restore the
        previous value."""
        return _current.set(self)

    @staticmethod
    def deactivate(token):
        _current.reset(token)

    def persist(self, database_module):
        """Merges (does not overwrite) this timeline's stages/category
        totals into whatever is already persisted for this trade_id. A
        trade's full timeline is typically written in two passes -
        prepare_trade's stages, then track_outcome's - sometimes from two
        different processes (a recovery-resumed trade), so the write must
        combine with existing data rather than clobber it."""
        if self.trade_id is None:
            return
        database_module.record_trade_timeline(self.trade_id, self.stage_timestamps, self.category_totals_ms)


class time_category:
    """Context manager (supports both `with` and `async with`) that times
    the wrapped block and adds it to the CURRENT active timeline's category
    total, if one is active for this context. A no-op measurement (wrapped
    code still runs normally) when no timeline is active - e.g. startup
    code with no trade in flight."""

    def __init__(self, category):
        self.category = category
        self._t0 = None

    def _start(self):
        self._t0 = time.monotonic()

    def _finish(self):
        elapsed_ms = (time.monotonic() - self._t0) * 1000
        timeline = get_current_timeline()
        if timeline is not None:
            timeline.add_time(self.category, elapsed_ms)

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish()
        return False

    async def __aenter__(self):
        self._start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._finish()
        return False


def timed(category):
    """Decorator for async or sync functions - wraps the whole call in
    time_category(category). Used to instrument public functions (e.g.
    every database.py function as "database", every pocket_dom.py
    browser-interaction function as "browser") without needing to touch
    every internal await/query individually."""

    def decorator(func):
        if _is_coroutine_function(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                async with time_category(category):
                    return await func(*args, **kwargs)
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with time_category(category):
                return func(*args, **kwargs)
        return sync_wrapper

    return decorator


def _is_coroutine_function(func):
    import inspect
    return inspect.iscoroutinefunction(func)

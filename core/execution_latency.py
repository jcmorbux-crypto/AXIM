"""
Precision-latency instrumentation for Martin Trader's boundary-to-broker-
acceptance execution path (2026-07-27 precision-latency audit).

Kept deliberately separate from core/timeline.py's TradeTimeline: that
mechanism assumes stage timestamps occur in a FIXED chronological order
(core/timeline_report.py's stage_deltas() zips consecutive STAGES-order
entries) - once pre-staging moves asset/expiry/amount selection to
minutes BEFORE the scheduled boundary, that ordering assumption no
longer holds for Martin Trader specifically, and reusing STAGES would
silently corrupt the shared reporting tool for every other provider.
This module tracks a fixed, named set of timestamps with explicit,
hand-picked derived metrics instead of positional deltas.

VERIFIED LIMITATION (confirmed by reading execution/pocket_dom.py's own
existing extraction code and docstrings, not speculation): Pocket
Option's web UI exposes NO sub-minute timestamp anywhere AXIM can read
it - the Closed-trades list's own displayed time is HH:MM only (see
pocket_dom.py's _closest_closed_item docstring: "the site doesn't
render seconds"), and there is no dedicated open-trades timestamp
extraction at all. broker_acknowledged_at and broker_trade_opened_at
below are therefore AXIM's own local wall clock, captured at the
earliest possible moment the DOM confirms the broker accepted the
order (the .no-deals list hiding) - the best available proxy, not a
true broker-issued timestamp. Every consumer of this data (reports,
this module's own to_dict()) must keep flagging this, never present it
as broker-authoritative.
"""
from datetime import datetime, timezone

FIELDS = [
    "signal_received_at",
    "series_created_at",
    "boundary_calculated_at",
    "scheduled_boundary_at",
    "scheduler_awakened_at",
    "worker_requested_at",
    "worker_acquired_at",
    "browser_command_started_at",
    "order_payload_sent_at",
    "broker_acknowledged_at",
    "broker_trade_opened_at",
    "broker_trade_closed_at",
    "result_detected_at",
    "result_persisted_at",
    "rejected_at",
]


class ExecutionLatency:
    """A plain, ordered bag of ISO-8601 UTC timestamps for ONE entry
    attempt (one series_id + entry_number). Not tied to a trade_id at
    construction time - series_created_at/boundary_calculated_at/
    scheduled_boundary_at/scheduler_awakened_at/worker_requested_at are
    all known before the `signals` row (and its trade_id) even exists;
    core/database.py's record_execution_latency is what actually
    persists everything, merging in trade_id once it's known and again
    as later fields (browser/broker/result timestamps) are captured."""

    def __init__(self, series_id=None, entry_number=None):
        self.series_id = series_id
        self.entry_number = entry_number
        self.timestamps = {}

    def mark(self, field, at=None):
        """Records `field` as `at` (defaults to right now, UTC) - use
        for every field EXCEPT scheduled_boundary_at, which describes a
        future target instant, not "now" (see set_scheduled_boundary)."""
        if field not in FIELDS:
            raise ValueError(f"unknown execution_latency field: {field!r}")
        moment = at if at is not None else datetime.now(timezone.utc)
        self.timestamps[field] = moment.isoformat()
        return moment

    def set_scheduled_boundary(self, boundary_dt):
        """The TARGET five-minute boundary this entry is aimed at - a
        future instant computed in advance, never a "now" marker."""
        self.timestamps["scheduled_boundary_at"] = boundary_dt.isoformat()

    def _delta_ms(self, field_a, field_b):
        if field_a not in self.timestamps or field_b not in self.timestamps:
            return None
        a = datetime.fromisoformat(self.timestamps[field_a])
        b = datetime.fromisoformat(self.timestamps[field_b])
        return (b - a).total_seconds() * 1000

    def metrics_ms(self):
        """The six original requested latency measurements, plus
        boundary_to_rejection_ms (2026-07-27 campaign-classification
        addition): for an entry that never reached broker submission
        (an early risk-check block, a live minimum-payout/asset-
        untradeable rejection, or ARMED=false), this is what actually
        matters - it separates a FAST, correct rejection (the intended
        safety behavior) from a SLOW one caused by a real execution
        delay (e.g. worker-pool contention) eating the pre-stage window
        before the rejection was ever reached. None for any pair whose
        two endpoints aren't both recorded yet (e.g. before the trade
        resolves, or for an entry that was never rejected at all) -
        never a guessed/interpolated value."""
        return {
            "scheduler_lateness_ms": self._delta_ms("scheduled_boundary_at", "scheduler_awakened_at"),
            "worker_acquisition_ms": self._delta_ms("worker_requested_at", "worker_acquired_at"),
            "browser_command_ms": self._delta_ms("browser_command_started_at", "order_payload_sent_at"),
            "broker_acknowledgement_ms": self._delta_ms("order_payload_sent_at", "broker_acknowledged_at"),
            "total_boundary_to_broker_acceptance_ms": self._delta_ms("scheduled_boundary_at", "broker_acknowledged_at"),
            "broker_close_to_result_detection_ms": self._delta_ms("broker_trade_closed_at", "result_detected_at"),
            "boundary_to_rejection_ms": self._delta_ms("scheduled_boundary_at", "rejected_at"),
        }

    def to_dict(self):
        return {
            "series_id": self.series_id,
            "entry_number": self.entry_number,
            "timestamps": dict(self.timestamps),
            "metrics_ms": self.metrics_ms(),
            # Verified limitation (see module docstring) - broker_acknowledged_at/
            # broker_trade_opened_at are AXIM-observed proxies, never a true
            # broker-issued timestamp. Always False; kept explicit so no
            # future caller can mistake this for having been fixed silently.
            "broker_timestamp_authoritative": False,
        }

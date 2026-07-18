"""Generic, provider-agnostic, STATEFUL multi-message signal assembler
(Universal Signal Intelligence Engine, 2026-07-18 product directive).

Generalizes the one real multi-message mechanism already working live
(core/telegram_listener.py's _carried_assets_by_channel /
parsers.signal_parser.parse_asset_announcement, built for OTC Pro
Trading Robot's "Preparing trading asset X" -> "Summary: BUY OPTION..."
two-step pattern) into something that works for ANY provider without a
new hardcoded regex per provider:

- Asset-announcement detection is now the GENERIC "this message is only
  a bare asset reference, nothing else" shape (the same shape core/
  provider_language_learner.py's _asset_only already recognizes across
  multiple real providers in the OPT SIGNALS corpus - NTrade, OTC Pro
  Robot, Pocket Option Signals all send this), not one literal English
  phrase.
- Supports MORE THAN ONE pending sequence per channel at once, keyed by
  asset (directive: "Support more than one pending sequence in a source
  when the provider's pattern requires it") - the old mechanism had
  exactly one slot per channel, so a second announcement silently
  overwrote the first.
- A Telegram reply (message.reply_to_msg_id) to a specific earlier
  message is used as a correlation tie-breaker when present, ahead of
  "most recent pending" - directive: "reply relationships."
- Configurable per-source timeout (profile's assembly_timeout_seconds),
  not one hardcoded constant for every provider.

State is held ENTIRELY in-memory, per running listener process,
exactly like the mechanism it replaces (core/telegram_listener.py's own
comment: "a process restart losing an in-flight announcement just means
that one entry message is treated as unparseable, same fail-closed
behavior as if the asset was never announced, not a corrupted trade") -
a deliberate, honest scope choice consistent with the code it replaces,
not a corner cut silently. Every function that touches state takes the
state object explicitly rather than reading a module-global, so this is
fully unit-testable without a real Telegram connection.
"""
import sys
import time
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR.parent / "parsers"))

from signal_parser import parse_signal  # noqa: E402
import provider_language_learner as learner  # noqa: E402

DEFAULT_ASSEMBLY_TIMEOUT_SECONDS = 300


class PendingSignal:
    """One in-progress multi-message signal for a specific asset on a
    specific channel. `message_ids` accumulates every message that
    contributed to this signal (directive schema's message_ids field) -
    a single-message signal ends up with exactly one entry."""

    def __init__(self, asset, first_message_id, now):
        self.asset = asset
        self.direction = None
        self.expiry = None
        self.message_ids = [first_message_id]
        self.created_at = now
        self.updated_at = now

    def age_seconds(self, now):
        return now - self.created_at

    def is_complete(self):
        return bool(self.asset and self.direction)


class ChannelAssemblerState:
    """All of one channel's currently-pending sequences. `pending_by_asset`
    is keyed by normalized asset (the correlation dimension every real
    multi-step provider in the OPT SIGNALS research corpus actually
    uses) - `pending_by_reply_to` additionally indexes the same
    PendingSignal objects by the message_id a later reply should
    correlate to, when the provider's messages use Telegram's reply
    feature at all (most don't; this is a bonus signal when present, not
    a requirement)."""

    def __init__(self):
        self.pending_by_asset = {}
        self.pending_by_reply_to = {}

    def expire_stale(self, now, timeout_seconds):
        """Drops (and returns) every pending sequence older than
        timeout_seconds - directive: "configurable assembly timeout."
        An expired sequence is simply dropped, never force-completed
        with guessed fields."""
        expired = []
        for asset, sig in list(self.pending_by_asset.items()):
            if sig.age_seconds(now) > timeout_seconds:
                expired.append(sig)
                del self.pending_by_asset[asset]
                for mid in sig.message_ids:
                    self.pending_by_reply_to.pop(mid, None)
        return expired

    def add_pending(self, sig):
        self.pending_by_asset[sig.asset] = sig
        for mid in sig.message_ids:
            self.pending_by_reply_to[mid] = sig

    def resolve_for_entry(self, reply_to_message_id):
        """Which pending sequence a non-asset-only message should attach
        to: an explicit Telegram reply wins outright (directive: "reply
        relationships" as a correlation signal) when it points at a
        message this state is tracking; otherwise falls back to the
        single most-recently-announced pending sequence (directive:
        "chronological proximity") - correct for every provider observed
        so far (each only ever has one truly ACTIVE announcement in
        flight even when the raw text doesn't reply-link), and
        deliberately does NOT guess between two simultaneously pending
        sequences with no reply link (directive: "never combine two
        separate pending signals incorrectly" - returns None rather than
        picking arbitrarily when more than one is pending and there's no
        reply to disambiguate)."""
        if reply_to_message_id is not None:
            hit = self.pending_by_reply_to.get(reply_to_message_id)
            if hit is not None:
                return hit
        if len(self.pending_by_asset) == 1:
            return next(iter(self.pending_by_asset.values()))
        if len(self.pending_by_asset) > 1:
            return max(self.pending_by_asset.values(), key=lambda s: s.updated_at)
        return None


class SignalAssembler:
    """Owns one ChannelAssemblerState per channel_id - the object
    core/telegram_listener.py holds for the life of the process
    (module-level singleton there, exactly like the dict it replaces)."""

    def __init__(self):
        self._states = {}

    def _state_for(self, channel_id):
        if channel_id not in self._states:
            self._states[channel_id] = ChannelAssemblerState()
        return self._states[channel_id]

    def process_message(self, channel_id, message_id, text, reply_to_message_id=None,
                         assembly_timeout_seconds=DEFAULT_ASSEMBLY_TIMEOUT_SECONDS, now=None):
        """The one entry point: feed one incoming message in, get back a
        result dict describing what happened. Never raises for ordinary
        unparseable/noise text - that's an expected, common outcome
        (promotional copy, chit-chat), not a failure.

        Returns {"action": ..., "expired_assets": [...], ...} where
        action is one of:
        - "announced": this message was a bare asset announcement - now
          pending, waiting for its direction/expiry follow-up. Includes
          "asset".
        - "signal_ready": a complete signal (asset+direction, expiry
          optional) is ready to trade NOW - either because this one
          message was self-contained, or because it completed a pending
          announcement. Includes "asset"/"direction"/"expiry"/
          "message_ids"/"is_multi_message".
        - "no_signal": this message wasn't a signal at all (didn't match
          any known shape) - the normal, common case for chatter/promo.

        expired_assets (present on every result, usually empty) lists
        the asset(s) whose pending sequence timed out THIS call without
        ever completing - a genuine parse FAILURE distinct from ordinary
        chatter (this provider announced an asset and then never sent a
        usable follow-up in time), for callers tracking a real success-
        rate metric (core/provider_profile.py's graduation_status)."""
        now = now if now is not None else time.monotonic()
        state = self._state_for(channel_id)
        expired = [sig.asset for sig in state.expire_stale(now, assembly_timeout_seconds)]

        if not text or not text.strip():
            return {"action": "no_signal", "reason": "empty_message", "expired_assets": expired}

        # Step 1: a bare asset-only message - the generic shape
        # core/provider_language_learner.py's _asset_only already proved
        # out against multiple real providers (NTrade, OTC Pro Robot,
        # Pocket Option Signals) - not one hardcoded phrase.
        announced_asset = learner._asset_only(text)
        if announced_asset:
            sig = PendingSignal(announced_asset, message_id, now)
            state.add_pending(sig)
            return {"action": "announced", "asset": announced_asset, "message_id": message_id, "expired_assets": expired}

        # Step 2: does this message stand completely on its own (asset +
        # direction, with or without expiry)? Checked BEFORE trying to
        # complete a pending sequence - a message that names its OWN
        # asset is a new, independent signal, never a continuation of an
        # unrelated pending one for a different asset.
        standalone = parse_signal(text)
        if standalone:
            return {
                "action": "signal_ready", "asset": standalone["asset"], "direction": standalone["direction"],
                "expiry": standalone.get("expiry"), "message_ids": [message_id], "is_multi_message": False,
                "expired_assets": expired,
            }

        # Step 3: does this message complete a currently-pending
        # announcement (direction, optionally expiry, no asset of its
        # own)? parse_signal's carried_asset parameter does the real
        # extraction work; resolve_for_entry decides WHICH pending
        # sequence (if any) this message should attach to.
        pending = state.resolve_for_entry(reply_to_message_id)
        if pending is not None:
            completed = parse_signal(text, carried_asset=pending.asset)
            if completed:
                del state.pending_by_asset[pending.asset]
                for mid in pending.message_ids:
                    state.pending_by_reply_to.pop(mid, None)
                message_ids = pending.message_ids + [message_id]
                return {
                    "action": "signal_ready", "asset": completed["asset"], "direction": completed["direction"],
                    "expiry": completed.get("expiry"), "message_ids": message_ids, "is_multi_message": True,
                    "expired_assets": expired,
                }

        return {"action": "no_signal", "reason": "unrecognized", "expired_assets": expired}

    def pending_count(self, channel_id):
        return len(self._state_for(channel_id).pending_by_asset)

    def clear_channel(self, channel_id):
        """Drops all pending state for one channel - used when a source
        is disabled or reverted to observation, so a stale pending
        sequence from before the change can never complete into a trade
        after the fact."""
        self._states.pop(channel_id, None)

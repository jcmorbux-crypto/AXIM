"""Provider Profile lifecycle (Universal Signal Intelligence Engine,
2026-07-18 product directive) - the trading_mode state machine and
graduation criteria layered on top of core/database.py's
provider_profiles CRUD.

State machine (one-way except the explicit "send back" actions):
    observation -> demo_ready -> demo -> live
                       ^                  |
                       |__________________|
                (revert_to_observation, any state -> observation)

Every source starts in 'observation' (directive: "New or uncertain
sources must begin in Observation Mode"). Demo and Live are each a
SEPARATE explicit human approval - graduation only makes a source
ELIGIBLE for demo_ready, it never auto-advances to demo or live on its
own, and reaching 'demo' never implies 'live' is authorized either.
"""
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def graduation_status(profile):
    """Pure. Returns {"eligible": bool, "reasons": [...]} - reasons is
    empty iff eligible. Every threshold is read from the profile itself
    (graduation_min_signals/success_rate/confidence), not a hardcoded
    global - an operator can tighten or loosen a specific source's bar
    without touching every other profile."""
    reasons = []
    observed = profile["observed_signal_count"] or 0
    min_signals = profile["graduation_min_signals"]
    if observed < min_signals:
        reasons.append(f"needs {min_signals} observed signals (has {observed})")

    success_rate = (profile["parse_success_count"] / observed) if observed else 0.0
    min_success_rate = profile["graduation_min_success_rate"]
    if success_rate < min_success_rate:
        reasons.append(
            f"parse success rate {success_rate:.0%} is below the required {min_success_rate:.0%}"
        )

    coverage = profile["coverage"] or 0.0
    min_confidence = profile["graduation_min_confidence"]
    if coverage < min_confidence:
        reasons.append(
            f"pattern coverage {coverage:.0%} is below the required {min_confidence:.0%}"
        )

    if profile["drift_detected_at"]:
        reasons.append("format drift is currently flagged on this source - resolve it before graduating")

    return {"eligible": not reasons, "reasons": reasons}


class ProviderProfileError(ValueError):
    """Raised for an invalid trading_mode transition (e.g. approving
    live before demo, or graduating a source that doesn't meet
    criteria) - a real guard, not a formality, since a bad transition
    here would mean a source trades real money before it has earned
    that trust."""


def record_observed_signal(profile_id, parsed_successfully, changed_by=None):
    """Called once per message this source's assembler/parser actually
    attempted to interpret as a signal (not every raw message - a
    promotional/chatter message that was correctly recognized as "not a
    signal" is neither a hit nor a miss here). Increments the running
    counters graduation_status reads."""
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise ProviderProfileError(f"no provider profile with id {profile_id}")
    fields = {"observed_signal_count": (profile["observed_signal_count"] or 0) + 1}
    if parsed_successfully:
        fields["parse_success_count"] = (profile["parse_success_count"] or 0) + 1
    database.update_provider_profile(profile_id, changed_by=changed_by, reason="observed signal recorded", **fields)


def graduate_to_demo_ready(profile_id, changed_by=None):
    """observation -> demo_ready. Raises ProviderProfileError (not a
    silent no-op) if this profile doesn't actually meet the criteria -
    the caller (a scheduled check or a manual "Validate" click) must
    handle that explicitly, never swallow it."""
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise ProviderProfileError(f"no provider profile with id {profile_id}")
    if profile["trading_mode"] != "observation":
        raise ProviderProfileError(
            f"can only graduate from 'observation', this profile is '{profile['trading_mode']}'"
        )
    status = graduation_status(profile)
    if not status["eligible"]:
        raise ProviderProfileError("does not yet meet graduation criteria: " + "; ".join(status["reasons"]))
    database.update_provider_profile(
        profile_id, changed_by=changed_by, reason="graduated to demo_ready", trading_mode="demo_ready",
    )
    logger.info("provider_profile: profile_id=%s graduated to demo_ready", profile_id)


def approve_demo(profile_id, approved_by):
    """demo_ready -> demo. A real human decision (approved_by is
    required, not optional) - graduation makes a source ELIGIBLE, this
    is what actually turns trading on for it."""
    if not approved_by:
        raise ProviderProfileError("approve_demo requires approved_by - this is a real, attributable decision")
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise ProviderProfileError(f"no provider profile with id {profile_id}")
    if profile["trading_mode"] != "demo_ready":
        raise ProviderProfileError(
            f"can only approve demo from 'demo_ready', this profile is '{profile['trading_mode']}'"
        )
    database.update_provider_profile(
        profile_id, changed_by=approved_by, reason="demo trading approved",
        trading_mode="demo", demo_approved_by=approved_by,
        demo_approved_at=datetime.now().isoformat(),
    )
    logger.info("provider_profile: profile_id=%s approved for demo by %s", profile_id, approved_by)


def approve_live(profile_id, approved_by):
    """demo -> live. Explicitly SEPARATE from approve_demo (directive:
    "Live trading requires separate explicit approval") - reaching
    'demo' never implies this. Requires demo to have already been
    approved on THIS profile, not just any prior trading_mode value."""
    if not approved_by:
        raise ProviderProfileError("approve_live requires approved_by - this is a real, attributable decision")
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise ProviderProfileError(f"no provider profile with id {profile_id}")
    if profile["trading_mode"] != "demo":
        raise ProviderProfileError(
            f"can only approve live from 'demo', this profile is '{profile['trading_mode']}'"
        )
    if not profile["demo_approved_at"]:
        raise ProviderProfileError("this profile was never actually demo-approved - cannot skip to live")
    database.update_provider_profile(
        profile_id, changed_by=approved_by, reason="live trading approved",
        trading_mode="live", live_approved_by=approved_by,
        live_approved_at=datetime.now().isoformat(),
    )
    logger.info("provider_profile: profile_id=%s approved for LIVE by %s", profile_id, approved_by)


def revert_to_observation(profile_id, reason, changed_by=None):
    """Any state -> observation. Always allowed, from any trading_mode -
    the safe fallback for format drift or manual operator judgment.
    Does NOT clear demo_approved_at/live_approved_at (that history stays
    real - a source that already earned Live once and got reverted for
    drift shouldn't have to re-earn Demo from zero once the drift is
    resolved, only re-pass graduation and get a fresh approve_demo/
    approve_live)."""
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise ProviderProfileError(f"no provider profile with id {profile_id}")
    database.update_provider_profile(
        profile_id, changed_by=changed_by, reason=reason, trading_mode="observation",
    )
    logger.info("provider_profile: profile_id=%s reverted to observation: %s", profile_id, reason)

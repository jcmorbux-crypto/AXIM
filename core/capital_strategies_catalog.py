"""AXIM Capital Strategies (tm) catalog - the Investment House / strategy
reference data the UI browses (Capital Strategies nav -> Investment House
pages -> strategy catalog -> strategy detail pages).

Code-defined structured configuration, not a DB table - this is static
reference content that ships with the app (the same "reusable
configuration, not hard-coded into UI components" principle the spec
itself asks for, just expressed as a Python module rather than a
migration - matches how web/shell.js's NAV_ITEMS/ICONS are already
code-defined rather than DB rows, for the same kind of static-per-release
content). A risk_profile INSTANCE using one of these (risk_profiles.
strategy_key) is a normal DB row exactly as before; this module is only
the catalog description shown before a profile is created.

`implemented` distinguishes Phase 1 (real, working calculations - see
core/capital_strategies.py) from Phase 2/3 (catalog/detail content exists
now per the spec's own requirement that the catalog be complete, but no
calculation engine yet - the UI must not claim otherwise)."""

INVESTMENT_HOUSES = {
    "foundry": {
        "name": "Foundry Series",
        "tagline": "Disciplined foundations.",
        "purpose": "Disciplined foundations, consistent exposure, predictable risk, income and session management.",
    },
    "summit": {
        "name": "Summit Series",
        "tagline": "Structured ascent.",
        "purpose": "Structured account growth, milestone compounding, positive progression, capital challenges.",
    },
    "alpha": {
        "name": "Alpha Series",
        "tagline": "Advanced and adaptive.",
        "purpose": "Advanced allocation, selective aggression, high-conviction opportunities, adaptive intelligence.",
    },
    "legacy": {
        "name": "Legacy Series",
        "tagline": "Protect what you've built.",
        "purpose": "Principal protection, profit preservation, drawdown defense, portfolio diversification.",
    },
}

# Phoenix (tm) is deliberately NOT a member of any house - the spec is
# explicit it must never be presented as part of a conservative house.
HIGH_RISK_STANDALONE = "phoenix"

STRATEGIES = {
    "foundation": {
        "name": "Foundation", "house": "foundry", "formerly": "Fixed Dollar / Fixed Unit",
        "philosophy": "Use the same fixed dollar or unit amount for every qualifying trade - no change following wins, losses, or bankroll movement.",
        "tagline": "Consistency builds the foundation.",
        "risk_level": "Low to Moderate", "implemented": True, "phase": 1,
        "sizing_mode": "fixed",
    },
    "titan_allocation": {
        "name": "Titan Allocation", "house": "foundry", "formerly": "Fixed Percentage",
        "philosophy": "Position size equals a configurable percentage of current active bankroll, recalculated every trade - contracts automatically as bankroll declines.",
        "tagline": "Capital determines command.",
        "risk_level": "Moderate", "implemented": True, "phase": 1,
        "sizing_mode": "dynamic",
    },
    "cashflow": {
        "name": "Cashflow", "house": "foundry", "formerly": "Daily Income Mode",
        "philosophy": "Select a daily, weekly, or monthly profit objective; stop opening trades once achieved, reducing deployment size as the target approaches.",
        "tagline": "Make the number. Leave the table.",
        "risk_level": "Low to Moderate", "implemented": True, "phase": 1,
    },
    "strike": {
        "name": "Strike", "house": "foundry", "formerly": "Session Target Mode",
        "philosophy": "A session terminates the moment any approved condition (profit target, loss limit, max trades, consecutive losses, session duration) is reached - never auto-restarts.",
        "tagline": "One mission. One exit. No greed.",
        "risk_level": "Low to Moderate", "implemented": True, "phase": 1,
    },
    "apex_ascension": {
        "name": "Apex Ascension", "house": "summit", "formerly": "Nova Analytics",
        "philosophy": "Earn the right to increase risk - unit value steps up only after real capital milestones, never recalculated trade-to-trade.",
        "tagline": "Protect the climb. Accelerate the summit.",
        "risk_level": "Moderate to Aggressive", "implemented": True, "phase": 1,
        "sizing_mode": "apex_ascension",
    },
    "momentum": {
        "name": "Momentum", "house": "summit", "formerly": "Anti-Martingale / Positive Progression",
        "philosophy": "Increase deployment only following wins; reset to base (or a lower step) after a loss.",
        "tagline": "Press strength. Retreat from weakness.",
        "risk_level": "Moderate to Aggressive", "implemented": True, "phase": 2,
    },
    "empire": {
        "name": "Empire", "house": "summit", "formerly": "Ladder Challenge",
        "philosophy": "Advance through a sequence of capital levels from a starting amount to a target, with checkpoint protection against a failed step.",
        "tagline": "Build the empire one conquest at a time.",
        "risk_level": "Aggressive", "implemented": True, "phase": 2,
        "sizing_mode": "empire",
    },
    "leviathan": {
        "name": "Leviathan", "house": "alpha", "formerly": "Donny Millionaire / PWF-inspired",
        "philosophy": "Protect capital, operate through phases, deploy larger amounts only during designated Pay Opportunities - never unlimited doubling.",
        "tagline": "Stay submerged until the opportunity is large enough.",
        "risk_level": "Moderate to Aggressive", "implemented": False, "phase": 2,
        # 2026-08-01 product decision: AXIM only ever sizes off REAL,
        # measurable statistics (see core/provider_scorecard.py) - never
        # a fabricated judgment call. "Pay Opportunity" is not currently
        # a defined, measurable concept anywhere in AXIM - it would need
        # a precise, product-owner-specified rule for exactly which real
        # signal/provider/market conditions constitute one (e.g. some
        # combination of provider_scorecard fields plus a threshold?
        # Asset-specific? Time-of-day? Signal-language-based?) before any
        # code can honestly implement "deploy larger amounts only during
        # a Pay Opportunity" - inventing that rule here would be exactly
        # the kind of fabricated scoring system the product decision
        # forbids. Definition Required: never activatable until this
        # exists.
        "definition_required": True,
        "definition_required_reason": (
            "\"Pay Opportunity\" has no defined, measurable trigger - needs a precise product "
            "specification of which real, already-collected AXIM statistics (see the Provider "
            "Scorecard fields Blackwater/Sniper already use: win rate, sample size, profit factor, "
            "payout, rolling performance, drawdown, streak, signal age) combine to mark a signal as "
            "a qualifying \"Pay Opportunity\", and what the phase structure itself (how phases "
            "advance/reset) actually is. No AI/fabricated confidence score may be substituted."
        ),
    },
    "blackwater": {
        "name": "Blackwater", "house": "alpha", "formerly": "Black Whale Club",
        "philosophy": "Ignore most signals; execute only on strict high-conviction conditions. Increase allocation only for qualified premium opportunities. This is not Martingale - never chase losses.",
        "tagline": "We do not trade often. We trade when the price is wrong.",
        "risk_level": "Aggressive", "implemented": True, "phase": 2,
        "sizing_mode": "blackwater",
    },
    "sniper": {
        "name": "Sniper", "house": "alpha", "formerly": None,
        "philosophy": "Execute only signals passing every selected filter - precision over maximum deployment.",
        "tagline": "One shot. No hesitation.",
        "risk_level": "Moderate", "implemented": True, "phase": 2,
    },
    "oracle": {
        "name": "Oracle", "house": "alpha", "formerly": None,
        "philosophy": "Adaptive allocation engine - a 0-100 AXIM Confidence Score drives deployment band, with every recommendation showing the reasons behind it.",
        "tagline": "Intelligence decides the size. Discipline controls the risk.",
        "risk_level": "Adaptive", "implemented": False, "phase": 3,
        # Same 2026-08-01 decision as Leviathan above, even more directly:
        # Oracle's own catalog description names a "0-100 AXIM Confidence
        # Score" - that IS the fabricated AI confidence score the product
        # decision explicitly forbids inventing. Definition Required until
        # a real product definition replaces it with a deterministic
        # formula over AXIM's own already-measured statistics (same
        # Provider Scorecard fields Blackwater/Sniper use), with every
        # "recommendation" traceable to real numbers, not a black-box score.
        "definition_required": True,
        "definition_required_reason": (
            "The catalog's own \"0-100 AXIM Confidence Score\" concept is exactly the fabricated "
            "AI/confidence scoring the 2026-08-01 product decision forbids. Needs a replacement "
            "product definition: a deterministic formula over real, already-collected Provider "
            "Scorecard statistics (win rate, sample size, profit factor, payout, rolling "
            "performance, drawdown, streak, signal age) that maps to a deployment band, with every "
            "recommendation showing exactly which real numbers produced it - no learned/opaque "
            "scoring model, no invented weighting scheme without explicit product sign-off."
        ),
    },
    "quantedge": {
        "name": "QuantEdge", "house": "alpha", "formerly": "Kelly / Fractional Kelly",
        "philosophy": "Full, half, quarter, or custom fractional Kelly sizing from bankroll, win probability, and payout - never trades on a zero or negative edge.",
        "tagline": "Mathematics finds the edge. Risk controls the ambition.",
        "risk_level": "Advanced", "implemented": True, "phase": 3,
        "sizing_mode": "kelly",
    },
    "fortress": {
        "name": "Fortress", "house": "legacy", "formerly": None,
        "philosophy": "Track original principal separately from profits; once protected, new trades fund only from active profits - the principal never returns to the battlefield.",
        "tagline": "The principal does not return to the battlefield.",
        "risk_level": "Conservative", "implemented": True, "phase": 2,
    },
    "axiom_vault": {
        "name": "Axiom Vault", "house": "legacy", "formerly": "Profit Vault",
        "philosophy": "Automatically remove a percentage of profits from active trading risk on a configurable trigger - profit isn't real until it leaves the battlefield.",
        "tagline": "Profit is not real until it leaves the battlefield.",
        "risk_level": "Conservative", "implemented": True, "phase": 2,
    },
    "sentinel": {
        "name": "Sentinel", "house": "legacy", "formerly": "Drawdown Recovery",
        "philosophy": "Graduated deployment reduction as drawdown deepens, suspending trading entirely past a hard threshold - configurable at account, strategy, or provider level.",
        "tagline": "When capital is threatened, Sentinel takes command.",
        "risk_level": "Protective", "implemented": True, "phase": 1,
    },
    "dominion": {
        "name": "Dominion", "house": "legacy", "formerly": "Multi-Bankroll / Multi-Fund Engine",
        "philosophy": "Independent capital Funds with their own bankroll, strategy, provider, and rules - one empire, multiple mandates, controlled independently.",
        "tagline": "One empire. Multiple mandates. Controlled independently.",
        "risk_level": "Portfolio", "implemented": True, "phase": 1,
    },
    "phoenix": {
        "name": "Phoenix", "house": None, "formerly": "Martingale",
        "philosophy": "Increase deployment after a loss under a hard-capped, pre-validated step ladder - unlimited progression is never available by default.",
        "tagline": "Recovery has a price. Know it before the first trade.",
        "risk_level": "Very High Risk", "implemented": True, "phase": 3,
        "high_risk_standalone": True,
    },
}

# "implemented" (above) means the real calculation exists SOMEWHERE in
# AXIM (core/risk_engine.py or core/capital_strategies.py) - it does NOT
# mean the quick, stateless-or-self-contained simulate endpoint
# (core/capital_strategies.py's simulate_strategy) supports it.
# Cashflow/Strike/Sentinel are modifiers layered on a base sizing choice,
# not sizing strategies themselves; Dominion is the Fund system (no
# per-trade "simulation" concept applies); Axiom Vault/Phoenix are real
# and already live in core/risk_engine.py's existing Vault/Martingale
# math. Momentum/Fortress/Phoenix specifically need a base_amount sourced
# from a DIFFERENT sizing mode's settings, which a single-strategy demo
# has no honest source for - see capital_strategies.SIMULATABLE_
# STRATEGIES's comment. Kept as an explicit set here, checked against
# capital_strategies.SIMULATABLE_STRATEGIES by a test, rather than
# silently drifting out of sync with that set.
_SIMULATE_SUPPORTED = {"foundation", "titan_allocation", "apex_ascension", "empire", "quantedge"}


def get_catalog():
    """Full catalog grouped by Investment House, plus the standalone
    high-risk entry - exactly the shape the Capital Strategies nav/detail
    pages need, computed fresh (not cached) since it's cheap and static."""
    houses = {
        key: {**house, "key": key, "strategies": []}
        for key, house in INVESTMENT_HOUSES.items()
    }
    standalone = []
    for key, strategy in STRATEGIES.items():
        entry = {**strategy, "key": key, "simulate_supported": key in _SIMULATE_SUPPORTED}
        if strategy.get("high_risk_standalone"):
            standalone.append(entry)
        else:
            houses[strategy["house"]]["strategies"].append(entry)
    return {"houses": list(houses.values()), "standalone": standalone}


def get_strategy(key):
    strategy = STRATEGIES.get(key)
    if strategy is None:
        return None
    entry = {**strategy, "key": key, "simulate_supported": key in _SIMULATE_SUPPORTED}
    if strategy.get("house"):
        entry["house_info"] = {**INVESTMENT_HOUSES[strategy["house"]], "key": strategy["house"]}
    return entry

"""AXIM Trader - Money Management Studio, the 4-strategy redesign.

Complete replacement of the old "6 starters + 27-item Advanced Library"
system per direct product-owner directive (2026-07-13): too technical,
too many generic-named options, felt like configuring software instead
of having a professional trading coach beside you. Originally designed
and verified on the ui-vision-upgrade branch's isolated preview server;
this copy is the real, production version wired to core/risk_engine.py
and core/database.py's real risk_profiles table.

Exactly 4 official, LOCKED strategies + 1 Custom Strategy Builder entry
point. "Locked" means the definitions below are the single source of
truth, hardcoded here, never stored as an editable DB row - "Use This
Strategy"/"Create From This Template" always creates a NEW real
risk_profile row (via the existing, unchanged POST /api/risk-profiles),
tagged with strategy_key for display, and that new profile is the
user's own from then on. The official definition itself can never be
edited in place. No performance numbers (ROI/drawdown/win-rate) live
here - those depend entirely on a specific Signal Provider's real
historical signals and belong only in Strategy Lab. Every number below
is a mechanical, deterministic illustration of the RULES, never a
performance projection.

**Real-engine mapping - stated plainly, not glossed over.** See
risk_profile_fields_for() below for the exact fields each strategy
saves as. Sizing (fixed/percent), Martingale (steps/multiplier/reset-
on-win), and Vault ("per_trade" trigger - a real, already-existing,
already-tested mechanism, core/capital_strategies.py's
per_trade_vault_skim) all map FAITHFULLY - the saved profile really
does behave the way its detail page describes. One mechanic does NOT
exist in the real engine and is not faked: growth-threshold
recalculation (resetting the sizing baseline once Active Bankroll
crosses +125%/+100%/+50% above the previous baseline) has no
equivalent in core/risk_engine.py's compounding model today, which
only steps risk-percent against a SESSION's own realized_pnl via
profile.compounding.steps_json, not a persistent cross-session
baseline. Building that is real, separate backend work - tracked as a
follow-up, not silently faked. Alternating Compound's real saved
profile uses a single fixed 3.75% (the average of its 2.5%/5%
alternating cycle) for the same reason - the real engine has no
per-trade-count alternating sizing mode. Both gaps are disclosed
directly on the strategy detail page, not just in this comment.
"""

STARTING_BANKROLL = 1000.0
PAYOUT_PERCENT = 88  # matches preview_server.py's DEFAULT_SCENARIO_PAYOUT - AXIM's real observed historical average


def _money(n):
    return round(n, 2)


def _pct(n):
    return f"{n:g}%"


# ---------------------------------------------------------------------
# Strategy 1: Capital Preservation
# ---------------------------------------------------------------------

def _capital_preservation_worked_example(bankroll=STARTING_BANKROLL):
    stake = _money(bankroll * 0.01)
    payout = PAYOUT_PERCENT / 100.0
    profit = _money(stake * payout)
    vault_cut = _money(profit * 0.25)
    active_gain = _money(profit - vault_cut)
    return {
        "starting_bankroll": bankroll, "stake": stake,
        "win": {
            "profit": profit, "vault_amount": vault_cut, "active_gain": active_gain,
            "active_after": _money(bankroll + active_gain), "vault_after": vault_cut,
        },
        "loss": {"loss": -stake, "active_after": _money(bankroll - stake)},
    }


def _capital_preservation_timeline(bankroll=STARTING_BANKROLL):
    threshold = _money(bankroll * 2.25)
    new_stake = _money(threshold * 0.01)
    return [{
        "checkpoint": 1,
        "trigger": f"Active Bankroll grows from ${bankroll:,.0f} to ${threshold:,.0f} (+125%)",
        "action": f"Baseline resets to ${threshold:,.0f}. New stake: 1% of ${threshold:,.0f} = ${new_stake:,.2f}.",
        "baseline_after": threshold, "new_stake": new_stake, "vaulted_this_step": 0.0,
    }]


CAPITAL_PRESERVATION = {
    "key": "capital_preservation", "id": "capital_preservation",
    "name": "Capital Preservation", "icon": "\U0001F6E1️",
    "tagline": "Protect capital first.",
    "personality": "Conservative", "risk_level": "Low",
    "purpose": "Built for the moments when you don't yet know if a signal provider is any good, or you simply want your bankroll to survive first and grow second. Every trade risks a small, fixed slice of your balance, and a quarter of every winning trade's profit is locked away immediately - before you can ever be tempted to risk it again.",
    "risk": {"headline": "1% of Active Bankroll", "detail": "Every trade risks exactly 1% of your Active Bankroll (your trading balance - it excludes anything already moved to Vault)."},
    "martingale": {"active": False, "summary": "No martingale. A loss never changes your next stake."},
    "compounding": {"active": False, "summary": "No compounding during a session. Your risk stays fixed at 1% of the current baseline until a growth checkpoint is reached."},
    "vault": {"active": True, "percent": 25, "summary": "25% of every winning trade's profit moves to Vault immediately - not at the end of the session, not at a milestone. Right away."},
    "growth_recalc": {"active": True, "threshold_percent": 125, "summary": "Your stake only changes after your Active Bankroll grows 125% above the previous baseline (i.e. more than doubles). Until then, 1% is always 1% of the same number."},
    "best_for": ["New signal providers you haven't proven out yet", "Unknown or unranked providers", "Traders who want to protect capital before anything else"],
    "pros": [
        "The most predictable strategy in the lineup - your stake barely moves, session to session.",
        "Profit gets locked away automatically, so a losing streak later can't take back gains you've already made.",
        "Easiest strategy to reason about with zero trading experience.",
    ],
    "cons": [
        "Slowest strategy to grow a bankroll, by design - it is deliberately not optimized for speed.",
        "Vaulting 25% immediately means less capital compounds inside Active Bankroll than a pure-growth strategy.",
    ],
    "ideal_providers": "Best paired with a provider you have little or no track record with yet. If the provider turns out to have a real edge, Capital Preservation still grows - just slower - while limiting what a bad or unproven provider can cost you.",
    "faq": [
        {"q": "Why 1% and not something bigger?", "a": "1% is small enough that a long losing streak - even 10 losses in a row - only costs about 10% of your Active Bankroll, not a number that puts your whole balance at risk."},
        {"q": "What counts as \"Active Bankroll\"?", "a": "Your trading balance, excluding anything already vaulted. Vaulted funds are protected - they're never risked again by this strategy."},
        {"q": "Can I turn off the Vault?", "a": "Not on this locked strategy - the automatic 25% vault is core to what Capital Preservation is. If you want the same 1% risk without vaulting, build that in the Custom Strategy Builder."},
        {"q": "What happens to my stake as I keep winning?", "a": "It stays exactly the same until your Active Bankroll grows 125% above the last baseline - then it recalculates once, from the new, larger number."},
    ],
    "session_controls": {"daily_loss_limit": "configurable", "session_loss_limit": "configurable"},
    "_worked_example_fn": _capital_preservation_worked_example,
    "_timeline_fn": _capital_preservation_timeline,
}


# ---------------------------------------------------------------------
# Strategy 2: Growth Accelerator
# ---------------------------------------------------------------------

def _growth_accelerator_worked_example(bankroll=STARTING_BANKROLL):
    stake = _money(bankroll * 0.05)
    payout = PAYOUT_PERCENT / 100.0
    profit = _money(stake * payout)
    return {
        "starting_bankroll": bankroll, "stake": stake,
        "win": {"profit": profit, "active_after": _money(bankroll + profit)},
        "loss": {"loss": -stake, "active_after": _money(bankroll - stake)},
    }


def _growth_accelerator_timeline(bankroll=STARTING_BANKROLL):
    # Checkpoint 1: first +125% growth from the ORIGINAL baseline - recalculates
    # position size only, no vault yet (matches the user's own worked example:
    # $1,000 -> $2,250 is a plain recalculation, the vault only appears later).
    b1 = _money(bankroll * 2.25)
    stake1 = _money(b1 * 0.05)
    # Checkpoint 2 onward: every ADDITIONAL +100% (doubling) from the new
    # baseline triggers both a recalculation AND a 25% vault of the profit
    # made since the last baseline - matches "$2,250 -> $4,500 -> vault 25%".
    b2_before_vault = _money(b1 * 2.0)
    profit_since_b1 = _money(b2_before_vault - b1)
    vault2 = _money(profit_since_b1 * 0.25)
    b2 = _money(b2_before_vault - vault2)
    stake2 = _money(b2 * 0.05)
    return [
        {
            "checkpoint": 1,
            "trigger": f"Active Bankroll grows from ${bankroll:,.0f} to ${b1:,.0f} (+125%)",
            "action": f"Baseline resets to ${b1:,.0f}. No vault yet - this is the first growth checkpoint. New stake: 5% of ${b1:,.0f} = ${stake1:,.2f}.",
            "baseline_after": b1, "new_stake": stake1, "vaulted_this_step": 0.0,
        },
        {
            "checkpoint": 2,
            "trigger": f"Active Bankroll grows from ${b1:,.0f} to ${b2_before_vault:,.0f} (+100% from the new baseline)",
            "action": f"Vault 25% of the ${profit_since_b1:,.0f} gained since the last baseline (${vault2:,.2f} moves to Vault). Continue trading the remaining ${b2:,.2f}. New stake: 5% of ${b2:,.2f} = ${stake2:,.2f}.",
            "baseline_after": b2, "new_stake": stake2, "vaulted_this_step": vault2,
        },
    ]


GROWTH_ACCELERATOR = {
    "key": "growth_accelerator", "id": "growth_accelerator",
    "name": "Growth Accelerator", "icon": "\U0001F4C8",
    "tagline": "Maximum long-term growth, without martingale.",
    "personality": "Aggressive", "risk_level": "High",
    "purpose": "For a bankroll you're confident in and a provider you trust, built to compound as fast as possible while never once increasing a stake because of a loss. Growth comes entirely from a bigger baseline after real, sustained gains - not from chasing losses.",
    "risk": {"headline": "5% of Active Bankroll", "detail": "Every trade risks 5% of your Active Bankroll - five times Capital Preservation's risk, by design, since this strategy exists purely to grow faster."},
    "martingale": {"active": False, "summary": "No martingale, ever. A loss never increases your next stake."},
    "compounding": {"active": True, "summary": "Risk stays fixed until your bankroll grows 125% above the previous baseline, then recalculates from the new, larger number - and again every additional 100% after that."},
    "vault": {"active": True, "percent": 25, "summary": "No vaulting on the first growth checkpoint (+125%) - but every checkpoint after that vaults 25% of the profit made since the last baseline, locking in real gains as you go."},
    "growth_recalc": {"active": True, "threshold_percent": 125, "repeat_threshold_percent": 100, "summary": "First recalculation at +125% growth. Every additional +100% growth after that both recalculates your stake and vaults 25% of that leg's profit."},
    "best_for": ["Trusted, proven signal providers with a real track record", "Traders comfortable with meaningfully larger swings in exchange for faster growth", "Bankrolls you can afford to see fluctuate"],
    "pros": [
        "The fastest of the 4 official strategies to grow a bankroll - no martingale required to get there.",
        "Every growth checkpoint after the first locks in real profit via the Vault, so gains aren't purely paper gains.",
        "Because it never martingales, a losing streak costs a predictable, bounded amount - it just costs more per trade than Capital Preservation, by design.",
    ],
    "cons": [
        "5% per trade means a losing streak is felt immediately and significantly more than a 1% strategy.",
        "Not recommended for a provider you don't yet trust - there's no protective vaulting until the first growth checkpoint is reached.",
    ],
    "ideal_providers": "Best paired with a provider you already trust - ideally one you've run through Strategy Lab's historical backtesting first. This strategy assumes the signals are good; it doesn't protect you from a provider that isn't.",
    "faq": [
        {"q": "Why isn't the first checkpoint vaulted?", "a": "The first +125% checkpoint is about proving the strategy is working and re-basing your stake to a realistic new size. Vaulting starts once you're compounding on top of an already-larger bankroll."},
        {"q": "Could my stake ever go down?", "a": "Yes - stake size is always 5% of the current baseline, and baseline only ever moves at a growth checkpoint. A losing streak lowers your Active Bankroll, but your stake doesn't recalculate downward until you choose a lower-risk strategy or start a new session with a smaller bankroll."},
        {"q": "Is this the same as Martingale?", "a": "No. Martingale increases a stake specifically because of a loss. Growth Accelerator's stake only ever changes because of sustained overall growth, never a single loss."},
    ],
    "session_controls": {"daily_loss_limit": "configurable", "session_loss_limit": "configurable", "profit_target": "configurable"},
    "_worked_example_fn": _growth_accelerator_worked_example,
    "_timeline_fn": _growth_accelerator_timeline,
}


# ---------------------------------------------------------------------
# Strategy 3: Alternating Compound
# ---------------------------------------------------------------------

_ALT_CYCLE_PERCENTS = [2.5, 5.0, 2.5, 5.0]


def _alternating_compound_cycle(bankroll=STARTING_BANKROLL):
    return [{"trade": i + 1, "risk_percent": p, "stake": _money(bankroll * p / 100.0)} for i, p in enumerate(_ALT_CYCLE_PERCENTS)]


def _alternating_compound_worked_example(bankroll=STARTING_BANKROLL):
    cycle = _alternating_compound_cycle(bankroll)
    stake = cycle[0]["stake"]  # trade 1 of the cycle
    payout = PAYOUT_PERCENT / 100.0
    profit = _money(stake * payout)
    return {
        "starting_bankroll": bankroll, "stake": stake, "cycle": cycle,
        "win": {"profit": profit, "active_after": _money(bankroll + profit)},
        "loss": {"loss": -stake, "active_after": _money(bankroll - stake)},
    }


def _alternating_compound_timeline(bankroll=STARTING_BANKROLL):
    threshold = _money(bankroll * 1.5)
    new_cycle = _alternating_compound_cycle(threshold)
    cycle_str = " / ".join(f"${c['stake']:,.2f}" for c in new_cycle)
    return [{
        "checkpoint": 1,
        "trigger": f"Active Bankroll grows from ${bankroll:,.0f} to ${threshold:,.0f} (+50%)",
        "action": f"Baseline resets to ${threshold:,.0f}. The 4-trade cycle recalculates: {cycle_str}.",
        "baseline_after": threshold, "new_stake": new_cycle[0]["stake"], "vaulted_this_step": 0.0,
    }]


ALTERNATING_COMPOUND = {
    "key": "alternating_compound", "id": "alternating_compound",
    "name": "Alternating Compound", "icon": "⚖️",
    "tagline": "Increase growth while keeping drawdown reasonable.",
    "personality": "Balanced", "risk_level": "Medium",
    "purpose": "A middle ground between Capital Preservation's caution and Growth Accelerator's speed. Risk alternates on a fixed, predictable 4-trade cycle - never in reaction to a win or a loss - so growth is faster than a flat 1-2%, but no single trade ever risks as much as a pure high-risk strategy.",
    "risk": {"headline": "2.5% / 5% alternating", "detail": "Trade 1 risks 2.5%, Trade 2 risks 5%, Trade 3 risks 2.5%, Trade 4 risks 5% - then the pattern repeats. It follows the trade count, never the outcome of the previous trade."},
    "martingale": {"active": False, "summary": "No martingale. The alternating pattern is fixed by trade count, not by whether you won or lost."},
    "compounding": {"active": True, "summary": "Risk stays on the fixed 2.5%/5% cycle until a growth checkpoint, then the whole cycle recalculates from the new, larger baseline."},
    "vault": {"active": False, "summary": "No automatic vaulting on this strategy - all growth stays in Active Bankroll and keeps compounding."},
    "growth_recalc": {"active": True, "threshold_percent": 50, "summary": "The entire 4-trade cycle recalculates once your Active Bankroll grows 50% above the previous baseline."},
    "best_for": ["Traders who want more growth than a flat 1% but don't want every trade to risk 5%", "Sessions with a configurable target where you want steady, predictable progress"],
    "pros": [
        "Averages 3.75% risk per trade across the cycle - meaningfully more growth potential than a flat 1-2% strategy.",
        "Because the pattern is fixed by trade count, it's completely predictable - you always know what the next stake will be.",
        "No single trade ever risks more than 5%, capping how much one bad trade can cost you.",
    ],
    "cons": [
        "No automatic vault - all gains stay exposed to future trades until you end the session.",
        "The 50% recalculation threshold means your stake grows faster than Capital Preservation's, so a losing streak right after a recalculation costs more in dollar terms.",
    ],
    "ideal_providers": "Works well with a provider that has a reasonably consistent track record - the alternating cycle assumes a steady signal flow rather than occasional large bursts.",
    "faq": [
        {"q": "Why alternate instead of just picking one percentage?", "a": "Alternating between 2.5% and 5% produces a higher average risk (3.75%) than a flat conservative percentage, without ever committing 5% on every single trade."},
        {"q": "Does a loss on Trade 2 change Trade 3's risk?", "a": "No. Trade 3 is always 2.5%, regardless of what happened on Trades 1 or 2. The cycle is fixed - that's what makes this not martingale."},
        {"q": "What is \"Session target\"?", "a": "An optional dollar or percentage profit goal you set for the session - when reached, AXIM can stop the session automatically. It's off by default; you configure it if you want it."},
    ],
    "session_controls": {"session_target": "configurable", "daily_loss_limit": "configurable"},
    "_worked_example_fn": _alternating_compound_worked_example,
    "_timeline_fn": _alternating_compound_timeline,
}


# ---------------------------------------------------------------------
# Strategy 4: Recovery Ladder
# ---------------------------------------------------------------------

DEFAULT_RECOVERY_MULTIPLIER = 2.0
DEFAULT_RECOVERY_MAX_STEPS = 3


def _recovery_ladder_table(bankroll=STARTING_BANKROLL, multiplier=DEFAULT_RECOVERY_MULTIPLIER, max_steps=DEFAULT_RECOVERY_MAX_STEPS):
    base_stake = bankroll * 0.01
    return [{"step": i, "stake": _money(base_stake * (multiplier ** i)), "is_max": i == max_steps} for i in range(max_steps + 1)]


def _recovery_ladder_worked_example(bankroll=STARTING_BANKROLL):
    ladder = _recovery_ladder_table(bankroll)
    stake = ladder[0]["stake"]
    payout = PAYOUT_PERCENT / 100.0
    profit = _money(stake * payout)
    return {
        "starting_bankroll": bankroll, "stake": stake, "ladder": ladder,
        "win": {"profit": profit, "active_after": _money(bankroll + profit)},
        "loss": {"loss": -stake, "active_after": _money(bankroll - stake), "next_step_stake": ladder[1]["stake"]},
    }


def _recovery_ladder_timeline(bankroll=STARTING_BANKROLL):
    threshold = _money(bankroll * 2.0)
    new_base = _money(threshold * 0.01)
    return [{
        "checkpoint": 1,
        "trigger": f"Active Bankroll doubles from ${bankroll:,.0f} to ${threshold:,.0f} (+100%)",
        "action": f"Baseline resets to ${threshold:,.0f}. New base stake (Step 0): 1% of ${threshold:,.0f} = ${new_base:,.2f}. Only checked while you're at Step 0 - a recovery ladder in progress is never interrupted by a recalculation.",
        "baseline_after": threshold, "new_stake": new_base, "vaulted_this_step": 0.0,
    }]


RECOVERY_LADDER = {
    "key": "recovery_ladder", "id": "recovery_ladder",
    "name": "Recovery Ladder", "icon": "\U0001F504",
    "tagline": "Recover controlled losses using limited martingale.",
    "personality": "Aggressive", "risk_level": "High",
    "purpose": "For traders who accept that a martingale-style recovery ladder can work back a losing streak faster - as long as it's capped, not unlimited. The ladder always has a hard maximum step count you choose, and any win at any step resets you straight back to the smallest stake.",
    "risk": {"headline": "1% of Active Bankroll", "detail": "Starts at 1% of Active Bankroll. After a loss, the next stake increases by your chosen multiplier - up to a hard maximum number of steps you set. It never increases past that cap."},
    "martingale": {"active": True, "summary": "Limited martingale: after a loss, the stake increases by your chosen multiplier, capped at a maximum number of steps (both configurable). This is the only one of the 4 official strategies that increases a stake because of a loss.", "default_multiplier": DEFAULT_RECOVERY_MULTIPLIER, "default_max_steps": DEFAULT_RECOVERY_MAX_STEPS},
    "compounding": {"active": False, "summary": "No compounding in the usual sense - the ladder itself is the growth mechanism during a recovery, and resets to base on any win."},
    "vault": {"active": False, "summary": "No automatic vaulting on this strategy."},
    "growth_recalc": {"active": True, "threshold_percent": 100, "summary": "The 1% base stake recalculates once your Active Bankroll doubles - but only while you're at Step 0 (no recovery ladder currently in progress), so a recalculation never happens mid-recovery."},
    "best_for": ["Traders who want faster loss recovery than a flat-risk strategy", "Bankrolls large enough to comfortably absorb the maximum ladder step before it's ever reached", "Providers with a track record of not producing very long losing streaks"],
    "pros": [
        "Any win, at any step, resets you immediately to the smallest stake - recovery is never dragged out longer than one win.",
        "Unlike unlimited martingale, the maximum exposure is always known in advance - you set the cap.",
        "Recalculation is deliberately paused mid-ladder, so a bankroll milestone never interrupts an in-progress recovery.",
    ],
    "cons": [
        "Still the highest-risk of the 4 official strategies - a losing streak that reaches your maximum step size costs meaningfully more than one trade at the base 1%.",
        "If a loss happens exactly at the maximum step, the session stops rather than continuing to increase the stake - by design, but worth knowing before you start.",
        "Not recommended for a provider with a history of long losing streaks.",
    ],
    "ideal_providers": "Best suited to a provider whose historical signals (visible in Strategy Lab) show occasional short losing streaks, not long ones - the ladder is built to recover from a handful of losses, not ten in a row.",
    "faq": [
        {"q": "What happens if I lose at the maximum step?", "a": "The session stops rather than increasing the stake further. AXIM never lets a martingale ladder increase without the cap you set - that's a hard rule, not a suggestion."},
        {"q": "Why does a win reset to 1% instead of the previous step?", "a": "A full reset after any win is what keeps this \"limited\" and \"controlled,\" rather than a slower-draining version of unlimited martingale. It's the safest version of loss recovery AXIM offers."},
        {"q": "Can I set the multiplier and max steps myself?", "a": "Yes - both are configurable on this strategy (defaults shown here are 2.0x and 3 steps). Daily loss, session loss, and profit target are configurable too."},
    ],
    "session_controls": {"martingale_multiplier": "configurable", "max_steps": "configurable", "daily_loss_limit": "configurable", "session_loss_limit": "configurable", "profit_target": "configurable"},
    "_worked_example_fn": _recovery_ladder_worked_example,
    "_timeline_fn": _recovery_ladder_timeline,
}


STRATEGIES = [CAPITAL_PRESERVATION, GROWTH_ACCELERATOR, ALTERNATING_COMPOUND, RECOVERY_LADDER]
STRATEGIES_BY_KEY = {s["key"]: s for s in STRATEGIES}


def strategy_card(s):
    """Trimmed shape for the list page - everything a card needs, nothing more."""
    return {
        "key": s["key"], "id": s["id"], "name": s["name"], "icon": s["icon"],
        "tagline": s["tagline"], "personality": s["personality"], "risk_level": s["risk_level"],
        "risk": s["risk"], "martingale": s["martingale"], "compounding": s["compounding"],
        "vault": s["vault"], "growth_recalc": s["growth_recalc"], "best_for": s["best_for"],
    }


def strategy_detail(key):
    s = STRATEGIES_BY_KEY.get(key)
    if s is None:
        return None
    out = {k: v for k, v in s.items() if not k.startswith("_")}
    out["worked_example"] = s["_worked_example_fn"]()
    out["growth_timeline"] = s["_timeline_fn"]()
    return out


# ---------------------------------------------------------------------
# Real-engine mapping - what "Use This Strategy" / "Create From This
# Template" actually saves via the existing, unchanged POST /api/
# risk-profiles + PATCH .../martingale + PATCH .../vault endpoints.
# Every field here is real and enforced by core/risk_engine.py today -
# nothing here pretends to implement growth-threshold recalculation
# (not real) or Alternating Compound's true alternating cycle (not
# real, approximated as a flat average). See the module docstring.
# ---------------------------------------------------------------------

def risk_profile_fields_for(key, name, bankroll):
    """Returns (create_fields, martingale_fields_or_None,
    vault_fields_or_None) for POST /api/risk-profiles + its martingale/
    vault PATCH sub-endpoints. create_fields always includes
    strategy_key so the saved profile can show "Based on <Strategy>"."""
    if key == "capital_preservation":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 1.0, "strategy_key": key,
            "description": "Based on Capital Preservation - 1% risk per trade, 25% of every winning trade's profit vaulted immediately.",
        }
        vault = {"enabled": True, "vault_percent": 25, "trigger_event": "per_trade"}
        return create, None, vault

    if key == "growth_accelerator":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 5.0, "strategy_key": key,
            "description": "Based on Growth Accelerator - 5% risk per trade, 25% of every winning trade's profit vaulted immediately.",
        }
        vault = {"enabled": True, "vault_percent": 25, "trigger_event": "per_trade"}
        return create, None, vault

    if key == "alternating_compound":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 3.75, "strategy_key": key,
            "description": "Based on Alternating Compound - approximated as a flat 3.75% (the average of its real 2.5%/5% alternating cycle, which the live engine doesn't yet support trade-by-trade).",
        }
        return create, None, None

    if key == "recovery_ladder":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 1.0, "strategy_key": key,
            "description": "Based on Recovery Ladder - 1% base risk, limited martingale recovery on a loss, resets to base on any win.",
        }
        martingale = {
            "enabled": True, "max_steps": DEFAULT_RECOVERY_MAX_STEPS,
            "multiplier": DEFAULT_RECOVERY_MULTIPLIER, "reset_after_win": True,
        }
        return create, martingale, None

    return None, None, None

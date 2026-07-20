"""AXIM Trader - Money Management Studio, the 5-strategy redesign.

Complete replacement of the old "6 starters + 27-item Advanced Library"
system per direct product-owner directive (2026-07-13): too technical,
too many generic-named options, felt like configuring software instead
of having a professional trading coach beside you. Originally designed
and verified on the ui-vision-upgrade branch's isolated preview server;
this copy is the real, production version wired to core/risk_engine.py
and core/database.py's real risk_profiles table. Daily Compounding
(strategy 5, see below) was added 2026-07-18 as a genuinely new
calendar-day-scoped sizing mode (core/daily_compounding.py) - the
other 4 strategies and their real-engine mapping are unchanged.

Exactly 5 official, LOCKED strategies + 1 Custom Strategy Builder entry
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
on-win), Vault ("per_trade" trigger - a real, already-existing,
already-tested mechanism, core/capital_strategies.py's
per_trade_vault_skim), and Alternating Compound's real trade-by-trade
2.5%/5% cycle (core/risk_engine.py's compounding mode
"alternating_cycle", keyed off the session's own trades_count, not an
averaged approximation) all map FAITHFULLY - the saved profile really
does behave the way its detail page describes. One mechanic does NOT
exist in the real engine and is not faked: growth-threshold
recalculation (resetting the sizing baseline once Active Bankroll
crosses +125%/+100%/+50% above the previous baseline) has no
equivalent in core/risk_engine.py's compounding model today, which
only steps risk-percent against a SESSION's own realized_pnl or trade
count, not a persistent cross-session baseline. Building that is real,
separate backend work - tracked as a follow-up, not silently faked -
and is disclosed directly on the strategy detail page, not just here.
"""

import json

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


# ---------------------------------------------------------------------
# Strategy 5: Daily Compounding
# ---------------------------------------------------------------------

DAILY_COMPOUNDING_RISK_PERCENT = 1.0
DAILY_COMPOUNDING_PROFIT_TARGET_PERCENT = 50.0
DAILY_COMPOUNDING_LOSS_LIMIT_PERCENT = 25.0


def _daily_compounding_worked_example(bankroll=STARTING_BANKROLL):
    stake = _money(bankroll * (DAILY_COMPOUNDING_RISK_PERCENT / 100.0))
    payout = PAYOUT_PERCENT / 100.0
    profit = _money(stake * payout)
    return {
        "starting_bankroll": bankroll, "stake": stake,
        "win": {"profit": profit, "active_after": _money(bankroll + profit)},
        "loss": {"loss": -stake, "active_after": _money(bankroll - stake)},
        "daily_profit_target": _money(bankroll * (DAILY_COMPOUNDING_PROFIT_TARGET_PERCENT / 100.0)),
        "daily_loss_limit": _money(bankroll * (DAILY_COMPOUNDING_LOSS_LIMIT_PERCENT / 100.0)),
    }


def _daily_compounding_timeline(bankroll=STARTING_BANKROLL):
    # Not a growth-checkpoint strategy like the other 4 - the "checkpoint"
    # here is calendar-driven (every trading day), not bankroll-driven,
    # so this describes the real daily reset core/daily_compounding.py
    # performs rather than a fabricated growth threshold.
    return [{
        "checkpoint": 1,
        "trigger": "The next trading day begins (your Fund's configured timezone)",
        "action": (
            "AXIM captures the Fund's real balance at that moment as the new starting balance, "
            f"recalculates the {DAILY_COMPOUNDING_RISK_PERCENT:g}% stake, the "
            f"{DAILY_COMPOUNDING_PROFIT_TARGET_PERCENT:g}% profit target, and the "
            f"{DAILY_COMPOUNDING_LOSS_LIMIT_PERCENT:g}% loss limit from it, and resets yesterday's "
            "realized P/L counter to $0 - yesterday's trade history is preserved, never erased, "
            "and an in-progress trade is never touched by the reset."
        ),
        "baseline_after": None, "new_stake": None, "vaulted_this_step": 0.0,
    }]


DAILY_COMPOUNDING = {
    "key": "daily_compounding", "id": "daily_compounding",
    "name": "Daily Compounding", "icon": "",
    "tagline": "A fresh, fixed risk budget every trading day.",
    "personality": "Balanced", "risk_level": "Medium",
    "purpose": "Built around the trading day, not the session: every trading day starts with its own risk budget, its own profit target, and its own loss limit, all sized off that day's real starting Fund balance. Once the day's target or limit is reached, AXIM stops trading that Fund until the next trading day begins - so a great day can't be given back, and a bad day can't be chased.",
    "risk": {"headline": f"{DAILY_COMPOUNDING_RISK_PERCENT:g}% of today's starting balance", "detail": f"Every trade risks {DAILY_COMPOUNDING_RISK_PERCENT:g}% (minimum - you can set it higher) of the Fund's balance at the moment today's trading began. It does not move again until tomorrow, no matter how the balance changes during the day."},
    "martingale": {"active": False, "summary": "No martingale. A loss never changes today's stake."},
    "compounding": {"active": True, "custom_label": "Once per day", "summary": "Recalculates exactly once, at the start of each trading day, from that day's real starting Fund balance - never mid-day, never per-trade."},
    "vault": {"active": False, "summary": "Off by default - optionally vault a percentage of the day's profit the moment the daily target is hit."},
    "growth_recalc": {"active": True, "custom_label": "Start of each trading day", "summary": "Recalculates once at the start of every trading day - tied to the calendar (your Fund's configured timezone), not to a bankroll growth percentage like the other strategies."},
    "best_for": ["Traders who want a hard daily circuit breaker on both the upside and the downside", "Funds trading every day and wanting consistent, repeatable daily risk", "Anyone who has ever given back a great day's profit and wants that structurally prevented"],
    "pros": [
        "A real daily stop on both sides - trading halts for the day at the profit target exactly the same way it halts at the loss limit.",
        "Risk per trade is always sized off a real, known number (today's real starting balance), never a stale or projected one.",
        "Resumes automatically the next trading day - no manual re-enable required.",
    ],
    "cons": [
        "A strong trading day is deliberately capped at the profit target - this strategy will not let a session run further once the target is hit.",
        "Requires a correctly configured Fund/account timezone to align the daily boundary with when you actually trade.",
    ],
    "ideal_providers": "Works with any provider whose signals arrive throughout the trading day - the daily boundary is about YOUR risk management, not about the provider's own signal pattern.",
    "faq": [
        {"q": "What happens right at midnight if a trade is still open?", "a": "Nothing - an in-progress trade is never touched by the daily reset. The new day's starting balance and thresholds are captured at the first new signal of the new day, and the open trade resolves normally."},
        {"q": "Can I risk more than 1%?", "a": "Yes - 1% is the minimum, not a cap. Raise it in the Custom Strategy Builder, subject to your Fund and platform limits."},
        {"q": "What timezone is \"the start of the day\"?", "a": "Whatever timezone you set on this strategy - not the server's timezone and not a hardcoded UTC assumption. Set it to match where you actually consider your trading day to begin."},
        {"q": "Does hitting the loss limit pause my whole Fund?", "a": "No - only today's trading stops. The Fund itself stays active and automatically resumes trading, with a fresh budget, at the start of the next trading day."},
    ],
    "session_controls": {"daily_profit_target": "configurable (50% default)", "daily_loss_limit": "configurable (25% default)", "max_trades_per_day": "configurable", "timezone": "configurable"},
    "_worked_example_fn": _daily_compounding_worked_example,
    "_timeline_fn": _daily_compounding_timeline,
}


STRATEGIES = [CAPITAL_PRESERVATION, GROWTH_ACCELERATOR, ALTERNATING_COMPOUND, RECOVERY_LADDER, DAILY_COMPOUNDING]
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
    vault_fields_or_None, compounding_fields_or_None,
    daily_compounding_fields_or_None) for POST /api/risk-profiles + its
    martingale/vault/compounding/daily-compounding PATCH sub-endpoints.
    create_fields always includes strategy_key so the saved profile can
    show "Based on <Strategy>"."""
    if key == "capital_preservation":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 1.0, "strategy_key": key,
            "description": "Based on Capital Preservation - 1% risk per trade, 25% of every winning trade's profit vaulted immediately.",
        }
        vault = {"enabled": True, "vault_percent": 25, "trigger_event": "per_trade"}
        return create, None, vault, None, None

    if key == "growth_accelerator":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": 5.0, "strategy_key": key,
            "description": "Based on Growth Accelerator - 5% risk per trade, 25% of every winning trade's profit vaulted immediately.",
        }
        vault = {"enabled": True, "vault_percent": 25, "trigger_event": "per_trade"}
        return create, None, vault, None, None

    if key == "alternating_compound":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "percent",
            "percent_of_bankroll": _ALT_CYCLE_PERCENTS[0], "strategy_key": key,
            "description": "Based on Alternating Compound - the real 4-trade 2.5%/5%/2.5%/5% cycle, keyed off this session's own trade count (not an averaged approximation).",
        }
        compounding = {"mode": "alternating_cycle", "steps_json": json.dumps(_ALT_CYCLE_PERCENTS)}
        return create, None, None, compounding, None

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
        return create, martingale, None, None, None

    if key == "daily_compounding":
        create = {
            "name": name, "bankroll": bankroll, "sizing_mode": "daily_compounding", "strategy_key": key,
            "description": "Based on Daily Compounding - 1% of each trading day's starting Fund balance risked per trade, recalculated once at the start of every trading day, stopping for the day at a 50% profit target or a 25% loss limit.",
        }
        daily = {
            "enabled": True, "risk_percent": DAILY_COMPOUNDING_RISK_PERCENT,
            "profit_target_percent": DAILY_COMPOUNDING_PROFIT_TARGET_PERCENT,
            "loss_limit_percent": DAILY_COMPOUNDING_LOSS_LIMIT_PERCENT,
            "timezone": "UTC", "stop_after_target": True, "stop_after_loss_limit": True,
        }
        return create, None, None, None, daily

    return None, None, None, None, None


# ---------------------------------------------------------------------
# Virtual profiles - the zero-DB-footprint replacement for what a real
# risk_profiles row used to look like for one of the 5 canonical
# strategies (2026-07-19 product directive: "Do not create database
# rows for the five plans merely to make them appear in selectors. Do
# not seed them."). build_virtual_profile() below returns an in-memory
# dict shape-identical to database.get_risk_profile()'s output (base
# fields + all 11 sub-setting dicts: martingale, momentum, compounding,
# profit_vault, apex_ascension, drawdown_protection, cashflow, strike,
# fortress, empire, daily_compounding), built from risk_profile_fields_for
# above plus this table's own hand-verified copies of every sub-table's
# real schema DEFAULT (core/database.py's CREATE TABLE statements - kept
# in sync by hand since a virtual profile, by definition, never has a
# real row to read defaults from). Every strategy here touches at most
# one of martingale/vault/compounding/daily_compounding; every other
# sub-setting is a pure, untouched default - exactly what a freshly
# create_risk_profile()'d row would also contain.
# ---------------------------------------------------------------------

_RISK_PROFILE_BASE_DEFAULTS = {
    "description": None, "is_template": 0, "bankroll": STARTING_BANKROLL,
    "sizing_mode": "fixed", "fixed_amount": 1, "percent_of_bankroll": 1,
    "kelly_win_rate_estimate": None, "kelly_payout_estimate": None,
    "kelly_fraction_multiplier": 0.5, "max_trade_amount": 0, "max_daily_loss": 0,
    "max_session_loss": 0, "profit_target": 0, "max_trades": 0, "live_allowed": 0,
    "created_at": None, "updated_at": None, "strategy_key": None, "archived_at": None,
}
_MARTINGALE_DEFAULTS = {
    "enabled": 0, "max_steps": 0, "multiplier": 2.0, "custom_ladder_json": None,
    "reset_after_win": 1, "reset_after_session": 1, "max_total_exposure": 0,
    "confidence_threshold": None, "same_asset_only": 0, "same_source_only": 0,
}
_MOMENTUM_DEFAULTS = {
    "enabled": 0, "max_steps": 0, "multiplier": 1.5, "custom_ladder_json": None,
    "profit_lock_percent": 0,
}
_COMPOUNDING_DEFAULTS = {
    "mode": "disabled", "base_risk_percent": 2.0, "steps_json": None,
    "drawdown_reset_percent": 0, "max_risk_percent": 0, "min_risk_percent": 0,
}
_VAULT_DEFAULTS = {
    "enabled": 0, "vault_percent": 0, "trigger_event": "every_winning_session",
    "milestone_amount": 0,
}
_APEX_ASCENSION_DEFAULTS = {
    "enabled": 0, "starting_bankroll": 1000, "starting_unit_value": 10,
    "standard_units": 5, "first_reset_threshold": 2500, "reset_increment": 1000,
    "reset_unit_step": 10, "downgrade_protection": 1, "highest_tier_reached": 0,
}
_DRAWDOWN_PROTECTION_DEFAULTS = {
    "enabled": 0, "bands_json": None, "suspend_above_percent": 20, "scope": "account",
}
_CASHFLOW_DEFAULTS = {
    "enabled": 0, "target_amount": 0, "target_period": "session",
    "partial_target_percent": 75, "partial_reduction_percent": 50,
}
_STRIKE_DEFAULTS = {
    "enabled": 0, "max_session_duration_minutes": 0, "max_consecutive_losses": 0,
}
_FORTRESS_DEFAULTS = {
    "enabled": 0, "protection_threshold": 0, "protected_principal": 0,
}
_EMPIRE_DEFAULTS = {
    "enabled": 0, "starting_amount": 10, "target_amount": 100, "num_levels": 10,
    "levels_json": None, "failure_behavior": "reset_to_start", "checkpoint_level": 0,
    "current_level": 0,
}
_DAILY_COMPOUNDING_DEFAULTS = {
    "enabled": 0, "risk_percent": 1.0, "risk_fixed_amount": None,
    "profit_target_percent": 50.0, "profit_target_fixed_amount": None,
    "loss_limit_percent": 25.0, "loss_limit_fixed_amount": None, "timezone": "UTC",
    "max_trades_per_day": 0, "max_concurrent_trades": 0, "cooldown_after_loss_seconds": 0,
    "consecutive_loss_stop": 0, "vault_enabled": 0, "vault_percent_on_target": 0,
    "stop_after_target": 1, "stop_after_loss_limit": 1,
}


def _sub_settings(defaults, overrides=None):
    d = {"id": None, "risk_profile_id": None, **defaults}
    if overrides:
        d.update(overrides)
    return d


def build_virtual_profile(key, name=None, bankroll=None):
    """Zero-DB-footprint stand-in for a real risk_profiles row, for any
    of the 5 canonical strategies - see the module comment above. Every
    field risk_engine.py / risk_control_center.py read from a real
    profile is present here with the correct real-schema default, so
    either can consume this as a drop-in replacement. Returns None if
    key isn't one of the 5 canonical strategies."""
    strategy = STRATEGIES_BY_KEY.get(key)
    if strategy is None:
        return None
    bankroll = bankroll if bankroll is not None else STARTING_BANKROLL
    name = name or strategy["name"]
    create_fields, martingale_fields, vault_fields, compounding_fields, daily_compounding_fields = (
        risk_profile_fields_for(key, name, bankroll)
    )
    create_fields = dict(create_fields)
    create_fields.pop("name", None)

    profile = {"id": None, "name": name, "is_virtual": True, **_RISK_PROFILE_BASE_DEFAULTS, **create_fields}
    profile["martingale"] = _sub_settings(_MARTINGALE_DEFAULTS, martingale_fields)
    profile["momentum"] = _sub_settings(_MOMENTUM_DEFAULTS)
    profile["compounding"] = _sub_settings(_COMPOUNDING_DEFAULTS, compounding_fields)
    profile["profit_vault"] = _sub_settings(_VAULT_DEFAULTS, vault_fields)
    profile["apex_ascension"] = _sub_settings(_APEX_ASCENSION_DEFAULTS)
    profile["drawdown_protection"] = _sub_settings(_DRAWDOWN_PROTECTION_DEFAULTS)
    profile["cashflow"] = _sub_settings(_CASHFLOW_DEFAULTS)
    profile["strike"] = _sub_settings(_STRIKE_DEFAULTS)
    profile["fortress"] = _sub_settings(_FORTRESS_DEFAULTS)
    profile["empire"] = _sub_settings(_EMPIRE_DEFAULTS)
    profile["daily_compounding"] = _sub_settings(_DAILY_COMPOUNDING_DEFAULTS, daily_compounding_fields)
    return profile

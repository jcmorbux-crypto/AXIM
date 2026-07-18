"""Provider Language Learner (Phase 2 Priority #1: "Provider
Intelligence Engine") - automatically detects a new Telegram signal
provider's message format from a batch of real history, without
requiring a hand-written adapter per provider first.

This is a BATCH-mode analyzer (a full 30-day message history in,
signal+result records out) - deliberately separate from
parsers/signal_parser.py, which is the live, per-message, real-time
parser the Telegram listener uses to actually place trades and must
stay conservative and unchanged. Nothing here is ever imported by
core/telegram_listener.py, core/trade_coordinator.py, or
execution/pocket_executor.py - this module is analysis-only, exactly
like the research branch's own adapters before it (see
docs/OPT_SIGNALS_RESULT_MATCHING_REPORT.md's standing "never fabricate
confidence" discipline, which this inherits).

How it works: tries a library of PATTERN TEMPLATES against the real
message batch - each template encodes one structural shape actually
observed across the 12-provider OPT SIGNALS research corpus (single-
line compact signals, labeled multi-field blocks, two-step "asset then
direction" messages, HIGH/LOWER vocab, bilingual RU/EN mirrored lines,
result tokens in a dozen different real vocabularies). Every template
is SCORED against the batch (what fraction of messages it successfully
extracts a signal from); the best-scoring template above
MIN_VIABLE_COVERAGE is used, otherwise this honestly reports "no
pattern fits well enough" rather than force a bad one - the same
discipline as _find_valid_pair's own "never guess" comment.

This does not claim to solve every possible provider format (a
genuinely novel structure still needs a hand-built adapter, same as
the 12 providers already researched) - it solves the COMMON shapes,
consistent with the Phase 2 mandate: "little or no manual parser
engineering for common provider formats," not "zero engineering for
every format that will ever exist."
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))
from signal_parser import (  # noqa: E402
    _find_valid_pair, _CURRENCY_CODES, parse_signal, parse_asset_announcement,
)

# A provider whose best-scoring pattern still extracts a clean signal
# from fewer than this fraction of its real messages isn't a good
# automatic match - most of that channel's traffic is something else
# (promotional copy, session chatter, results) and forcing a low-
# coverage pattern would mostly manufacture noise, not real signals.
MIN_VIABLE_COVERAGE = 0.10

_WIN_TOKENS = {"win", "won", "profit"}
_LOSS_TOKENS = {"loss", "lost", "lose"}
_DRAW_TOKENS = {"draw", "refund", "void", "break even", "breakeven"}
_WIN_SYMBOLS = ("✅", "✔", "🟢", "➕")
_LOSS_SYMBOLS = ("❌", "❎", "🔴", "➖")
_DRAW_SYMBOLS = ("♻", "🔄")


def _normalize(text):
    return (text or "").strip()


def _classify_result_token(norm_text):
    """Returns 'win'/'loss'/'draw'/None for a short result-only message -
    the common vocabulary observed across the research corpus (Martin
    Trader, TYLER VIP CLUB, Pocket Option Signals, Daniel FX Trade, VIP |
    Signals all use some combination of these symbols/words)."""
    t = norm_text.strip()
    if not t or len(t) > 60:
        return None  # a real signal/commentary line, not a short result token
    lowered = t.lower()
    for sym in _WIN_SYMBOLS:
        if t.startswith(sym):
            return "win"
    for sym in _LOSS_SYMBOLS:
        if t.startswith(sym):
            return "loss"
    for sym in _DRAW_SYMBOLS:
        if t.startswith(sym):
            return "draw"
    if any(tok in lowered for tok in _WIN_TOKENS) and not any(tok in lowered for tok in _LOSS_TOKENS):
        return "win"
    if any(tok in lowered for tok in _LOSS_TOKENS):
        return "loss"
    if any(tok in lowered for tok in _DRAW_TOKENS):
        return "draw"
    if t in ("+", "-", "="):
        return {"+": "win", "-": "loss", "=": "draw"}[t]
    return None


# ---------------------------------------------------------------------
# Pattern templates - each is (name, try_parse_line(line) -> dict|None).
# A returned dict has asset/direction/expiry keys (expiry optional).
# ---------------------------------------------------------------------

_PAIR_CORE = r"[A-Za-z]{3}\s*/?\s*[A-Za-z]{3}"


def _resolve_pair(raw):
    upper = re.sub(r"\s+", "", raw.upper())
    m = (
        _find_valid_pair(r"^([A-Z]{3})/([A-Z]{3})$", raw.upper().replace(" ", ""))
        or _find_valid_pair(r"^([A-Z]{3})([A-Z]{3})$", upper)
    )
    return f"{m.group(1)}/{m.group(2)}" if m else None


_COMPACT_BUYSELL_RE = re.compile(
    rf"^({_PAIR_CORE})\s*(?:\(?OTC\)?)?\s*[—\-]?\s*(\d+)\s*(?:MIN(?:UTE)?S?)\s*(BUY|SELL|UP|DOWN|CALL|PUT)\b",
    re.IGNORECASE,
)
_COMPACT_DIRFIRST_RE = re.compile(
    rf"^({_PAIR_CORE})\s*(BUY|SELL|UP|DOWN|CALL|PUT|HIGH|LOWER)\b.*?(\d+)\s*(?:MIN(?:UTE)?S?)",
    re.IGNORECASE,
)
_LABELED_RE = re.compile(
    r"(?:pair|currency\s*pair|asset)\s*:\s*([A-Za-z/]{6,9})", re.IGNORECASE,
)
_LABELED_DIRECTION_RE = re.compile(r"\b(BUY|SELL|UP|DOWN|CALL|PUT|HIGH|LOWER)\b", re.IGNORECASE)
_LABELED_EXPIRY_RE = re.compile(r"(\d+)\s*(?:MIN(?:UTE)?S?|SEC(?:OND)?S?)", re.IGNORECASE)


def _direction_from_word(word):
    word = word.upper()
    if word in ("BUY", "UP", "CALL", "HIGH"):
        return "BUY"
    if word in ("SELL", "DOWN", "PUT", "LOWER"):
        return "SELL"
    return None


# ---------------------------------------------------------------------
# TYLER VIP CLUB named pattern - ported from the OPT SIGNALS research
# branch's hand-built adapter (research/parser/adapters/tyler_vip_club.py),
# which was grounded in an exhaustive read of that provider's full 2906-
# message dump, not guessed. That provider's real vocabulary ("BUY/SELL
# NOW X (OTC)" signals, "WIN"/"Bad luck" results, "Raise your stake"/"Go
# back to the initial trade size" recovery instructions) doesn't fit any
# of the generic templates above - "Bad luck" in particular isn't in
# _classify_result_token's generic loss vocabulary at all. Unlike the
# generic templates (reusable shapes seen across multiple providers),
# this one is intentionally provider-specific: its coverage on OTHER
# providers' real corpora should stay near zero (this exact phrasing
# doesn't appear elsewhere), so adding it here is safe alongside the
# existing templates, not a source of regression risk for them.
# ---------------------------------------------------------------------

_TYLER_SIGNAL_RE = re.compile(r"\b(BUY|SELL)\s+NOW\b.*?([A-Za-z][A-Za-z0-9 /]*?)\s*\(OTC\)", re.IGNORECASE)
_TYLER_RAISE_STAKE_RE = re.compile(r"Raise your stake", re.IGNORECASE)
_TYLER_RESET_STAKE_RE = re.compile(r"Go back to the initial trade size", re.IGNORECASE)
# Every "Trade time:" line in the full research dump used this exact
# duration (confirmed by exhaustive correlation, not decoded from the
# obscured keycap-emoji glyphs themselves - see the ported adapter's own
# docstring for the full reasoning). Fixed here on that same basis.
_TYLER_FIXED_EXPIRY = "2 Minutes"


def _tyler_is_win(norm_text):
    first_line = norm_text.strip().split("\n", 1)[0].strip().upper()
    return first_line in ("WIN", "✅ WIN")


def _tyler_is_loss(norm_text):
    first_line = norm_text.strip().split("\n", 1)[0].strip().upper()
    return "BAD LUCK" in first_line


def _tyler_parse_signal(text):
    m = _TYLER_SIGNAL_RE.search(text)
    if m is None:
        return None
    direction = _direction_from_word(m.group(1))
    raw_asset = m.group(2).strip()
    pair = _resolve_pair(raw_asset)
    asset = pair or raw_asset  # trust the source verbatim for crypto/other non-forex names
    return {"asset": asset, "direction": direction, "expiry": _TYLER_FIXED_EXPIRY}


def _score_tyler_vip_flow(messages):
    total = sum(1 for m in messages if _normalize(m.get("text")))
    if not total:
        return 0.0
    hits = sum(1 for m in messages if _tyler_parse_signal(_normalize(m.get("text"))))
    return hits / total


def _parse_tyler_vip_flow(messages):
    signal_records = []
    result_links = []
    pending = None  # (message_id, record)

    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue

        if _TYLER_RAISE_STAKE_RE.search(text) or _TYLER_RESET_STAKE_RE.search(text):
            continue  # recovery-instruction lines - not a signal, not a result

        if _tyler_is_win(text) or _tyler_is_loss(text):
            result = "win" if _tyler_is_win(text) else "loss"
            if pending is not None:
                result_links.append({
                    "signal_message_id": pending[0], "result_message_id": m["message_id"], "result": result,
                })
                pending = None
            continue

        parsed = _tyler_parse_signal(text)
        if parsed is None:
            continue  # promo/testimonial/schedule/prep text - not forced into a fake record

        if pending is not None:
            result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "unresolved"})
        record = {
            "source_message_id": m["message_id"], "normalized_asset": parsed["asset"],
            "direction": parsed["direction"], "expiry": parsed["expiry"], "confidence": 0.7,
        }
        signal_records.append(record)
        pending = (m["message_id"], record)

    if pending is not None:
        result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "unresolved"})
    return signal_records, result_links


# ---------------------------------------------------------------------
# OTC Pro Trading Robot named pattern - reuses parsers/signal_parser.py's
# own parse_asset_announcement/carried_asset (built and live-verified
# 2026-07-18 to make this exact provider's real-time trading work:
# 26/60 real messages correctly reconstructed into signals). Deliberately
# NOT a separate reimplementation - the batch onboarding/backtest pattern
# and the live real-time parser must agree on what this provider's
# messages mean, or a provider AXIM can already trade correctly in real
# time could still fail to onboard/backtest, which is exactly the gap
# found live (2026-07-18): the generic two_step_asset_then_direction
# template scores 0 here because the real "Preparing trading asset X"
# announcement is 60+ chars (loses _asset_only's 40-char cap) and the
# real entry message ("💹 Summary: BUY OPTION ... Expiration time: 3
# MINUTES ...") doesn't match _direction_only's labeled-field regex.
#
# Terminal result messages ("Summary: EUR/JPY OTC Profit🟢 Closing
# price:...", "Safe option has been completed! Closing price:...
# Summary: ... Profit🟢") always contain PROFIT/LOSS/DRAW as a literal
# word (confirmed against real full-text messages, not the truncated
# samples first fetched) - same detection the OPT SIGNALS research
# branch's own hand-built adapter for this provider already used.
# ---------------------------------------------------------------------

_OTC_ROBOT_TERMINAL_RESULT_RE = re.compile(r"\b(PROFIT|LOSS|DRAW)\b", re.IGNORECASE)
_OTC_ROBOT_RESULT_MAP = {"PROFIT": "win", "LOSS": "loss", "DRAW": "draw"}
# The real, distinguishing shape of this provider's entry/recovery
# messages ("Summary: BUY OPTION...", "Safe OPTION SELL...") - required
# so this pattern doesn't just re-detect ANY message the shared
# real-time parser happens to recognize (parse_signal is intentionally
# generic across many providers' shapes; without this gate,
# otc_pro_robot_flow scored 0.375 on plain "BUY NOW EUR/USD (OTC)"
# TYLER-style text in testing - a real false-positive caught before
# shipping, not a hypothetical one).
_OTC_ROBOT_OPTION_WORD_RE = re.compile(r"\bOPTION\b", re.IGNORECASE)


def _score_otc_pro_robot_flow(messages):
    total = sum(1 for m in messages if _normalize(m.get("text")))
    if not total:
        return 0.0
    hits = 0
    carried_asset = None
    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue
        announced = parse_asset_announcement(text)
        if announced:
            carried_asset = announced
            continue
        if _OTC_ROBOT_TERMINAL_RESULT_RE.search(text.upper()):
            continue  # a terminal result line, not a signal - doesn't count as a hit or a miss
        if not _OTC_ROBOT_OPTION_WORD_RE.search(text):
            continue
        if parse_signal(text, carried_asset=carried_asset):
            hits += 1
    return hits / total


def _parse_otc_pro_robot_flow(messages):
    signal_records = []
    result_links = []
    pending = None  # (message_id, record)
    carried_asset = None

    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue

        announced = parse_asset_announcement(text)
        if announced:
            carried_asset = announced
            continue

        result_m = _OTC_ROBOT_TERMINAL_RESULT_RE.search(text.upper())
        if result_m:
            outcome = _OTC_ROBOT_RESULT_MAP[result_m.group(1).upper()]
            if pending is not None:
                result_links.append({
                    "signal_message_id": pending[0], "result_message_id": m["message_id"], "result": outcome,
                })
                pending = None
            continue

        if not _OTC_ROBOT_OPTION_WORD_RE.search(text):
            continue  # not this provider's entry/recovery shape - promo/chatter, not forced into a fake record

        signal = parse_signal(text, carried_asset=carried_asset)
        if signal is None:
            continue  # promo/prep chatter - not forced into a fake record

        if pending is not None:
            # A recovery re-entry after an unresolved prior entry in the
            # same chain - the provider's own mechanic only fires a next
            # entry after the previous one lost, matching the OPT SIGNALS
            # research adapter's identical inference for this provider.
            result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "loss"})
        record = {
            "source_message_id": m["message_id"], "normalized_asset": signal["asset"],
            "direction": signal["direction"],
            "expiry": signal["expiry"] if signal["expiry"] != "Unknown" else None,
            "confidence": 0.7,
        }
        signal_records.append(record)
        pending = (m["message_id"], record)

    if pending is not None:
        result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "unresolved"})
    return signal_records, result_links


def _try_compact_buysell(line):
    m = _COMPACT_BUYSELL_RE.match(line.strip())
    if not m:
        return None
    pair = _resolve_pair(m.group(1))
    if not pair:
        return None
    direction = _direction_from_word(m.group(3))
    return {"asset": pair, "direction": direction, "expiry": f"{m.group(2)} Minutes"}


def _try_compact_dirfirst(line):
    m = _COMPACT_DIRFIRST_RE.match(line.strip())
    if not m:
        return None
    pair = _resolve_pair(m.group(1))
    if not pair:
        return None
    direction = _direction_from_word(m.group(2))
    return {"asset": pair, "direction": direction, "expiry": f"{m.group(3)} Minutes"}


def _try_labeled_block(text):
    """Applied to a whole message (may be multi-line), not one line -
    labeled fields are often spread across 2-3 lines of the same message."""
    pair_m = _LABELED_RE.search(text)
    if not pair_m:
        return None
    pair = _resolve_pair(pair_m.group(1))
    if not pair:
        return None
    dir_m = _LABELED_DIRECTION_RE.search(text)
    direction = _direction_from_word(dir_m.group(1)) if dir_m else None
    if direction is None:
        return None
    exp_m = _LABELED_EXPIRY_RE.search(text)
    expiry = f"{exp_m.group(1)} Minutes" if exp_m else None
    return {"asset": pair, "direction": direction, "expiry": expiry}


_SINGLE_MESSAGE_TEMPLATES = [
    ("compact_buysell", _try_compact_buysell),
    ("compact_dirfirst", _try_compact_dirfirst),
    ("labeled_block", _try_labeled_block),
]


def _asset_only(text):
    """A message that's ONLY a bare asset (no direction/expiry) - the
    first half of a two-step "asset, then direction" provider shape
    (NTrade/OTC Pro Robot/Pocket Option Signals all do this)."""
    stripped = text.strip()
    if len(stripped) > 40:
        return None
    pair = _resolve_pair(re.sub(r"\bOTC\b", "", stripped, flags=re.IGNORECASE).strip())
    if not pair:
        return None
    if _LABELED_DIRECTION_RE.search(stripped):
        return None  # has a direction too - not asset-only
    return pair


def _direction_only(text):
    stripped = text.strip()
    if len(stripped) > 60:
        return None
    dir_m = _LABELED_DIRECTION_RE.search(stripped)
    if not dir_m:
        return None
    direction = _direction_from_word(dir_m.group(1))
    exp_m = _LABELED_EXPIRY_RE.search(stripped)
    expiry = f"{exp_m.group(1)} Minutes" if exp_m else None
    return {"direction": direction, "expiry": expiry}


def _score_single_message_template(parse_fn, messages, use_whole_text=False):
    hits = 0
    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue
        target = text if use_whole_text else text.split("\n")[0]
        if parse_fn(target):
            hits += 1
    total = sum(1 for m in messages if _normalize(m.get("text")))
    return (hits / total) if total else 0.0


def _score_two_step_pattern(messages):
    """Asset-only message immediately followed by a direction(+expiry)
    message - scores how many asset-only messages get a usable
    follow-up, out of all non-blank messages."""
    total = sum(1 for m in messages if _normalize(m.get("text")))
    if not total:
        return 0.0
    hits = 0
    pending_asset = None
    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue
        asset = _asset_only(text)
        if asset:
            pending_asset = asset
            continue
        if pending_asset is not None:
            direction_info = _direction_only(text)
            if direction_info and direction_info["direction"]:
                hits += 1
                pending_asset = None
    return hits / total


def detect_pattern(messages):
    """messages: list of {message_id, text, date_utc} in chronological
    order (a real 30-day-or-however-much history batch). Returns
    {"pattern": name, "coverage": 0-1} for the best-scoring pattern, or
    None if nothing clears MIN_VIABLE_COVERAGE - this provider needs a
    hand-built adapter, honestly reported rather than forced."""
    candidates = []
    for name, fn in _SINGLE_MESSAGE_TEMPLATES:
        use_whole = (name == "labeled_block")
        coverage = _score_single_message_template(fn, messages, use_whole_text=use_whole)
        candidates.append((name, coverage))
    candidates.append(("two_step_asset_then_direction", _score_two_step_pattern(messages)))
    candidates.append(("tyler_vip_flow", _score_tyler_vip_flow(messages)))
    candidates.append(("otc_pro_robot_flow", _score_otc_pro_robot_flow(messages)))

    best_name, best_coverage = max(candidates, key=lambda c: c[1])
    if best_coverage < MIN_VIABLE_COVERAGE:
        return None
    return {"pattern": best_name, "coverage": round(best_coverage, 4), "all_scores": dict(candidates)}


def parse_with_pattern(pattern_name, messages):
    """Applies the named pattern across the full message batch and
    returns (signal_records, result_links) in the same shape the
    research branch's hand-built adapters use (source_message_id,
    normalized_asset, direction, expiry / signal_message_id,
    result_message_id, result). Every record's confidence is fixed per
    pattern (compact/labeled single-message patterns are high-
    confidence when they match at all; the two-step pattern is
    medium-confidence, matching the equivalent hand-built adapters'
    own calibration)."""
    if pattern_name == "two_step_asset_then_direction":
        return _parse_two_step(messages)
    if pattern_name == "tyler_vip_flow":
        return _parse_tyler_vip_flow(messages)
    if pattern_name == "otc_pro_robot_flow":
        return _parse_otc_pro_robot_flow(messages)
    fn = dict(_SINGLE_MESSAGE_TEMPLATES)[pattern_name]
    use_whole = (pattern_name == "labeled_block")
    return _parse_single_message(fn, messages, use_whole_text=use_whole, confidence=0.75)


def _parse_single_message(parse_fn, messages, use_whole_text, confidence):
    signal_records = []
    result_links = []
    pending = None  # (message_id, record)

    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue
        result = _classify_result_token(text)
        if result is not None:
            if pending is not None:
                result_links.append({
                    "signal_message_id": pending[0], "result_message_id": m["message_id"], "result": result,
                })
                pending = None
            continue

        target = text if use_whole_text else text.split("\n")[0]
        parsed = parse_fn(target)
        if parsed is None:
            continue

        if pending is not None:
            result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "unresolved"})
        record = {
            "source_message_id": m["message_id"], "normalized_asset": parsed["asset"],
            "direction": parsed["direction"], "expiry": parsed.get("expiry"), "confidence": confidence,
        }
        signal_records.append(record)
        pending = (m["message_id"], record)

    if pending is not None:
        result_links.append({"signal_message_id": pending[0], "result_message_id": None, "result": "unresolved"})
    return signal_records, result_links


def _parse_two_step(messages):
    signal_records = []
    result_links = []
    pending_asset = None  # (message_id, asset)
    pending_signal = None  # (message_id, record)

    for m in messages:
        text = _normalize(m.get("text"))
        if not text:
            continue

        result = _classify_result_token(text)
        if result is not None:
            if pending_signal is not None:
                result_links.append({
                    "signal_message_id": pending_signal[0], "result_message_id": m["message_id"], "result": result,
                })
                pending_signal = None
            continue

        asset = _asset_only(text)
        if asset:
            pending_asset = (m["message_id"], asset)
            continue

        if pending_asset is not None:
            direction_info = _direction_only(text)
            if direction_info and direction_info["direction"]:
                if pending_signal is not None:
                    result_links.append({
                        "signal_message_id": pending_signal[0], "result_message_id": None, "result": "unresolved",
                    })
                record = {
                    "source_message_id": pending_asset[0], "normalized_asset": pending_asset[1],
                    "direction": direction_info["direction"], "expiry": direction_info.get("expiry"),
                    "confidence": 0.65,
                }
                signal_records.append(record)
                pending_signal = (pending_asset[0], record)
                pending_asset = None

    if pending_signal is not None:
        result_links.append({"signal_message_id": pending_signal[0], "result_message_id": None, "result": "unresolved"})
    return signal_records, result_links


def analyze_provider(messages):
    """Top-level entry point: detect + parse in one call. Returns
    {"pattern": ..., "coverage": ..., "signal_records": [...],
    "result_links": [...]} or None if no pattern was viable."""
    detection = detect_pattern(messages)
    if detection is None:
        return None
    signal_records, result_links = parse_with_pattern(detection["pattern"], messages)
    return {**detection, "signal_records": signal_records, "result_links": result_links}

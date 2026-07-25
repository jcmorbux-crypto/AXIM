import re
import unicodedata

# Two adjacent regional-indicator symbols form a flag emoji (e.g. the EU
# flag before "EUR"). Some real providers (confirmed live: OTC Pro Trading
# Robot) put one directly between the "/" and the second currency code
# ("CAD/\U0001F1EF\U0001F1F5 JPY OTC"), which breaks the plain
# ([A-Z]{3})/([A-Z]{3}) pair regex outright - not a cosmetic issue, a
# silent parse failure on a real, currently-shipping message shape.
# Ported from research/parser/layer1_normalize.py (already proven safe
# there against the full OPT SIGNALS provider corpus) rather than
# reimplemented - strips pairs specifically, not the whole Unicode range,
# so a lone regional indicator that's actually part of something else
# isn't eaten. Never removes or alters anything that could change a
# message's MEANING (direction words, numbers, asset codes untouched).
_FLAG_PAIR_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
_KEYCAP_RE = re.compile(r"([0-9])️?⃣")
_TEN_KEYCAP_RE = re.compile(r"\U0001F51F")
_NBSP_RE = re.compile(r"[  ]")


# A daily/session results summary (confirmed live: Martin Trader's real
# "SESSION REPORT" message - "Accuracy: 93.00% / Wins: 14 / Losses: 1"
# followed by a list of that day's already-closed trades) is never a new
# tradeable signal - but because it lists past trades, it genuinely
# contains a real asset+direction pair, so the ordinary asset/direction
# search below would otherwise extract the FIRST one and misfire a
# phantom trade off historical data, not a live entry. Rejected before
# any extraction runs, on the presence of BOTH an accuracy/win-rate
# figure and a win/loss tally - the combination genuinely distinguishes
# "this is a scoreboard" from a real single-trade signal, which never
# reports its own historical accuracy.
_SUMMARY_REPORT_RE = re.compile(
    r"\b(ACCURACY|WIN\s*RATE)\b.{0,30}[:\-]?\s*\d{1,3}(\.\d+)?\s*%", re.IGNORECASE | re.DOTALL,
)
_WIN_LOSS_TALLY_RE = re.compile(r"\bWINS?\s*[:\-]\s*\d+\b.{0,60}\bLOSS(ES)?\s*[:\-]\s*\d+\b", re.IGNORECASE | re.DOTALL)

# See the direction-extraction comment in parse_signal() below for why this
# exists. 16 sits in the real gap between the longest legitimate
# direction-bearing line observed across all 16 OPT SIGNALS providers (14
# words - OTC Pro Trading Robot's heavily emoji-decorated "Summary: BUY
# OPTION ... Expiration time: 3 MINUTES ... Opening price: X" entry
# message) and the shortest confirmed real narrative-prose false positive
# (18 words - see the shared parser false-positive comment below). A
# tighter threshold (10) was tried first and broke that real OTC Pro
# Trading Robot entry shape - caught by tests/test_signal_parser.py's
# CarriedAssetTests/FlagEmojiNormalizationTests before it ever reached
# production.
_MAX_DIRECTION_LINE_WORDS = 16


def _normalize(text):
    """Provider-agnostic decoration cleanup applied before any parsing -
    see _FLAG_PAIR_RE comment. Safe to apply universally: every
    substitution here removes visual noise that carries no signal
    information beyond what the surrounding text already states."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _NBSP_RE.sub(" ", t)
    t = _KEYCAP_RE.sub(r"\1", t)
    t = _TEN_KEYCAP_RE.sub("10", t)
    t = _FLAG_PAIR_RE.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


# ISO 4217 3-letter currency codes, plus CNH (Offshore Chinese Yuan - not
# a formal ISO 4217 code, but a real, distinct, commonly OTC-traded FX
# designation confirmed live: Pocket 5M Trader's real "USDCNH-OTC"
# signals were silently rejected before this - CNY alone wasn't enough,
# CNH is a genuinely different currency). Used to validate both halves of
# a concatenated asset pair (e.g. "NZDJPY") before accepting it as an
# asset - without this, a plain 6-letter English word standalone in
# message boilerplate (e.g. "SIGNAL" -> "SIG"+"NAL") would false-
# positive-match as a fake asset and reach the browser as a garbage
# search term. Real bug hit live in production: a Go+ message reading
# "...Signal: BUY" parsed as asset "SIG/NAL" and the platform correctly
# rejected it as not found.
_CURRENCY_CODES = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNH", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
    "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
    "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
    "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL",
    "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR",
    "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
    "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR",
    "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
    "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL", "THB",
    "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
    "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF",
    "XPF", "YER", "ZAR", "ZMW", "ZWL",
})


def _find_valid_pair(pattern, text):
    """Like re.search but only accepts a match where both captured 3-letter
    codes are real currency codes - rejects coincidental matches on English
    words that happen to be 6 letters (see _CURRENCY_CODES comment)."""
    for m in re.finditer(pattern, text):
        if m.group(1) in _CURRENCY_CODES and m.group(2) in _CURRENCY_CODES:
            return m
    return None


# Sources like Go+ label the asset by category ("Currency pair: EUR/NZD OTC",
# "Cryptocurrency: Toncoin OTC", "Commoditi: WTI Crude Oil OTC" - yes, that's
# really how Go+ spells it, "Stock: GameStop Corp OTC", "Index: US100 OTC").
# Each category is matched by word-stem ("Commodit\w*", "Crypto\w*", etc.)
# rather than one exact spelling - sources are inconsistent/typo-prone about
# these labels (confirmed live: "Commoditi:" is real, not "Commodity:") and
# the asset name itself is what actually matters for execution, not getting
# the category word exactly right. Matched against the ORIGINAL (non-
# uppercased) message, since Pocket Option's real asset names outside forex
# are mixed-case ("GameStop Corp OTC", "ExxonMobil OTC", "McDonald's OTC") -
# uppercasing and reconstructing with .title() (the old approach) silently
# mismatches the platform's exact display text (e.g. "Gamestop Corp OTC" !=
# "GameStop Corp OTC"), which is exact-text-matched by execution/pocket_dom.py.
#
# Whitespace around the colon is deliberately restricted to spaces/tabs,
# not \s (which also matches newlines) - every real provider's labeled
# field puts "Label: value" on one line. Real bug found live (2026-07-25):
# with \s* on both sides, a label word ending its own line right before a
# colon (e.g. a chart-education post's "...what the pattern indicates:\n")
# let the regex's whitespace consume that trailing newline and read the
# UNRELATED following line's text as the asset value - confirmed live on
# TYLER VIP CLUB message [22513], a phantom signal from "indicates:"
# leaking into "Sellers' interest is waning..." on the next line.
_LABELED_ASSET_RE = re.compile(
    r"\b(Curr\w*\s*pair|Curr\w*|Pair|Crypto\w*|Commodit\w*|Stock\w*|Index|Indic\w*)[ \t]*:[ \t]*([^\r\n]+)",
    re.IGNORECASE,
)
_FOREX_LABEL_RE = re.compile(r"^(curr|pair)", re.IGNORECASE)


def _normalize_labeled_forex(raw_value):
    """"Currency pair: EUR/NZD OTC" or "Currency pair: NZDJPY OTC" -> the
    same normalized slash-pair asset the unlabeled path produces."""
    upper = raw_value.upper().strip()
    otc = bool(re.search(r"\bOTC\b", upper))
    pair_match = (
        _find_valid_pair(r"\b([A-Z]{3})\s*/\s*([A-Z]{3})\b", upper)
        or _find_valid_pair(r"\b([A-Z]{3})([A-Z]{3})\b", upper)
    )
    if not pair_match:
        return None
    asset = f"{pair_match.group(1)}/{pair_match.group(2)}"
    return f"{asset} OTC" if otc else asset


def parse_signal(message, carried_asset=None):
    """carried_asset: a fallback asset (already in "EUR/USD" or "EUR/USD OTC"
    form) to use ONLY if this specific message contains no asset of its
    own. Some real providers (confirmed live: OTC Pro Trading Robot) split
    a single trade across two messages - a "Preparing trading asset X"
    announcement, then a separate entry message with direction+expiry but
    no asset at all - see parse_asset_announcement(). Never overrides an
    asset actually found in the message; every other source's behavior is
    completely unchanged when this is omitted."""
    if not message:
        return None

    message = _normalize(message)

    if _SUMMARY_REPORT_RE.search(message) and _WIN_LOSS_TALLY_RE.search(message):
        return None

    signal = {}

    # Labeled formats take priority - checked against the original message
    # to preserve exact platform casing for non-forex categories.
    labeled_match = _LABELED_ASSET_RE.search(message)
    if labeled_match:
        label = labeled_match.group(1).strip().lower()
        raw_value = labeled_match.group(2).strip()

        if _FOREX_LABEL_RE.match(label):
            asset = _normalize_labeled_forex(raw_value)
        else:
            # Cryptocurrency / Commodity / Stock / Index: trust the source's
            # own casing, it already matches Pocket Option's display name.
            asset = raw_value or None

        if asset:
            signal["asset"] = asset

    text = message.upper().replace('"', "").replace("'", "").strip()

    if "asset" not in signal:
        # Asset formats (no explicit label):
        # USD/IDR OTC
        # CAD/CHF OTC
        # Stock: Intel OTC
        # NZDJPY OTC (concatenated pair, no slash - normalized to NZD/JPY OTC)
        # USDCNH-OTC (hyphen, no space, before "OTC" - confirmed live:
        # Pocket 5M Trader always formats it this way; [\s\-]* accepts
        # either separator instead of only whitespace, so the OTC suffix
        # isn't silently dropped from the asset name).
        stock_match = re.search(r"\bSTOCK:\s*([A-Z0-9 ]+?\s+OTC)\b", text)
        slash_match = _find_valid_pair(r"\b([A-Z]{3})\s*/\s*([A-Z]{3})\b([\s\-]*OTC)?", text)
        concat_match = _find_valid_pair(r"\b([A-Z]{3})([A-Z]{3})\b([\s\-]*OTC)?", text)

        if stock_match:
            signal["asset"] = stock_match.group(1).title().replace("Otc", "OTC")
        elif slash_match or concat_match:
            pair_match = slash_match or concat_match
            asset = f"{pair_match.group(1)}/{pair_match.group(2)}"

            if pair_match.group(3):
                asset = f"{asset} OTC"

            signal["asset"] = asset
        elif carried_asset:
            signal["asset"] = carried_asset
        else:
            return None

    # Direction formats:
    # UP, DOWN, CALL, PUT, HIGH, LOWER take priority over a trailing BUY/
    # SELL word - some sources send both (e.g. "NZDJPY OTC DOWN BUY"),
    # where the UP/DOWN/CALL/PUT/HIGH/LOWER keyword is the actual call and
    # BUY/SELL is incidental wording. HIGH/LOWER confirmed against a real
    # provider's live format (Daniel FX Trade: "GBP/CAD HIGH ⬆ 15 MIN" /
    # "GBP/CHF LOWER ⬇ 15 MIN") - not a guess.
    #
    # Scoped to short lines only (<= _MAX_DIRECTION_LINE_WORDS words), not
    # the whole message: a labeled asset field can leave several sentences
    # of free-text market commentary in the remainder, and an unscoped
    # search would misread an incidental word like "sell" inside a
    # sentence such as "look for sell opportunities in line with the
    # trend" as a real trade instruction. Real bug found live (2026-07-25,
    # replay validation): Trading Booster Elite Membership message
    # [51781] parsed as a phantom AUD/CAD SELL signal purely from that
    # kind of narrative sentence, not an actual entry instruction. Every
    # real verified provider's direction indicator - a bare word, a short
    # structured line, an emoji-prefixed line, or a "Direction: X" labeled
    # field - fits comfortably within a short line; multi-sentence
    # commentary never legitimately carries the trade instruction itself.
    direction_text = "\n".join(
        line for line in text.split("\n")
        if 0 < len(line.split()) <= _MAX_DIRECTION_LINE_WORDS
    )
    directional_match = re.search(r"\b(UP|DOWN|CALL|PUT|HIGH|LOWER)\b", direction_text)

    if directional_match:
        signal["direction"] = "BUY" if directional_match.group(1) in ("UP", "CALL", "HIGH") else "SELL"
    elif re.search(r"\bBUY\b", direction_text):
        signal["direction"] = "BUY"
    elif re.search(r"\bSELL\b", direction_text):
        signal["direction"] = "SELL"
    else:
        return None

    # Expiry formats:
    # S5, S10, S15, S20, S25, S30, S45, S50, S55 = seconds
    # M1, M2, M3, M4, M5, M6, M7, M8, M9, M10 = minutes
    # Also supports: 30 Seconds, 1 Minute, 5 MIN
    second_match = re.search(r"\bS\s?(\d{1,2})\b", text)
    minute_match = re.search(r"\bM\s?(\d{1,2})\b", text)

    if second_match:
        signal["expiry"] = f"{second_match.group(1)} Seconds"
    elif minute_match:
        signal["expiry"] = f"{minute_match.group(1)} Minute"
    else:
        expiry_match = re.search(
            r"\b(\d{1,2})\s*(SECOND|SECONDS|SEC|SECS|MINUTE|MINUTES|MIN|MINS)\b",
            text
        )

        if expiry_match:
            number = expiry_match.group(1)
            unit = expiry_match.group(2)

            if unit.startswith(("SECOND", "SEC")):
                signal["expiry"] = f"{number} Seconds"
            else:
                signal["expiry"] = f"{number} Minute"
        else:
            signal["expiry"] = "Unknown"

    # Entry timing (2026-07-25 Execution Reliability directive, gap queue
    # item 2) - captures an explicitly labeled entry time (confirmed
    # real: Martin Trader's "Entry: 09:00" field) as a new, optional
    # field. Parsing only - deliberately does NOT change any execution
    # behavior: whether AXIM should wait for this time, reject a signal
    # whose entry time has already passed, or something else is a
    # product decision, not a parsing question (see
    # docs/opt_signals_gap_queue.md item 2). Every existing caller
    # ignores this key entirely, so nothing downstream changes until
    # that decision is made and a real consumer is built.
    entry_time_match = re.search(r"\bEntry\s*:\s*(\d{1,2}:\d{2})\b", message, re.IGNORECASE)
    if entry_time_match:
        signal["entry_time"] = entry_time_match.group(1)

    signal["raw_message"] = message

    return signal


_ASSET_ANNOUNCEMENT_RE = re.compile(r"\bPreparing trading asset\b", re.IGNORECASE)


def parse_asset_announcement(message):
    """Some real providers (confirmed live: OTC Pro Trading Robot) send a
    standalone "Preparing trading asset EUR/JPY OTC ..." message before
    the actual entry, and the entry message itself ("Summary: BUY OPTION
    ... Expiration time: 3 MINUTES ...") never repeats the asset at all -
    two messages, one trade. This is NOT itself a tradeable signal (no
    direction/expiry) - it only tells the caller which asset to carry
    forward into the next call to parse_signal(..., carried_asset=...)
    for this channel. Returns the normalized asset string, or None if
    this message isn't one of these announcements or has no recognizable
    asset in it."""
    if not message:
        return None
    text = _normalize(message)
    if not _ASSET_ANNOUNCEMENT_RE.search(text):
        return None
    upper = text.upper()
    pair = (
        _find_valid_pair(r"\b([A-Z]{3})\s*/\s*([A-Z]{3})\b", upper)
        or _find_valid_pair(r"\b([A-Z]{3})([A-Z]{3})\b", upper)
    )
    if not pair:
        return None
    asset = f"{pair.group(1)}/{pair.group(2)}"
    if re.search(r"\bOTC\b", upper):
        asset += " OTC"
    return asset


def apply_expiry_fallback(signal, default_expiry):
    """If parse_signal() found no expiry in the message itself (falls back to
    the literal "Unknown" rather than guessing), and the channel has an
    explicit configured default (core/database.py's ui_channels.default_expiry
    - set only when a human has confirmed that provider's real convention,
    e.g. "this provider's signals are always 5-minute trades"), use it.

    Never overrides a real parsed expiry, never invents one when no default
    is configured for that channel - those cases keep failing closed exactly
    as before. This is the one, narrow, explicitly-configured exception,
    not a general-purpose guesser."""
    if not signal or signal.get("expiry") != "Unknown" or not default_expiry:
        return signal
    signal = dict(signal)
    signal["expiry"] = default_expiry
    return signal


def apply_signal_rules(message, rules):
    """Applies channel-specific find/replace rules (core/database.py's
    signal_rules table) to a raw message BEFORE parse_signal() sees it -
    e.g. a channel that spells its label unusually can be normalized to
    whatever parse_signal() already recognizes, without a second parser
    implementation. `rules` is an iterable of dicts with find_pattern/
    replace_with keys (already filtered to enabled=1 by the caller).
    A rule with an invalid regex is skipped rather than raising, so one
    bad saved rule can't take down every message from that channel."""
    for rule in rules:
        try:
            message = re.sub(rule["find_pattern"], rule["replace_with"], message)
        except re.error:
            continue
    return message
import re

# ISO 4217 3-letter currency codes. Used to validate both halves of a
# concatenated asset pair (e.g. "NZDJPY") before accepting it as an asset -
# without this, a plain 6-letter English word standalone in message
# boilerplate (e.g. "SIGNAL" -> "SIG"+"NAL") would false-positive-match as
# a fake asset and reach the browser as a garbage search term. Real bug hit
# live in production: a Go+ message reading "...Signal: BUY" parsed as
# asset "SIG/NAL" and the platform correctly rejected it as not found.
_CURRENCY_CODES = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
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
_LABELED_ASSET_RE = re.compile(
    r"\b(Curr\w*\s*pair|Curr\w*|Pair|Crypto\w*|Commodit\w*|Stock\w*|Index|Indic\w*)\s*:\s*([^\r\n]+)",
    re.IGNORECASE,
)
_FOREX_LABEL_RE = re.compile(r"^(curr|pair)", re.IGNORECASE)


def _normalize_labeled_forex(raw_value):
    """"Currency pair: EUR/NZD OTC" or "Currency pair: NZDJPY OTC" -> the
    same normalized slash-pair asset the unlabeled path produces."""
    upper = raw_value.upper().strip()
    otc = bool(re.search(r"\bOTC\b", upper))
    pair_match = (
        _find_valid_pair(r"\b([A-Z]{3})/([A-Z]{3})\b", upper)
        or _find_valid_pair(r"\b([A-Z]{3})([A-Z]{3})\b", upper)
    )
    if not pair_match:
        return None
    asset = f"{pair_match.group(1)}/{pair_match.group(2)}"
    return f"{asset} OTC" if otc else asset


def parse_signal(message):
    if not message:
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
        stock_match = re.search(r"\bSTOCK:\s*([A-Z0-9 ]+?\s+OTC)\b", text)
        slash_match = _find_valid_pair(r"\b([A-Z]{3})/([A-Z]{3})\b(\s*OTC)?", text)
        concat_match = _find_valid_pair(r"\b([A-Z]{3})([A-Z]{3})\b(\s*OTC)?", text)

        if stock_match:
            signal["asset"] = stock_match.group(1).title().replace("Otc", "OTC")
        elif slash_match or concat_match:
            pair_match = slash_match or concat_match
            asset = f"{pair_match.group(1)}/{pair_match.group(2)}"

            if pair_match.group(3):
                asset = f"{asset} OTC"

            signal["asset"] = asset
        else:
            return None

    # Direction formats:
    # UP, DOWN, CALL, PUT take priority over a trailing BUY/SELL word - some
    # sources send both (e.g. "NZDJPY OTC DOWN BUY"), where the UP/DOWN/
    # CALL/PUT keyword is the actual call and BUY/SELL is incidental wording.
    directional_match = re.search(r"\b(UP|DOWN|CALL|PUT)\b", text)

    if directional_match:
        signal["direction"] = "BUY" if directional_match.group(1) in ("UP", "CALL") else "SELL"
    elif re.search(r"\bBUY\b", text):
        signal["direction"] = "BUY"
    elif re.search(r"\bSELL\b", text):
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

    signal["raw_message"] = message

    return signal
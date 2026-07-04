import re


def parse_signal(message):
    if not message:
        return None

    text = message.upper().replace('"', "").replace("'", "").strip()

    signal = {}

    # Asset formats:
    # USD/IDR OTC
    # CAD/CHF OTC
    # Stock: Intel OTC
    stock_match = re.search(r"\bSTOCK:\s*([A-Z0-9 ]+?\s+OTC)\b", text)
    forex_match = re.search(r"\b([A-Z]{3}/[A-Z]{3})\s*(OTC)?\b", text)

    if stock_match:
        signal["asset"] = stock_match.group(1).title().replace("Otc", "OTC")
    elif forex_match:
        asset = forex_match.group(1)

        if forex_match.group(2):
            asset = f"{asset} OTC"

        signal["asset"] = asset
    else:
        return None

    # Direction formats:
    # BUY, SELL, UP, DOWN, CALL, PUT
    if re.search(r"\b(BUY|CALL|UP)\b", text):
        signal["direction"] = "BUY"
    elif re.search(r"\b(SELL|PUT|DOWN)\b", text):
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
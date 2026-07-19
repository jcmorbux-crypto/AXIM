"""Real-provider-format regression corpus for parsers/signal_parser.py
(AXIM's actual production, trade-executing parser - deliberately NOT the
same thing as core/provider_language_learner.py's analysis-only pattern
library).

Sourced from the sanitized fixture corpus already curated in the sibling
OPT SIGNALS research repo (C:/AXIM-telegram-research/research/tests/
fixtures.py), which documents its own sanitization: "Deliberately NOT
verbatim copies of real provider messages - these are synthetic examples
that match the STRUCTURE/format each adapter was built against ... using
different currency pairs, times, and prices than any real message, and
no real channel names or usernames." This file re-uses that same
discipline for a curated subset actually relevant to the LIVE parser
(single-line/compact formats it's designed to handle), not the full
research corpus (most of which targets the research repo's own
hand-built multi-message adapters, a separate analysis-only system).

Every entry is (label, message_text, expected) where expected is either
a dict with the exact fields parse_signal() must return, or None if the
correct, honest behavior is to reject the message (missing a required
field, chatter/promotional noise, or a fragment of a multi-message
sequence that needs a carried_asset this file doesn't provide alone).
"""

PRODUCTION_PARSER_CORPUS = [
    # ---- Micha Trader-style compact unlabeled signal ----
    ("micha_signal_with_otc", "EUR/HUF OTC BUY 1 MIN",
     {"asset": "EUR/HUF OTC", "direction": "BUY", "expiry": "1 Minute"}),
    ("micha_signal_without_otc", "EUR/AUD SELL 1 MIN",
     {"asset": "EUR/AUD", "direction": "SELL", "expiry": "1 Minute"}),

    # ---- Daniel FX Trade-style single-line signal, HIGH/LOWER vocabulary
    # (real gap found and fixed 2026-07-19 - the parser only recognized
    # UP/DOWN/CALL/PUT/BUY/SELL before this) ----
    ("daniel_signal_high", "GBP/CAD HIGH ⬆ 15 MIN",
     {"asset": "GBP/CAD", "direction": "BUY", "expiry": "15 Minute"}),
    ("daniel_signal_lower", "GBP/CHF LOWER ⬇ 15 MIN",
     {"asset": "GBP/CHF", "direction": "SELL", "expiry": "15 Minute"}),

    # ---- Tyler VIP Club-style emoji-coded direction, noisy channel ----
    ("tyler_signal_buy_forex", "\U0001F53C BUY NOW \U0001F7E2 EUR/GBP (OTC)",
     {"asset": "EUR/GBP", "direction": "BUY", "expiry": "Unknown"}),
    ("tyler_signal_sell_forex", "\U0001F53D SELL NOW \U0001F534 GBP/CHF (OTC)",
     {"asset": "GBP/CHF", "direction": "SELL", "expiry": "Unknown"}),
    ("tyler_promo_noise", "He Used To Be Afraid To Lose $100!\nNow He Knows How To Make $1,000 In One Night!",
     None),

    # ---- SIGNALS # 2 Not Martingale-style compact, format variety ----
    ("s2nm_signal_sell", "GBP/CAD 15 min SELL",
     {"asset": "GBP/CAD", "direction": "SELL", "expiry": "15 Minute"}),
    ("s2nm_signal_no_slash", "CADJPY 18 min BUY",
     {"asset": "CAD/JPY", "direction": "BUY", "expiry": "18 Minute"}),
    ("s2nm_signal_double_space", "GBP/JPY  10 min SELL",
     {"asset": "GBP/JPY", "direction": "SELL", "expiry": "10 Minute"}),

    # ---- NTrade-style two-message chain fragments - a direction-only
    # fragment with no asset and no carried_asset context must be
    # rejected, never guessed at which pair it belongs to ----
    ("ntrade_put_fragment_alone", "⬇️ PUT (SELL) for 1 minutes", None),

    # ---- Layer-2 generic-grammar corpus ----
    ("l2_valid_simple", "EUR/USD OTC\nCALL\nM5",
     {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "5 Minute"}),
    ("l2_valid_labeled", "Currency pair: EUR/NZD OTC\nBUY\n5 minutes",
     {"asset": "EUR/NZD OTC", "direction": "BUY", "expiry": "5 Minute"}),
    ("l2_ambiguous_expiry_still_parses_asset_and_direction", "GBP/USD OTC\nSELL",
     {"asset": "GBP/USD OTC", "direction": "SELL", "expiry": "Unknown"}),
    ("l2_chatter_is_not_a_signal", "Good morning everyone! Let's have a great trading day", None),
    ("l2_result_only_is_not_a_signal", "WIN great trade everyone!", None),

    # ---- Martin Trader-style labeled block - missing a required field
    # must be flagged, never guessed (this fixture's own upstream comment
    # says exactly that) ----
    ("mt_signal_missing_direction",
     "SIGNAL\n\nAUD/JPY OTC\nTimeframe: M5\nExpiration: 5 minutes\nEntry: 09:00",
     None),
]

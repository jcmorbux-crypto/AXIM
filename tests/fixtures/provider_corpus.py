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

    # ---- Daniel FX Trade-style multi-paragraph market analysis - real
    # false positive found and fixed 2026-07-25 (same shared-parser fix as
    # the narrative-announcement case below): "Volatility is high, but
    # when working with indicator strategies..." describes market
    # conditions, not a CALL/PUT/HIGH/LOWER trade instruction, but used to
    # match the old unscoped HIGH/LOWER search. Confirmed live: 2 of this
    # provider's real 80 "signals" were actually this kind of analysis
    # commentary before the fix. ----
    ("daniel_analysis_commentary_mentioning_high_is_not_a_signal",
     "CHF/JPY. There are small jumps in candles, but this does not "
     "interfere with working with the currency pair.\n\nEUR/AUD. I "
     "recommend considering this currency pair for work. Volatility is "
     "high, but when working with indicator strategies, you will be able "
     "to determine the right moment to open a signal.",
     None),

    # ---- Tyler VIP Club-style emoji-coded direction, noisy channel ----
    ("tyler_signal_buy_forex", "\U0001F53C BUY NOW \U0001F7E2 EUR/GBP (OTC)",
     {"asset": "EUR/GBP", "direction": "BUY", "expiry": "Unknown"}),
    ("tyler_signal_sell_forex", "\U0001F53D SELL NOW \U0001F534 GBP/CHF (OTC)",
     {"asset": "GBP/CHF", "direction": "SELL", "expiry": "Unknown"}),
    ("tyler_promo_noise", "He Used To Be Afraid To Lose $100!\nNow He Knows How To Make $1,000 In One Night!",
     None),

    # ---- Tyler VIP Club-style educational strategy post - real false
    # positive found and fixed 2026-07-25 (same shared-parser fix): a
    # teaching post literally instructs "open a BUY position" / "open a
    # SELL position" as part of explaining an indicator strategy, not as
    # a live trade call. Confirmed live: 2 of this provider's real 228
    # "signals" were actually this kind of educational post before the
    # fix. ----
    ("tyler_educational_strategy_post_is_not_a_signal",
     "Strategy For Beginners - SMA 10 + Awesome Oscillator\n\nWe use just "
     "two indicators: SMA 10 and the Awesome Oscillator with default "
     "settings.\n\nA buy signal appears when the AO bars change color "
     "from red to green and the price crosses the SMA from below - open "
     "a BUY position.\n\nA sell signal appears when the AO bars change "
     "color from green to red and the price crosses the SMA from top to "
     "bottom - open a SELL position.",
     None),

    # ---- Tyler VIP Club-style chart-pattern education post - real false
    # positive found and fixed 2026-07-25: describes a chart pattern using
    # "The lower line slopes upward" (chart terminology, not a trade
    # direction) combined with a pre-existing _LABELED_ASSET_RE bug where
    # a label word ("indicates") ending its own line right before a colon
    # let the regex's whitespace match cross the newline and read the
    # NEXT, unrelated line as the asset value. Confirmed live: 1 of this
    # provider's real 228 "signals" was this kind of post before the fix.
    ("tyler_chart_pattern_education_with_label_leak_is_not_a_signal",
     "Ascending Triangle - A Pattern Indicating Uptrend\n\nWhat it looks "
     "like:\nThe upper line is horizontal - highs are aligned at the same "
     "level\nThe lower line slopes upward - each new low is higher than "
     "the previous one\n\nWhat the pattern indicates:\nSellers interest is "
     "waning, lows are being pushed higher",
     None),

    # ---- TYLER PRO CLUB-style (sister channel to TYLER VIP CLUB)
    # strategy-tutorial post - real false positive found and fixed
    # 2026-07-25: "Indicators:" ending its own line right before a colon
    # let the pre-existing _LABELED_ASSET_RE newline-crossing bug read the
    # next, unrelated line as the asset; separately "CALL Signal:"/"PUT
    # Signal:" sections teach a MACD+Stochastic strategy ("Open an UP
    # trade after the candle confirmation"), not a live entry. Confirmed
    # live: 1 of this provider's real 158 "signals" was this kind of post
    # before the fix. ----
    ("tyler_pro_club_strategy_tutorial_is_not_a_signal",
     "MACD + Stochastic strategy\n\nSettings:\nTimeframe: M1\nExpiration "
     "time: 5 minutes\n\nIndicators:\nMACD (12, 26, 9)\nStochastic (5, 3, "
     "3)\n\nCALL Signal:\nStochastic leaves the oversold zone (below 20)\n"
     "Open an UP trade after the candle confirmation\n\nPUT Signal:\n"
     "Stochastic leaves the overbought zone (above 80)\nOpen a DOWN trade "
     "after the candle confirmation",
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

    # ---- Martin Trader-style full labeled signal - a real, complete
    # entry, correctly parsed. entry_time is captured (2026-07-25,
    # Execution Reliability directive); scheduled_entries (the
    # "Martingale:" re-entry block) is now consumed by
    # core/trade_series_engine.py - see docs/opt_signals_gap_queue.md
    # item 2 for the history of why this sat unbuilt. ----
    ("mt_signal_complete",
     "SIGNAL\n\nAUD/JPY OTC\nTimeframe: M5\nExpiration: 5 minutes\nEntry: 09:00\nDirection: SELL\n\nMartingale:\n1 09:05\n2 09:10\n3 09:15",
     {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "entry_time": "09:00",
      "scheduled_entries": [
          {"entry_number": 2, "time": "09:05"},
          {"entry_number": 3, "time": "09:10"},
          {"entry_number": 4, "time": "09:15"},
      ]}),

    # ---- Martin Trader-style daily session report - real gap found and
    # fixed 2026-07-24 (historical replay validation): a summary listing
    # that day's already-closed trades genuinely contains a real
    # asset+direction pair, so the ordinary search below used to extract
    # the FIRST one and misfire a phantom trade off historical data, not
    # a live entry. Confirmed live: 49 of 857 "signals" produced by this
    # provider's real message history were actually session reports, not
    # live signals, before this fix. Must always be rejected regardless
    # of which pair happens to appear first in the trade list. ----
    ("mt_session_report_must_not_become_a_signal",
     "SESSION REPORT\n\nSunday, 12 July 2026\n\nResults:\nAccuracy: 93.00%\nWins: 14\nLosses: 1\n\nTrades:\nNight Session\n00:05 - AUD/JPY - Buy\n00:20 - EUR/USD - Sell",
     None),

    # ---- Pocket 5M Trader-style signal - hyphen-attached OTC suffix
    # with no space ("USDCNH-OTC"), and CNH (Offshore Chinese Yuan) as a
    # currency code. Real gap found and fixed 2026-07-24 (historical
    # replay validation): CNH was missing from the known-currency-code
    # allowlist entirely (rejecting the whole pair), and separately the
    # OTC-suffix regex only accepted whitespace before "OTC", silently
    # dropping the suffix for this provider's hyphenated format ----
    ("pocket5m_hyphenated_otc_with_cnh",
     "USDCNH-OTC\nEntry 09:05\n5 Minutes\nCall UP",
     {"asset": "USD/CNH OTC", "direction": "BUY", "expiry": "5 Minute"}),

    # ---- Trading Booster Elite Membership-style session-opening narrative
    # - real gap found and fixed 2026-07-25 (historical replay validation):
    # a labeled "Pair:"/"Expiration:" announcement followed by multi-
    # sentence free-text market commentary genuinely contains the English
    # word "sell" inside a descriptive sentence ("look for sell
    # opportunities"), not a trade instruction. The unscoped direction
    # search used to read that as a real SELL signal (confirmed live:
    # message [51781], a phantom AUD/CAD SELL). Must always be rejected -
    # this message is context-setting, not a per-trade entry (the actual
    # entries are separate, later, short bare-direction messages). ----
    ("narrative_announcement_with_incidental_direction_word_is_not_a_signal",
     "Hello everyone, welcome to the day trading session\n\nPair: AUDCAD\n"
     "Expiration: 2 minute\nCandles: 30 seconds\n\nThe pair is in a clear "
     "downtrend\n\nOur priority is to look for sell opportunities in line "
     "with the trend. Stay tuned for the signals",
     None),

    # ---- PayDay Signals PO-style bare stock ticker - real gap found
    # 2026-07-25 (onboarding/historical replay). Format: an emoji-labeled
    # asset line with no recognized label word ("Curr"/"Pair"/"Stock"/
    # etc.) and a 2-5 letter bare ticker (not a 6-letter concatenated
    # forex pair, so concat_match doesn't catch it either) - confirmed
    # live: GME-OTC/TSLA-OTC/AAPL-OTC/MARA-OTC/PFE-OTC/COIN-OTC signals
    # all silently returned None (~35% of this provider's real signal
    # volume). This corpus entry locks in the RAW shared-parser gap
    # itself (no signal_rule applied) so nobody "fixes" it by accident
    # without deliberately choosing to change the shared parser. The
    # actual fix is a channel-scoped signal_rule (core/database.py
    # signal_rules, channel_id=382) that rewrites the emoji-labeled line
    # to "Stock: <TICKER> OTC" before parse_signal() sees it - see
    # ApplySignalRulesTests.test_payday_bare_stock_ticker_rule_fixes_the_gap
    # in tests/test_signal_parser.py for the end-to-end fix verification
    # (kept separate from this file since a signal_rule is DB state, not
    # something parse_signal() alone can be asked to apply). ----
    ("payday_bot_bare_stock_ticker_without_rule_is_not_parsed",
     "⚡️PayDay Bot \n\n\U0001F4B5 XOM-OTC\n\U0001F525 M1\n⏳ 09:15:00\n\U0001F53C BUY",
     None),
]

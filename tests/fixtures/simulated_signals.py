SIMULATED_SIGNALS = [
    {
        "asset": "EUR/USD OTC",
        "direction": "BUY",
        "expiry": "3 Seconds",
        "raw_message": "EUR/USD OTC S3 BUY (simulated, matches an expiry preset)",
    },
    {
        "asset": "GBP/USD OTC",
        "direction": "SELL",
        "expiry": "1 Minute",
        "raw_message": "GBP/USD OTC M1 SELL (simulated, matches an expiry preset)",
    },
    {
        "asset": "EUR/GBP OTC",
        "direction": "BUY",
        "expiry": "7 Minute",
        "raw_message": "EUR/GBP OTC M7 BUY (simulated, NO matching UI preset - exercises the HH:MM:SS input path)",
    },
    {
        "asset": "GBP/JPY",
        "direction": "SELL",
        "expiry": "5 Minute",
        "raw_message": "GBP/JPY M5 SELL (simulated, NON-OTC - confirmed both 'GBP/JPY' and 'GBP/JPY OTC' "
                        "appear in search results, exercises explicit OTC/non-OTC disambiguation)",
    },
]

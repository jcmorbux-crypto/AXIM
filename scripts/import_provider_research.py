"""Bridges OPT SIGNALS Provider Intelligence research findings
(C:/AXIM-telegram-research, a separate git worktree - see
docs/AXIM_ENGINEERING_JOURNAL.md) into this repo's real backtest signal
pool (core/database.py's imported_signals table), then automatically
runs every official Money Studio strategy against each eligible
provider via the existing core/backtest_engine.py.

Deliberately a standalone script, not a core/ module or a live API
route: it depends on a sibling directory (the research worktree) that
won't exist on every machine this repo is cloned onto, and its own
adapters/config aren't part of this repo's dependency graph - importing
it from api/main.py or core/telegram_listener.py at module-load time
would risk a production crash on any environment without that sibling
directory. Run manually (`python scripts/import_provider_research.py`)
whenever the research branch produces new/updated provider findings to
refresh.

"Eligible" = the research branch's own final classification is
Demo-ready (docs/OPT_SIGNALS_RECOMMENDATIONS.md, research repo) - the
only classification with real, plausible, cleanly-linked win/loss/draw
outcomes. Every other classification (Parser-ready but configuration
required, Unsupported/unsafe, Forward observation required,
Insufficient history) either has no real result data or data that
can't be trusted - importing it here would silently launder a known
data-integrity problem into a backtest that looks authoritative.

Every imported row is tagged with a notes field carrying forward the
research branch's own standing caveat (docs/OPT_SIGNALS_RESULT_MATCHING_REPORT.md):
these are the provider's OWN reported outcomes, scraped from Telegram,
never independently verified against a broker. This bridge does not
change that - it only makes the same honestly-labeled data reachable
by the backtest engine.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

RESEARCH_REPO = Path("C:/AXIM-telegram-research")
IMPORT_BATCH_PREFIX = "opt_signals_research"
STARTING_BANKROLL = 1000.0
DEFAULT_PAYOUT_PERCENT = 88  # matches money_studio.PAYOUT_PERCENT - AXIM's real observed historical average

# (source_id, adapter module name) for every provider the research branch
# classified Demo-ready. Title is read from the research DB's own
# sources.title at import time, not hardcoded here, so an emoji/spelling
# drift in the source's real Telegram title never causes a mismatch.
ELIGIBLE_PROVIDERS = [
    (2122892148, "martin_trader"),
    (1851061994, "otc_pro_robot"),
    (1799419388, "tyler_vip_club"),
    (1862249399, "pocket_option_signals"),
    (2116990901, "daniel_fx_trade"),
]


def _load_research_modules():
    """Inserts the research repo's own import roots onto sys.path and
    returns (research_db_module, adapters_module) - done lazily inside a
    function, not at module import time, so importing this script itself
    never fails just because the research worktree happens to be absent."""
    if not RESEARCH_REPO.exists():
        raise FileNotFoundError(
            f"research worktree not found at {RESEARCH_REPO} - this script bridges "
            "Provider Intelligence findings from that repo and cannot run without it."
        )
    sys.path.insert(0, str(RESEARCH_REPO))
    sys.path.insert(0, str(RESEARCH_REPO / "research"))
    sys.path.insert(0, str(RESEARCH_REPO / "research" / "parser"))
    import config as research_config
    import importlib
    adapters = {
        name: importlib.import_module(f"adapters.{name}")
        for _, name in ELIGIBLE_PROVIDERS
    }
    return research_config, adapters


def _fetch_provider_rows(research_config, source_id):
    import sqlite3
    conn = sqlite3.connect(research_config.DB_PATH)
    conn.row_factory = sqlite3.Row
    title_row = conn.execute("SELECT title FROM sources WHERE source_id = ?", (source_id,)).fetchone()
    messages = conn.execute(
        "SELECT message_id, date_utc, text FROM raw_messages WHERE source_id = ? ORDER BY message_id",
        (source_id,),
    ).fetchall()
    conn.close()
    title = title_row["title"] if title_row else str(source_id)
    return title, messages


def _extract_backtestable_trades(adapter_module, messages):
    """Runs the adapter and keeps only cleanly-linked, decided trades:
    signal_message_id known (not an orphan result with nothing to attach
    asset/direction to) and result in win/loss/draw (not unresolved).
    Returns a list of dicts ready for database.create_imported_signal."""
    signal_records, result_links = adapter_module.parse_source(messages)
    by_message_id = {r["source_message_id"]: r for r in signal_records}
    date_by_message_id = {m["message_id"]: m["date_utc"] for m in messages}

    trades = []
    for link in result_links:
        if link["result"] not in ("win", "loss", "draw"):
            continue  # unresolved - no decided outcome to backtest
        signal_message_id = link["signal_message_id"]
        if signal_message_id is None:
            continue  # an orphan result with no signal of its own to attach asset/direction to
        record = by_message_id.get(signal_message_id)
        if record is None or not record["normalized_asset"] or not record["direction"]:
            continue  # can't backtest a trade with an unresolved asset or direction
        received_at = date_by_message_id.get(signal_message_id)
        if not received_at:
            continue
        trades.append({
            "asset": record["normalized_asset"],
            "direction": record["direction"],
            "expiry": record["expiry"],
            "received_at": received_at,
            "result": link["result"],
        })
    trades.sort(key=lambda t: t["received_at"])
    return trades


def import_all_eligible_providers():
    """Returns [{source_id, title, import_batch, imported_count}, ...]."""
    import database
    database.initialize_database()  # idempotent - picks up any schema added since the live API process last started
    research_config, adapters = _load_research_modules()

    results = []
    for source_id, adapter_name in ELIGIBLE_PROVIDERS:
        title, messages = _fetch_provider_rows(research_config, source_id)
        trades = _extract_backtestable_trades(adapters[adapter_name], messages)

        import_batch = f"{IMPORT_BATCH_PREFIX}_{adapter_name}"
        deleted = database.delete_imported_signals_by_batch(import_batch)
        for trade in trades:
            database.create_imported_signal(
                title, trade["asset"], trade["direction"], trade["expiry"], trade["received_at"],
                result=trade["result"],
                notes=(
                    "Provider-reported outcome from OPT SIGNALS Telegram history research "
                    "(docs/OPT_SIGNALS_RESULT_MATCHING_REPORT.md) - NOT independently verified "
                    "against a broker. Classified Demo-ready by the research branch."
                ),
                import_batch=import_batch,
            )
        results.append({
            "source_id": source_id, "title": title, "import_batch": import_batch,
            "replaced_count": deleted, "imported_count": len(trades),
        })
    return results


def run_backtest_for_provider(title, import_batch, created_by="provider_research_import"):
    """Creates and immediately runs one backtest_run for this provider's
    imported signal batch against each of money_studio's 5 canonical
    strategies, sized as virtual (zero-DB-footprint) profile snapshots -
    see money_studio.build_virtual_profile - then generates and persists
    this provider's capital recommendation (core/capital_recommendation.py)
    from the completed run. Returns (run_id, recommendation_id), either
    of which may be None if this provider had no importable trades or no
    rankable strategy."""
    import database
    import backtest_engine
    import capital_recommendation
    import money_studio

    # list_imported_signals defaults to limit=500 (fine for its usual UI-
    # listing callers) - explicit high limit here since this count feeds
    # the recommendation's displayed trades_backtested and must reflect
    # the provider's real total, not be silently truncated (OTC Pro
    # Trading Robot alone has 3724 decided trades).
    signals = database.list_imported_signals(import_batch=import_batch, graded_only=True, limit=100000)
    if not signals:
        return None, None

    strategy_profiles = [
        money_studio.build_virtual_profile(s["key"], s["name"], STARTING_BANKROLL) for s in money_studio.STRATEGIES
    ]

    signal_pool = {"source": "imported", "channel_filter": [title]}
    run_id = database.create_backtest_run(
        f"Auto: {title}", signal_pool, STARTING_BANKROLL,
        default_payout_percent=DEFAULT_PAYOUT_PERCENT, session_window="daily",
        created_by=created_by,
    )
    for profile in strategy_profiles:
        database.create_backtest_strategy(run_id, None, profile["name"], profile)

    backtest_engine.run_backtest(run_id)
    recommendation_id = capital_recommendation.generate_recommendation_for_provider(
        title, run_id, trades_backtested=len(signals),
    )
    return run_id, recommendation_id


def main():
    print(f"Importing {len(ELIGIBLE_PROVIDERS)} eligible providers from {RESEARCH_REPO} ...")
    import_results = import_all_eligible_providers()
    for r in import_results:
        print(f"  {r['title']!r}: {r['imported_count']} decided trades imported (batch={r['import_batch']})")

    print("\nRunning automatic backtests (4 official Money Studio strategies each) + generating recommendations ...")
    for r in import_results:
        if r["imported_count"] == 0:
            print(f"  {r['title']!r}: skipped - no importable trades")
            continue
        run_id, recommendation_id = run_backtest_for_provider(r["title"], r["import_batch"])
        print(f"  {r['title']!r}: backtest_run_id={run_id} recommendation_id={recommendation_id}")


if __name__ == "__main__":
    main()

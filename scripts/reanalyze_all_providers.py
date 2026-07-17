"""Phase 2 Priority #4: automatic scheduled provider re-analysis.
Run via a Windows Scheduled Task (scripts/install_reanalysis_task.ps1)
on a recurring basis - re-runs core/provider_onboarding.py's analysis
for every provider that has both an existing recommendation and a real,
currently-synced Telegram channel, and notifies the owner
(core/database.create_notification, visible in the Notification Center)
if a provider's recommendation meaningfully changed.

Standalone script, not a core/ module: like scripts/
import_provider_research.py, it needs a live authenticated Telegram
session (core/telegram_channels.fetch_channel_raw_history) - an
environment-specific dependency, not something api/main.py or
core/telegram_listener.py should import at module-load time.
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    import database
    import provider_reanalysis

    database.initialize_database()
    print("Re-analyzing every known provider with a real, live Telegram channel...")
    summary = await provider_reanalysis.reanalyze_all_known_providers()

    for entry in summary:
        if entry["status"] == "skipped_no_live_channel":
            print(f"  {entry['source_label']!r}: skipped - {entry['note']}")
        elif entry["status"] == "refresh_failed":
            print(f"  {entry['source_label']!r}: {entry['note']}")
        elif entry["status"] == "error":
            print(f"  {entry['source_label']!r}: ERROR - {entry['note']}")
        elif entry["changes"]:
            print(f"  {entry['source_label']!r}: RE-ANALYZED, notified owner:")
            for note in entry["changes"]:
                print(f"    - {note}")
        else:
            print(f"  {entry['source_label']!r}: re-analyzed, no meaningful change")

    print(f"\n{len(summary)} provider(s) considered.")


if __name__ == "__main__":
    asyncio.run(main())

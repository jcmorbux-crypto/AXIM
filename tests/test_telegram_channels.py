import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

import telegram_channels


class MessageToSignalRowTests(unittest.TestCase):
    """_message_to_signal_row() - the pure conversion core of
    fetch_channel_history(), tested without any TelegramClient/network
    dependency. Uses real parsers.signal_parser.parse_signal() (not
    mocked) so this proves the SAME parser the live listener uses
    recognizes historical message text the same way."""

    def test_real_signal_text_produces_a_row(self):
        row = telegram_channels._message_to_signal_row(
            "EUR/USD OTC BUY 1 Minute", datetime(2026, 1, 1, 10, 0, 0), 12345, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["asset"], "EUR/USD OTC")
        self.assertEqual(row["direction"], "BUY")
        self.assertEqual(row["source_label"], "Test Channel")
        self.assertIsNone(row["result"])
        self.assertIsNone(row["payout_percent"])
        self.assertIn("12345", row["notes"])

    def test_non_signal_text_returns_none(self):
        row = telegram_channels._message_to_signal_row(
            "Good morning everyone, have a great trading day!",
            datetime(2026, 1, 1, 10, 0, 0), 12346, "Test Channel",
        )
        self.assertIsNone(row)

    def test_empty_text_returns_none(self):
        row = telegram_channels._message_to_signal_row(
            "", datetime(2026, 1, 1, 10, 0, 0), 12347, "Test Channel",
        )
        self.assertIsNone(row)

    def test_missing_date_falls_back_to_now_not_a_crash(self):
        row = telegram_channels._message_to_signal_row(
            "GBP/USD OTC SELL 5 Minutes", None, 12348, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertTrue(row["received_at"])  # some ISO timestamp was produced, not blank/error

    def test_expiry_carried_through_when_present(self):
        row = telegram_channels._message_to_signal_row(
            "USD/JPY OTC BUY 5 Minutes", datetime(2026, 1, 1, 10, 0, 0), 12349, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["expiry"])


def _filter(title_text, pinned_peers=None, include_peers=None, exclude_peers=None,
            groups=False, broadcasts=False, bots=False, contacts=False, non_contacts=False):
    """A stand-in for telethon.tl.types.DialogFilter - real ones carry a
    TextWithEntities title (.text is the plain string), not a bare str."""
    return SimpleNamespace(
        title=SimpleNamespace(text=title_text),
        pinned_peers=pinned_peers or [], include_peers=include_peers or [], exclude_peers=exclude_peers or [],
        groups=groups, broadcasts=broadcasts, bots=bots, contacts=contacts, non_contacts=non_contacts,
    )


def _channel_peer(channel_id):
    return SimpleNamespace(channel_id=channel_id)


def _dialog(entity_id, name, is_user=False, is_channel=True, is_group=False, bot=False, contact=False):
    entity = SimpleNamespace(id=entity_id, username=None, bot=bot, contact=contact)
    return SimpleNamespace(id=entity_id, name=name, entity=entity,
                            is_user=is_user, is_channel=is_channel, is_group=is_group)


class FindFolderTests(IsolatedAsyncioTestCase):
    """_find_folder() - matches a Telegram folder by name against the
    real GetDialogFiltersRequest response shape (title.text, not a bare
    string), case/whitespace-insensitively, and never raises on folder
    types (e.g. DialogFilterDefault) that have no title at all."""

    async def test_finds_matching_folder_by_exact_name(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[
            _filter("OPT SIGNALS"), _filter("Other Folder"),
        ]))
        found = await telegram_channels._find_folder(client, "OPT SIGNALS")
        self.assertEqual(found.title.text, "OPT SIGNALS")

    async def test_match_is_case_and_whitespace_insensitive(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[_filter("  opt signals  ")]))
        found = await telegram_channels._find_folder(client, "OPT SIGNALS")
        self.assertIsNotNone(found)

    async def test_returns_none_when_no_folder_matches(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[_filter("Other Folder")]))
        found = await telegram_channels._find_folder(client, "OPT SIGNALS")
        self.assertIsNone(found)

    async def test_skips_filter_types_with_no_title_without_raising(self):
        # DialogFilterDefault (the "All Chats" pseudo-folder) has no
        # title at all - must be skipped, not crash the sync.
        default_filter = SimpleNamespace()
        client = AsyncMock(return_value=SimpleNamespace(filters=[default_filter, _filter("OPT SIGNALS")]))
        found = await telegram_channels._find_folder(client, "OPT SIGNALS")
        self.assertEqual(found.title.text, "OPT SIGNALS")

    async def test_returns_none_and_does_not_raise_when_request_fails(self):
        client = AsyncMock(side_effect=ConnectionError("Telegram connection dropped"))
        found = await telegram_channels._find_folder(client, "OPT SIGNALS")
        self.assertIsNone(found)


class DialogInFolderTests(unittest.TestCase):
    """_dialog_in_folder() - real folder membership (confirmed live
    against the actual "OPT SIGNALS" folder) is NOT a Telethon
    iter_dialogs(folder=id) call - that only understands the legacy
    Archived pseudo-folder and raises FolderIdInvalidError on a real
    DialogFilter id. Membership is pinned_peers/include_peers minus
    exclude_peers, plus optional category flags."""

    def test_dialog_matches_via_include_peers(self):
        f = _filter("OPT SIGNALS", include_peers=[_channel_peer(100)])
        self.assertTrue(telegram_channels._dialog_in_folder(_dialog(100, "In"), f))

    def test_dialog_matches_via_pinned_peers(self):
        f = _filter("OPT SIGNALS", pinned_peers=[_channel_peer(200)])
        self.assertTrue(telegram_channels._dialog_in_folder(_dialog(200, "Pinned"), f))

    def test_dialog_not_in_folder_when_not_listed_and_no_category_flags(self):
        f = _filter("OPT SIGNALS", include_peers=[_channel_peer(100)])
        self.assertFalse(telegram_channels._dialog_in_folder(_dialog(999, "Not In Folder"), f))

    def test_exclude_peers_wins_even_if_also_in_include_peers(self):
        f = _filter("OPT SIGNALS", include_peers=[_channel_peer(100)], exclude_peers=[_channel_peer(100)])
        self.assertFalse(telegram_channels._dialog_in_folder(_dialog(100, "Excluded"), f))

    def test_groups_category_flag_includes_any_group(self):
        f = _filter("Groups Folder", groups=True)
        d = _dialog(5, "A Group", is_channel=False, is_group=True)
        self.assertTrue(telegram_channels._dialog_in_folder(d, f))

    def test_broadcasts_category_flag_includes_channels_not_groups(self):
        f = _filter("Channels Folder", broadcasts=True)
        channel = _dialog(6, "A Channel", is_channel=True, is_group=False)
        group = _dialog(7, "A Group", is_channel=True, is_group=True)
        self.assertTrue(telegram_channels._dialog_in_folder(channel, f))
        self.assertFalse(telegram_channels._dialog_in_folder(group, f))

    def test_bots_category_flag_includes_bots_only(self):
        f = _filter("Bots Folder", bots=True)
        bot = _dialog(8, "A Bot", is_user=True, is_channel=False, bot=True)
        human = _dialog(9, "A Human", is_user=True, is_channel=False, bot=False)
        self.assertTrue(telegram_channels._dialog_in_folder(bot, f))
        self.assertFalse(telegram_channels._dialog_in_folder(human, f))

    def test_contacts_and_non_contacts_flags(self):
        contacts_folder = _filter("Contacts", contacts=True)
        non_contacts_folder = _filter("Non-Contacts", non_contacts=True)
        contact = _dialog(10, "A Contact", is_user=True, is_channel=False, contact=True)
        stranger = _dialog(11, "A Stranger", is_user=True, is_channel=False, contact=False)
        self.assertTrue(telegram_channels._dialog_in_folder(contact, contacts_folder))
        self.assertFalse(telegram_channels._dialog_in_folder(stranger, contacts_folder))
        self.assertTrue(telegram_channels._dialog_in_folder(stranger, non_contacts_folder))
        self.assertFalse(telegram_channels._dialog_in_folder(contact, non_contacts_folder))


class _FakeClient:
    """Stands in for TelegramClient: __call__ answers GetDialogFiltersRequest,
    iter_dialogs() yields every real dialog (never takes a folder= id, matching
    the real Telethon behavior confirmed live). Defined as a real class (not a
    mock with an instance-level __call__) because Python looks up dunder
    methods on the type for implicit invocations like client(request) - an
    instance attribute wouldn't be found."""

    def __init__(self, filters, all_dialogs):
        self._filters = filters
        self._all_dialogs = all_dialogs
        self.start = AsyncMock()
        self.disconnect = AsyncMock()

    async def __call__(self, request):
        return SimpleNamespace(filters=self._filters)

    def iter_dialogs(self):
        async def gen():
            for d in self._all_dialogs:
                yield d
        return gen()


class SyncDialogsFolderScopingTests(IsolatedAsyncioTestCase):
    """sync_dialogs() - proves the folder lookup actually scopes which
    dialogs get upserted, and that an unmatched folder name falls back
    to syncing everything rather than silently syncing zero channels."""

    def setUp(self):
        self.upserted = []
        self._orig_upsert = telegram_channels.database.upsert_channel
        telegram_channels.database.upsert_channel = lambda **kwargs: self.upserted.append(kwargs)
        self.removed_calls = []
        self._orig_mark_removed = telegram_channels.database.mark_channels_removed_from_folder
        telegram_channels.database.mark_channels_removed_from_folder = lambda seen: self.removed_calls.append(seen)
        self._orig_creds = telegram_channels._telegram_credentials
        telegram_channels._telegram_credentials = lambda: (12345, "hash", "+10000000000")
        self._orig_client_cls = telegram_channels.TelegramClient

    def tearDown(self):
        telegram_channels.database.upsert_channel = self._orig_upsert
        telegram_channels.database.mark_channels_removed_from_folder = self._orig_mark_removed
        telegram_channels._telegram_credentials = self._orig_creds
        telegram_channels.TelegramClient = self._orig_client_cls

    async def test_syncs_only_dialogs_in_the_matched_folder(self):
        matched, unmatched = _dialog(1, "In Folder"), _dialog(2, "Not In Folder")
        client = _FakeClient([_filter("OPT SIGNALS", include_peers=[_channel_peer(1)])], [matched, unmatched])
        telegram_channels.TelegramClient = lambda *a, **k: client
        count = await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(count, 1)
        self.assertEqual(self.upserted[0]["chat_id"], 1)

    async def test_removal_detection_runs_only_when_the_real_folder_was_resolved(self):
        matched, unmatched = _dialog(1, "In Folder"), _dialog(2, "Not In Folder")
        client = _FakeClient([_filter("OPT SIGNALS", include_peers=[_channel_peer(1)])], [matched, unmatched])
        telegram_channels.TelegramClient = lambda *a, **k: client
        await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(len(self.removed_calls), 1)
        self.assertEqual(self.removed_calls[0], [1])  # only the in-folder dialog was "seen"

    async def test_removal_detection_skipped_on_fallback_to_all_dialogs(self):
        d1, d2 = _dialog(1, "A"), _dialog(2, "B")
        client = _FakeClient([_filter("Some Other Folder")], [d1, d2])
        telegram_channels.TelegramClient = lambda *a, **k: client
        await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(self.removed_calls, [])

    async def test_falls_back_to_all_dialogs_when_folder_not_found(self):
        d1, d2 = _dialog(1, "A"), _dialog(2, "B")
        client = _FakeClient([_filter("Some Other Folder")], [d1, d2])
        telegram_channels.TelegramClient = lambda *a, **k: client
        count = await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(count, 2)

    async def test_folder_name_none_syncs_everything(self):
        d1, d2 = _dialog(1, "A"), _dialog(2, "B")
        client = _FakeClient([_filter("OPT SIGNALS", include_peers=[_channel_peer(1)])], [d1, d2])
        telegram_channels.TelegramClient = lambda *a, **k: client
        count = await telegram_channels.sync_dialogs(folder_name=None)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()

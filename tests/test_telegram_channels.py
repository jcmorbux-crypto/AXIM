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


def _filter(folder_id, title_text):
    """A stand-in for telethon.tl.types.DialogFilter - real ones carry a
    TextWithEntities title (.text is the plain string), not a bare str."""
    return SimpleNamespace(id=folder_id, title=SimpleNamespace(text=title_text))


class FindFolderIdTests(IsolatedAsyncioTestCase):
    """_find_folder_id() - matches a Telegram folder by name against the
    real GetDialogFiltersRequest response shape (title.text, not a bare
    string), case/whitespace-insensitively, and never raises on folder
    types (e.g. DialogFilterDefault) that have no title/id at all."""

    async def test_finds_matching_folder_by_exact_name(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[
            _filter(1, "OPT SIGNALS"), _filter(2, "Other Folder"),
        ]))
        folder_id = await telegram_channels._find_folder_id(client, "OPT SIGNALS")
        self.assertEqual(folder_id, 1)

    async def test_match_is_case_and_whitespace_insensitive(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[
            _filter(7, "  opt signals  "),
        ]))
        folder_id = await telegram_channels._find_folder_id(client, "OPT SIGNALS")
        self.assertEqual(folder_id, 7)

    async def test_returns_none_when_no_folder_matches(self):
        client = AsyncMock(return_value=SimpleNamespace(filters=[
            _filter(1, "Other Folder"),
        ]))
        folder_id = await telegram_channels._find_folder_id(client, "OPT SIGNALS")
        self.assertIsNone(folder_id)

    async def test_skips_filter_types_with_no_title_without_raising(self):
        # DialogFilterDefault (the "All Chats" pseudo-folder) has neither
        # a title nor a usable id - must be skipped, not crash the sync.
        default_filter = SimpleNamespace()
        client = AsyncMock(return_value=SimpleNamespace(
            filters=[default_filter, _filter(3, "OPT SIGNALS")]
        ))
        folder_id = await telegram_channels._find_folder_id(client, "OPT SIGNALS")
        self.assertEqual(folder_id, 3)

    async def test_returns_none_and_does_not_raise_when_request_fails(self):
        client = AsyncMock(side_effect=ConnectionError("Telegram connection dropped"))
        folder_id = await telegram_channels._find_folder_id(client, "OPT SIGNALS")
        self.assertIsNone(folder_id)


class _FakeClient:
    """Stands in for TelegramClient: __call__ answers GetDialogFiltersRequest,
    iter_dialogs(folder=...) answers per-folder dialog lists. Defined as a
    real class (not a mock with an instance-level __call__) because Python
    looks up dunder methods on the type for implicit invocations like
    client(request) - an instance attribute wouldn't be found."""

    def __init__(self, filters, dialogs_by_folder):
        self._filters = filters
        self._dialogs_by_folder = dialogs_by_folder
        self.start = AsyncMock()
        self.disconnect = AsyncMock()

    async def __call__(self, request):
        return SimpleNamespace(filters=self._filters)

    def iter_dialogs(self, folder=None):
        async def gen():
            for d in self._dialogs_by_folder.get(folder, []):
                yield d
        return gen()


class SyncDialogsFolderScopingTests(IsolatedAsyncioTestCase):
    """sync_dialogs() - proves the folder id lookup actually reaches
    client.iter_dialogs(folder=...), and that an unmatched folder name
    falls back to syncing everything (folder=None) rather than silently
    syncing zero channels."""

    def setUp(self):
        self.upserted = []
        self._orig_upsert = telegram_channels.database.upsert_channel
        telegram_channels.database.upsert_channel = lambda **kwargs: self.upserted.append(kwargs)
        self._orig_creds = telegram_channels._telegram_credentials
        telegram_channels._telegram_credentials = lambda: (12345, "hash", "+10000000000")
        self._orig_client_cls = telegram_channels.TelegramClient

    def tearDown(self):
        telegram_channels.database.upsert_channel = self._orig_upsert
        telegram_channels._telegram_credentials = self._orig_creds
        telegram_channels.TelegramClient = self._orig_client_cls

    def _dialog(self, chat_id, name):
        return SimpleNamespace(id=chat_id, name=name, entity=SimpleNamespace(username=None),
                                is_user=False, is_channel=True, is_group=False)

    async def test_syncs_only_dialogs_in_the_matched_folder(self):
        matched = self._dialog(1, "In Folder")
        client = _FakeClient([_filter(9, "OPT SIGNALS")],
                              {9: [matched], None: [matched, self._dialog(2, "Not In Folder")]})
        telegram_channels.TelegramClient = lambda *a, **k: client
        count = await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(count, 1)
        self.assertEqual(self.upserted[0]["chat_id"], 1)

    async def test_falls_back_to_all_dialogs_when_folder_not_found(self):
        d1, d2 = self._dialog(1, "A"), self._dialog(2, "B")
        client = _FakeClient([_filter(9, "Some Other Folder")], {None: [d1, d2]})
        telegram_channels.TelegramClient = lambda *a, **k: client
        count = await telegram_channels.sync_dialogs(folder_name="OPT SIGNALS")
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()

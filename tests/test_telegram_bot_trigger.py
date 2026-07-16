import asyncio
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

import database
import telegram_bot_trigger
import trade_coordinator
from trade_coordinator import TradeCoordinator


def _run(coro):
    return asyncio.run(coro)


class FakeMessage:
    def __init__(self, raw_text, msg_id=1, date=None):
        self.raw_text = raw_text
        self.id = msg_id
        self.date = date or datetime.now()


class _FakeConversationCtx:
    def __init__(self, client, chat_id):
        self.client = client
        self.chat_id = chat_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def send_message(self, text):
        self.client.sent_messages.append((self.chat_id, text))

    async def get_response(self):
        if not self.client.responses:
            raise asyncio.TimeoutError()
        item = self.client.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """A queue of pre-scripted replies, one consumed per conversation()
    call - real telethon.TelegramClient.conversation() opens a temporary,
    conversation-scoped listener; this fake matches only the surface
    telegram_bot_trigger.py actually uses (send_message/get_response as
    an async context manager)."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.sent_messages = []
        self.chat_ids_used = []

    def conversation(self, chat_id, timeout=None):
        self.chat_ids_used.append(chat_id)
        return _FakeConversationCtx(self, chat_id)


class FakeWorkerPool:
    def __init__(self):
        self.released = []

    async def acquire_worker(self, timeout=None):
        return object()

    def release_worker(self, worker):
        self.released.append(worker)


class ChannelLookupTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_bot_channel(self, trigger_command="/signal"):
        database.upsert_channel(chat_id=555, username="testbot", title="TestBot", kind="bot")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "555"][0]["id"]
        database.set_channel_config(channel_id, source_type="bot_command", trigger_command=trigger_command,
                                     command_wait_for_result=1)
        return channel_id

    def test_finds_bot_command_channel_covered_by_session(self):
        channel_id = self._make_bot_channel()
        session_id = database.start_trading_session("Test", [channel_id], "DEMO")
        session = database.get_trading_session(session_id)
        result = telegram_bot_trigger.bot_command_channel_for_session(session)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], channel_id)

    def test_passive_channel_not_treated_as_bot_command(self):
        database.upsert_channel(chat_id=777, username="passivechan", title="Passive", kind="channel")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "777"][0]["id"]
        session_id = database.start_trading_session("Test", [channel_id], "DEMO")
        session = database.get_trading_session(session_id)
        self.assertIsNone(telegram_bot_trigger.bot_command_channel_for_session(session))

    def test_bot_command_channel_without_trigger_command_not_matched(self):
        database.upsert_channel(chat_id=888, username="notrigger", title="NoTrigger", kind="bot")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "888"][0]["id"]
        database.set_channel_config(channel_id, source_type="bot_command")  # no trigger_command set
        session_id = database.start_trading_session("Test", [channel_id], "DEMO")
        session = database.get_trading_session(session_id)
        self.assertIsNone(telegram_bot_trigger.bot_command_channel_for_session(session))


class RunSessionLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        database.upsert_channel(chat_id=555, username="testbot", title="TestBot", kind="bot")
        self.channel_id = [c for c in database.list_channels() if c["chat_id"] == "555"][0]["id"]

        trade_coordinator.PREVIEW_ONLY = True  # short-circuit before the worker pool

        self._orig_wait_timeout = telegram_bot_trigger.TRADE_RESULT_TIMEOUT_SECONDS
        self._orig_wait_poll = telegram_bot_trigger.TRADE_RESULT_POLL_SECONDS
        self._orig_request_interval = telegram_bot_trigger.REQUEST_INTERVAL_SECONDS
        telegram_bot_trigger.REQUEST_INTERVAL_SECONDS = 0

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        telegram_bot_trigger.TRADE_RESULT_TIMEOUT_SECONDS = self._orig_wait_timeout
        telegram_bot_trigger.TRADE_RESULT_POLL_SECONDS = self._orig_wait_poll
        telegram_bot_trigger.REQUEST_INTERVAL_SECONDS = self._orig_request_interval

    def _make_channel(self, trigger_command="/signal", wait_for_result=0, max_requests=None):
        database.set_channel_config(
            self.channel_id, source_type="bot_command", trigger_command=trigger_command,
            command_wait_for_result=wait_for_result,
            **({"max_requests_per_session": max_requests} if max_requests is not None else {}),
        )
        return database.get_channel(self.channel_id)

    def test_sends_trigger_command_and_routes_the_parsed_reply(self):
        import broker_account_manager
        channel_row = self._make_channel(max_requests=1)
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        client = FakeClient(responses=[
            FakeMessage("EUR/USD OTC BUY 1 Minute"),
        ])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        original_route_signal = broker_account_manager.route_signal
        mock_route_signal = AsyncMock(return_value={"status": "preview", "trade_id": 1})
        broker_account_manager.route_signal = mock_route_signal
        try:
            _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))
        finally:
            broker_account_manager.route_signal = original_route_signal

        self.assertEqual(client.sent_messages, [(555, "/signal")])
        mock_route_signal.assert_awaited_once()
        call_kwargs = mock_route_signal.await_args.kwargs
        self.assertEqual(call_kwargs["session_id"], session_id)
        signal_arg = mock_route_signal.await_args.args[0]
        self.assertEqual(signal_arg["asset"], "EUR/USD OTC")
        self.assertEqual(signal_arg["direction"], "BUY")

    def test_stops_after_max_requests_per_session(self):
        channel_row = self._make_channel(max_requests=2)
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        client = FakeClient(responses=[
            FakeMessage("EUR/USD OTC BUY 1 Minute"),
            FakeMessage("GBP/USD OTC SELL 1 Minute"),
            FakeMessage("USD/JPY OTC BUY 1 Minute"),  # must never be requested
        ])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))

        self.assertEqual(len(client.sent_messages), 2)

    def test_stops_when_session_is_no_longer_active(self):
        channel_row = self._make_channel()  # no max_requests cap
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        database.stop_trading_session(session_id, "stopped_manual")
        client = FakeClient(responses=[FakeMessage("EUR/USD OTC BUY 1 Minute")])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))

        self.assertEqual(client.sent_messages, [])  # never sent - session was already inactive

    def test_unparseable_reply_does_not_crash_the_loop(self):
        channel_row = self._make_channel(max_requests=2)
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        client = FakeClient(responses=[
            FakeMessage("this is not a signal"),
            FakeMessage("EUR/USD OTC BUY 1 Minute"),
        ])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))

        self.assertEqual(len(client.sent_messages), 2)  # kept going after the bad reply

    def test_reply_timeout_does_not_crash_the_loop(self):
        channel_row = self._make_channel(max_requests=2)
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        client = FakeClient(responses=[
            asyncio.TimeoutError(),
            FakeMessage("EUR/USD OTC BUY 1 Minute"),
        ])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))

        self.assertEqual(len(client.sent_messages), 2)

    def test_removes_itself_from_active_loops_when_done(self):
        channel_row = self._make_channel(max_requests=1)
        session_id = database.start_trading_session("Test", [self.channel_id], "DEMO")
        telegram_bot_trigger._active_loops[session_id] = "placeholder"
        client = FakeClient(responses=[FakeMessage("EUR/USD OTC BUY 1 Minute")])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.run_session_loop(client, session_id, channel_row, coordinator))

        self.assertNotIn(session_id, telegram_bot_trigger._active_loops)


class SupervisorTickTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        trade_coordinator.PREVIEW_ONLY = True
        telegram_bot_trigger._active_loops.clear()
        self._orig_request_interval = telegram_bot_trigger.REQUEST_INTERVAL_SECONDS
        telegram_bot_trigger.REQUEST_INTERVAL_SECONDS = 0

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        telegram_bot_trigger._active_loops.clear()
        telegram_bot_trigger.REQUEST_INTERVAL_SECONDS = self._orig_request_interval

    def test_starts_a_loop_for_a_new_bot_command_session(self):
        # A real spawned task with an empty response queue and
        # REQUEST_INTERVAL_SECONDS=0 can race to completion (and remove
        # itself from _active_loops) inside asyncio.run()'s own task-
        # cancellation cleanup, before this test gets to assert anything -
        # patch asyncio.create_task with a never-resolving Future instead,
        # so this test verifies supervisor_tick's own bookkeeping (did it
        # start exactly one task, tracked under the right session_id)
        # without racing the fake loop's real execution speed.
        database.upsert_channel(chat_id=555, username="testbot", title="TestBot", kind="bot")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "555"][0]["id"]
        database.set_channel_config(channel_id, source_type="bot_command", trigger_command="/signal")
        session_id = database.start_trading_session("Test", [channel_id], "DEMO")
        client = FakeClient(responses=[])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        import asyncio as asyncio_module
        original_create_task = asyncio_module.create_task
        created = []

        def _fake_create_task(coro):
            coro.close()  # avoid a "coroutine was never awaited" warning
            future = asyncio_module.get_event_loop().create_future()
            created.append(future)
            return future

        asyncio_module.create_task = _fake_create_task
        try:
            _run(telegram_bot_trigger.supervisor_tick(client, coordinator))
        finally:
            asyncio_module.create_task = original_create_task

        self.assertEqual(len(created), 1)
        self.assertIn(session_id, telegram_bot_trigger._active_loops)
        self.assertIs(telegram_bot_trigger._active_loops[session_id], created[0])

    def test_does_not_start_a_second_loop_for_the_same_session(self):
        database.upsert_channel(chat_id=555, username="testbot", title="TestBot", kind="bot")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "555"][0]["id"]
        database.set_channel_config(channel_id, source_type="bot_command", trigger_command="/signal")
        session_id = database.start_trading_session("Test", [channel_id], "DEMO")
        telegram_bot_trigger._active_loops[session_id] = "already-running-placeholder"
        client = FakeClient(responses=[])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.supervisor_tick(client, coordinator))

        self.assertEqual(telegram_bot_trigger._active_loops[session_id], "already-running-placeholder")

    def test_ignores_sessions_with_no_bot_command_channel(self):
        database.upsert_channel(chat_id=777, username="passivechan", title="Passive", kind="channel")
        channel_id = [c for c in database.list_channels() if c["chat_id"] == "777"][0]["id"]
        database.start_trading_session("Test", [channel_id], "DEMO")
        client = FakeClient(responses=[])
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)

        _run(telegram_bot_trigger.supervisor_tick(client, coordinator))

        self.assertEqual(telegram_bot_trigger._active_loops, {})


if __name__ == "__main__":
    unittest.main()

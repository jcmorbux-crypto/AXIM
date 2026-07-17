import asyncio
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import provider_onboarding_routes as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class PreviewProviderRouteTests(IsolatedAsyncioTestCase):
    async def test_preview_passes_through_days_and_excludes_nothing(self):
        body = routes.PreviewProviderRequest(chat_id=123, days=14)
        fake_preview = AsyncMock(return_value={"status": "complete", "sample": []})
        with patch("provider_onboarding.preview_provider", fake_preview):
            result = await routes.preview_provider(body, user=_FAKE_ADMIN)
        self.assertEqual(result["status"], "complete")
        fake_preview.assert_awaited_once_with(123, source_label=None, days=14)

    async def test_preview_failure_becomes_a_clean_502(self):
        from fastapi import HTTPException
        body = routes.PreviewProviderRequest(chat_id=123)
        fake_preview = AsyncMock(side_effect=RuntimeError("Telegram session expired"))
        with patch("provider_onboarding.preview_provider", fake_preview):
            with self.assertRaises(HTTPException) as ctx:
                await routes.preview_provider(body, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 502)


class AnalyzeProviderRouteTests(IsolatedAsyncioTestCase):
    async def test_analyze_passes_through_days_and_excluded_ids(self):
        body = routes.AnalyzeProviderRequest(chat_id=123, days=60, excluded_message_ids=[1, 2])
        fake_analyze = AsyncMock(return_value={"status": "complete"})
        with patch("provider_onboarding.analyze_and_onboard_provider", fake_analyze):
            result = await routes.analyze_provider(body, user=_FAKE_ADMIN)
        self.assertEqual(result["status"], "complete")
        fake_analyze.assert_awaited_once_with(
            123, source_label=None, created_by=_FAKE_ADMIN["email"], days=60, excluded_message_ids={1, 2},
        )

    async def test_no_excluded_ids_passes_none_not_an_empty_set(self):
        body = routes.AnalyzeProviderRequest(chat_id=123)
        fake_analyze = AsyncMock(return_value={"status": "complete"})
        with patch("provider_onboarding.analyze_and_onboard_provider", fake_analyze):
            await routes.analyze_provider(body, user=_FAKE_ADMIN)
        _, kwargs = fake_analyze.call_args
        self.assertIsNone(kwargs["excluded_message_ids"])


if __name__ == "__main__":
    import unittest
    unittest.main()

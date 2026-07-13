from __future__ import annotations

import asyncio
import os

import pytest

import adapters
from imagegen_core.http_client import close_all_sessions
from media import extract_all_media


pytestmark = pytest.mark.skipif(
    os.getenv("ASTRBOT_IMAGEGEN_LIVE") != "1",
    reason="set ASTRBOT_IMAGEGEN_LIVE=1 to run live provider tests",
)


def test_live_text_to_image_provider():
    async def scenario():
        config = {
            "base_url": os.environ["ASTRBOT_IMAGEGEN_BASE_URL"],
            "api_key": os.environ["ASTRBOT_IMAGEGEN_API_KEY"],
            "model": os.environ["ASTRBOT_IMAGEGEN_MODEL"],
            "size": os.getenv("ASTRBOT_IMAGEGEN_SIZE", "1024x1024"),
        }
        try:
            response = await adapters.image_generation(
                config, "一只红色纸鹤，纯白背景", timeout=300
            )
            assert extract_all_media(response)
        finally:
            await close_all_sessions()

    asyncio.run(scenario())

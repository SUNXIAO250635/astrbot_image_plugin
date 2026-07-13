from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import aiohttp

import media
from media import extract_all_media
from tests.fakes.runtime import image_data_uri


ROOT = Path(__file__).resolve().parents[1]


def test_extracts_data_uri_from_response_dictionary():
    value = image_data_uri(b"image")
    response = {"data": [{"image_url": value}]}

    assert extract_all_media(response) == [("image", value)]


def test_extracts_nested_newapi_video_result():
    response = {
        "code": "success",
        "data": {
            "status": "SUCCESS",
            "result_url": "https://cdn.invalid/output.mp4",
        },
    }

    assert extract_all_media(response) == [("video", "https://cdn.invalid/output.mp4")]


def test_extracts_native_image_url_array_and_file_download_url():
    assert extract_all_media(
        {"data": {"image_urls": ["https://cdn.invalid/native.png"]}}
    ) == [("image", "https://cdn.invalid/native.png")]
    assert extract_all_media(
        {"file": {"download_url": "https://cdn.invalid/native.mp4"}}
    ) == [("video", "https://cdn.invalid/native.mp4")]


def test_extracts_multiple_media_urls_from_chat_content():
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "https://cdn.invalid/a.png"},
                        {"type": "text", "text": "https://cdn.invalid/b.mp4"},
                    ]
                }
            }
        ]
    }

    assert extract_all_media(response) == [
        ("video", "https://cdn.invalid/b.mp4"),
        ("image", "https://cdn.invalid/a.png"),
    ]


def test_download_uses_shared_session_with_request_timeout(tmp_path):
    class Content:
        async def iter_chunked(self, _size):
            yield b"image"

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Session:
        def __init__(self):
            self.timeout = None

        def get(self, _url, **kwargs):
            self.timeout = kwargs["timeout"]
            return Response()

    async def scenario():
        session = Session()
        with patch.object(media, "get_session", return_value=session):
            kind, path = await media.download_to_file(
                "https://cdn.invalid/test.png", str(tmp_path), "download", timeout=17
            )
        assert kind == "image"
        assert session.timeout == aiohttp.ClientTimeout(total=17)
        assert open(path, "rb").read() == b"image"

    asyncio.run(scenario())


def test_media_imports_cleanly_before_imagegen_core():
    completed = subprocess.run(
        [sys.executable, "-c", "import media; print('MEDIA_OK')"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "MEDIA_OK"

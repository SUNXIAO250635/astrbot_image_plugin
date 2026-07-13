from __future__ import annotations

from media import extract_all_media
from tests.fakes.runtime import image_data_uri


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

    assert extract_all_media(response) == [
        ("video", "https://cdn.invalid/output.mp4")
    ]


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

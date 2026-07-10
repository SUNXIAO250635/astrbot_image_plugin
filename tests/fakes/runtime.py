from __future__ import annotations

import base64
from dataclasses import dataclass
from types import SimpleNamespace

from astrbot.api.event import MessageChain


@dataclass
class EventResult:
    kind: str
    value: object


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, unified_msg_origin, chain):
        self.sent.append((unified_msg_origin, chain))


class FakeEvent:
    def __init__(
        self,
        components=None,
        sender_id="user-1",
        group_id="",
        unified_msg_origin="test:private:user-1",
        fail_send_calls=None,
    ):
        self.message_obj = SimpleNamespace(
            message=list(components or []),
            sender_id=sender_id,
            group_id=group_id,
        )
        self.sender_id = sender_id
        self.group_id = group_id
        self.unified_msg_origin = unified_msg_origin
        self.sent = []
        self.stopped = False
        self._send_calls = 0
        self._fail_send_calls = set(fail_send_calls or [])

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id

    def plain_result(self, text):
        return EventResult("plain", text)

    def image_result(self, value):
        return EventResult("image", value)

    def chain_result(self, chain):
        return EventResult("chain", list(chain))

    async def send(self, chain):
        self._send_calls += 1
        if self._send_calls in self._fail_send_calls:
            raise RuntimeError("simulated send failure")
        assert isinstance(chain, MessageChain)
        self.sent.append(chain)

    def stop_event(self):
        self.stopped = True


async def collect_async_generator(generator):
    return [item async for item in generator]


def image_data_uri(data=b"test-image", mime="image/png"):
    encoded = base64.b64encode(data).decode()
    return f"data:{mime};base64,{encoded}"


def plugin_config():
    return {
        "adapter_image_generation": {
            "base_url": "https://images.invalid",
            "api_key": "",
            "model": "test-image",
            "size": "1024x1024",
            "n": 1,
        },
        "adapter_image_edits": {
            "base_url": "https://edits.invalid",
            "api_key": "",
            "model": "test-edit",
            "size": "1024x1024",
            "n": 1,
        },
        "adapter_openai_chat": {
            "base_url": "https://chat.invalid",
            "api_key": "",
            "model": "test-chat",
        },
        "adapter_openai_video": {
            "base_url": "https://video.invalid",
            "api_key": "",
            "model": "test-video",
            "seconds": 8,
        },
        "generation_options": {
            "video_via_strategy": "openai_video",
            "image_to_image_strategy": "image_edits",
            "image_to_video_strategy": "openai_video",
            "prompt_enhance_enabled": False,
            "prompt_enhance_show_prompt": True,
            "image_edit_plan_enabled": False,
        },
        "compatibility": {"mode": "legacy"},
        "access_control": {},
        "image_reference": {
            "enable_previous_image": "true",
            "previous_image_ttl": 1800,
        },
        "media": {
            "timeout": 30,
            "multi_media_send_mode": "sequential",
            "multi_media_send_interval": 0,
        },
    }

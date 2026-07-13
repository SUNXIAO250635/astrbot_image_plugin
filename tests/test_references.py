from __future__ import annotations

import asyncio

import astrbot.api.message_components as Comp

from imagegen_core.references import ReferenceResolver
from tests.fakes.runtime import FakeEvent, image_data_uri


class Reply:
    def __init__(self, message_id):
        self.id = message_id


class Forward:
    def __init__(self, nodes):
        self.nodes = nodes


class File:
    def __init__(self, url, name):
        self.url = url
        self.name = name


class FileId:
    def __init__(self, file_id, busid, name):
        self.file_id = file_id
        self.busid = busid
        self.name = name


class At:
    def __init__(self, target):
        self.target = target


class FakeBot:
    async def get_msg(self, message_id):
        assert message_id == "reply-1"
        return {
            "message": [
                {
                    "type": "image",
                    "data": {"url": "https://cdn.invalid/reply-signed"},
                }
            ]
        }

    async def get_group_file_url(self, group_id, file_id, busid):
        assert file_id == "file-1"
        assert busid == "102"
        return {"data": {"url": "https://cdn.invalid/group-signed"}}


async def _load(value, filename):
    return value.encode(), filename


def test_resolver_reads_direct_reply_forward_group_file_and_avatar_sources():
    async def scenario():
        event = FakeEvent(
            [
                Comp.Image(file=image_data_uri(b"direct")),
                Reply("reply-1"),
                Forward(
                    [
                        {
                            "message": [
                                {
                                    "type": "image",
                                    "url": "https://cdn.invalid/forward.png",
                                }
                            ]
                        }
                    ]
                ),
                File("https://cdn.invalid/group-file.png", "group-file.png"),
                FileId("file-1", "102", "remote.png"),
                At("123456"),
            ]
        )
        event.bot = FakeBot()
        resolver = ReferenceResolver(_load)

        assets = await resolver.resolve(event, "使用 @ 对象头像", max_images=10)

        sources = [asset.source for asset in assets]
        values = [asset.value for asset in assets]
        assert "current" in sources
        assert "reply" in sources
        assert "forward" in sources
        assert "avatar" in sources
        assert "https://cdn.invalid/group-file.png" in values
        assert "https://cdn.invalid/reply-signed" in values
        assert "https://cdn.invalid/group-signed" in values
        assert any("nk=123456" in value for value in values)

    asyncio.run(scenario())


def test_resolver_uses_cache_only_when_no_message_reference_exists():
    async def scenario():
        resolver = ReferenceResolver(_load)

        async def cached():
            return [
                {
                    "ref": "https://cdn.invalid/cached.png",
                    "filename": "cached.png",
                }
            ]

        cached_assets = await resolver.resolve(
            FakeEvent(), "上一张图", cached_loader=cached, allow_cached=True
        )
        direct_assets = await resolver.resolve(
            FakeEvent([Comp.Image(file=image_data_uri(b"direct"))]),
            "上一张图",
            cached_loader=cached,
            allow_cached=True,
        )

        assert cached_assets[0].source == "cache"
        assert direct_assets[0].source == "current"
        assert all(asset.source != "cache" for asset in direct_assets)

    asyncio.run(scenario())

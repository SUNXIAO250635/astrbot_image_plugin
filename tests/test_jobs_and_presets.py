from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

import main
from imagegen_core.jobs import JobManager
from imagegen_core.models import (
    Capability,
    GenerationHandle,
    GenerationResult,
    MediaArtifact,
)
from tests.fakes.runtime import (
    FakeContext,
    FakeEvent,
    collect_async_generator,
    image_data_uri,
    plugin_config,
)


class KVOwner:
    def __init__(self):
        self.values = {}

    async def get_kv_data(self, key, default=None):
        return self.values.get(key, default)

    async def put_kv_data(self, key, value):
        self.values[key] = value

    async def delete_kv_data(self, key):
        self.values.pop(key, None)


def test_job_manager_moves_slow_operation_to_background_and_persists_handle():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=0.01)
        completed = asyncio.Event()
        delivered = []

        async def operation(on_handle):
            await on_handle(
                GenerationHandle(
                    provider_id="video",
                    remote_task_id="task-1",
                    capability=Capability.TEXT_TO_VIDEO,
                    accepted_at=1,
                )
            )
            await asyncio.sleep(0.03)
            return "done"

        async def on_complete(job_id, result, error, metadata):
            delivered.append((job_id, result, error, metadata))
            completed.set()

        run = await manager.run(operation, on_complete, {"task_name": "视频"})
        assert run.background is True
        state = owner.values[f"imagegen_jobs:{run.job_id}"]
        assert state["handle"]["remote_task_id"] == "task-1"
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert delivered[0][1] == "done"
        assert owner.values[f"imagegen_jobs:{run.job_id}"]["status"] == "delivered"

    asyncio.run(scenario())


def test_job_manager_restores_persisted_remote_handle_once():
    async def scenario():
        owner = KVOwner()
        handle = GenerationHandle(
            provider_id="video",
            remote_task_id="task-restore",
            capability=Capability.TEXT_TO_VIDEO,
            accepted_at=1,
        )
        owner.values[JobManager.INDEX_KEY] = ["job-restore"]
        owner.values["imagegen_jobs:job-restore"] = {
            "schema_version": 1,
            "status": "running",
            "handle": handle.to_dict(),
            "task_name": "文生视频",
        }
        manager = JobManager(owner, foreground_wait_seconds=1)
        resumed = []
        completed = asyncio.Event()

        async def resume(received):
            resumed.append(received)
            return "restored"

        async def on_complete(job_id, result, error, metadata):
            assert job_id == "job-restore"
            assert result == "restored"
            assert error is None
            completed.set()

        await manager.restore(resume, on_complete)
        await manager.restore(resume, on_complete)
        await asyncio.wait_for(completed.wait(), timeout=1)

        assert resumed == [handle]
        assert owner.values["imagegen_jobs:job-restore"]["status"] == "delivered"

    asyncio.run(scenario())


def test_figurine_preset_uses_image_to_image_and_preset_size():
    async def scenario():
        config = plugin_config()
        config["jobs"] = {"enabled": False}
        config["generation_options"]["intent_plan_enabled"] = False
        plugin = main.ImageGenPlugin(FakeContext(), config)
        event = FakeEvent([Comp.Image(file=image_data_uri(b"figure"))])
        generate = AsyncMock(
            return_value=GenerationResult(
                provider_id="test",
                media=[MediaArtifact("image", "https://cdn.invalid/figure.png")],
            )
        )
        plugin._generation_service.generate = generate

        results = await collect_async_generator(
            plugin.preset_figurine(event, "做成桌面手办")
        )

        request = generate.await_args.args[0]
        assert request.capability == Capability.IMAGE_TO_IMAGE
        assert request.size == "1024x1024"
        assert request.references[0].data == b"figure"
        assert results[-1].kind == "image"

    asyncio.run(scenario())


def test_plain_text_generation_does_not_implicitly_reuse_cached_image():
    async def scenario():
        config = plugin_config()
        config["jobs"] = {"enabled": False}
        config["generation_options"]["intent_plan_enabled"] = False
        plugin = main.ImageGenPlugin(FakeContext(), config)
        event = FakeEvent()
        plugin._remember_last_image(event, image_data_uri(b"cached"), "cached.png")
        generate = AsyncMock(
            return_value=GenerationResult(
                provider_id="test",
                media=[MediaArtifact("image", "https://cdn.invalid/cat.png")],
            )
        )
        plugin._generation_service.generate = generate

        await collect_async_generator(
            plugin.generate_media_tool(event, "画一只猫")
        )

        request = generate.await_args.args[0]
        assert request.capability == Capability.TEXT_TO_IMAGE
        assert request.references == []

    asyncio.run(scenario())


def test_llm_tool_uses_ai_intent_plan_for_text_to_video():
    async def scenario():
        config = plugin_config()
        config["jobs"] = {"enabled": False}
        config["compatibility"] = {"mode": "router"}
        config["generation_options"]["intent_plan_enabled"] = True
        config["generation_options"]["prompt_enhance_enabled"] = False
        config["adapter_prompt_chat"] = {
            "base_url": "https://chat.invalid",
            "model": "planner",
        }
        plugin = main.ImageGenPlugin(FakeContext(), config)
        event = FakeEvent()
        chat = AsyncMock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "capability": "text_to_video",
                                    "preset": "",
                                    "count": 1,
                                    "prompt": "火车穿越雪山",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        generate = AsyncMock(
            return_value=GenerationResult(
                provider_id="video",
                media=[MediaArtifact("video", "https://cdn.invalid/train.mp4")],
            )
        )
        plugin._generation_service.generate = generate

        with patch.object(main.adapters, "openai_chat", chat):
            results = await collect_async_generator(
                plugin.generate_media_tool(event, "生成火车穿越雪山的动态画面")
            )

        request = generate.await_args.args[0]
        assert request.capability == Capability.TEXT_TO_VIDEO
        assert request.prompt == "火车穿越雪山"
        assert results[-1].kind == "chain"
        assert isinstance(results[-1].value[0], Comp.Video)

    asyncio.run(scenario())


def test_routed_generation_sends_result_after_foreground_timeout():
    async def scenario():
        config = plugin_config()
        config["compatibility"] = {"mode": "router"}
        config["jobs"] = {"enabled": True, "foreground_wait_seconds": 0.01}
        context = FakeContext()
        plugin = main.ImageGenPlugin(context, config)
        event = FakeEvent()

        async def slow_generate(request, on_handle=None):
            await asyncio.sleep(0.04)
            return GenerationResult(
                provider_id="image",
                media=[MediaArtifact("image", "https://cdn.invalid/later.png")],
            )

        plugin._generation_service.generate = slow_generate

        result = await plugin._do_routed_generation(
            event,
            Capability.TEXT_TO_IMAGE,
            "cat",
            "文生图",
        )
        assert result.kind == "plain"
        assert "任务 ID" in result.value
        await asyncio.sleep(0.08)
        assert len(context.sent) == 1
        _, chain = context.sent[0]
        assert isinstance(chain[0], Comp.Image)
        assert chain[0].url.endswith("later.png")

    asyncio.run(scenario())

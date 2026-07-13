from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp
import pytest

import main
from imagegen_core.jobs import JobManager
from imagegen_core.models import (
    CallerContext,
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


def test_job_manager_cleans_failed_foreground_task_and_persisted_state():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=1)

        async def operation(on_handle):
            await on_handle(
                GenerationHandle(
                    provider_id="image",
                    remote_task_id="task-failed",
                    capability=Capability.TEXT_TO_IMAGE,
                    accepted_at=1,
                )
            )
            raise RuntimeError("generation failed")

        with pytest.raises(RuntimeError, match="generation failed"):
            await manager.run(operation, AsyncMock(), {"task_name": "image"})

        assert manager._tasks == {}
        assert owner.values.get(JobManager.INDEX_KEY) == []
        assert not any(key.startswith("imagegen_jobs:job-") for key in owner.values)

    asyncio.run(scenario())


def test_job_manager_marks_failed_when_background_callback_raises():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=0.01)

        async def operation(on_handle):
            await asyncio.sleep(0.03)
            return "done"

        callback_calls = []

        async def on_complete(job_id, result, error, metadata):
            callback_calls.append((result, error))
            raise RuntimeError("delivery failed")

        run = await manager.run(operation, on_complete, {"task_name": "image"})
        await asyncio.sleep(0.08)

        state = owner.values[f"imagegen_jobs:{run.job_id}"]
        assert state["status"] == "failed"
        assert state["error"] == "RuntimeError: delivery failed"
        assert callback_calls == [("done", None)]

    asyncio.run(scenario())


def test_job_manager_clears_background_operation_after_failure():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=0.01)
        completed = asyncio.Event()

        async def operation(on_handle):
            await asyncio.sleep(0.03)
            raise RuntimeError("generation failed")

        async def on_complete(job_id, result, error, metadata):
            assert isinstance(error, RuntimeError)
            completed.set()

        run = await manager.run(operation, on_complete, {"task_name": "image"})
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.sleep(0)

        assert manager._tasks == {}
        assert owner.values[f"imagegen_jobs:{run.job_id}"]["status"] == "failed"

    asyncio.run(scenario())


def test_job_manager_disabled_mode_removes_handle_state_after_completion():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, enabled=False)

        async def operation(on_handle):
            await on_handle(
                GenerationHandle(
                    provider_id="video",
                    remote_task_id="task-disabled",
                    capability=Capability.TEXT_TO_VIDEO,
                    accepted_at=1,
                )
            )
            return "done"

        run = await manager.run(operation, AsyncMock(), {"task_name": "video"})

        assert run.result == "done"
        assert owner.values.get(JobManager.INDEX_KEY) == []
        assert not any(key.startswith("imagegen_jobs:job-") for key in owner.values)

    asyncio.run(scenario())


def test_job_manager_removes_expired_terminal_jobs_on_restore():
    async def scenario():
        owner = KVOwner()
        owner.values[JobManager.INDEX_KEY] = ["old", "running"]
        owner.values["imagegen_jobs:old"] = {
            "status": "delivered",
            "updated_at": time.time() - 120,
        }
        owner.values["imagegen_jobs:running"] = {
            "status": "running",
            "updated_at": time.time() - 120,
        }
        manager = JobManager(owner, terminal_retention_seconds=60)

        await manager.restore(AsyncMock(), AsyncMock())

        assert "imagegen_jobs:old" not in owner.values
        assert owner.values[JobManager.INDEX_KEY] == ["running"]

    asyncio.run(scenario())


def test_job_manager_cleans_terminal_jobs_when_remote_restore_is_disabled():
    async def scenario():
        owner = KVOwner()
        handle = GenerationHandle(
            provider_id="video",
            remote_task_id="task-keep",
            capability=Capability.TEXT_TO_VIDEO,
            accepted_at=1,
        )
        owner.values[JobManager.INDEX_KEY] = ["old", "running"]
        owner.values["imagegen_jobs:old"] = {
            "status": "failed",
            "updated_at": time.time() - 120,
        }
        owner.values["imagegen_jobs:running"] = {
            "status": "running",
            "handle": handle.to_dict(),
        }
        manager = JobManager(owner, terminal_retention_seconds=60)
        resume = AsyncMock()

        await manager.restore(resume, AsyncMock(), resume_enabled=False)

        resume.assert_not_awaited()
        assert owner.values[JobManager.INDEX_KEY] == ["running"]
        state = owner.values["imagegen_jobs:running"]
        assert state["status"] == "failed"
        assert state["error"] == "UnrecoverableJob: remote task restore disabled"

    asyncio.run(scenario())


def test_job_manager_marks_running_job_without_handle_as_failed():
    async def scenario():
        owner = KVOwner()
        owner.values[JobManager.INDEX_KEY] = ["local-job"]
        owner.values["imagegen_jobs:local-job"] = {
            "status": "running",
            "task_name": "image",
        }
        manager = JobManager(owner)

        await manager.restore(AsyncMock(), AsyncMock())

        state = owner.values["imagegen_jobs:local-job"]
        assert state["status"] == "failed"
        assert state["error"] == "UnrecoverableJob: missing remote handle"

    asyncio.run(scenario())


def test_job_manager_index_eviction_deletes_evicted_job_kv():
    async def scenario():
        owner = KVOwner()
        ids = [f"job-{index}" for index in range(200)]
        owner.values[JobManager.INDEX_KEY] = ids.copy()
        owner.values["imagegen_jobs:job-0"] = {"status": "delivered"}
        manager = JobManager(owner)

        await manager._save_job("job-new", {"status": "running"})

        assert "job-0" not in owner.values[JobManager.INDEX_KEY]
        assert "imagegen_jobs:job-0" not in owner.values

    asyncio.run(scenario())


def test_job_manager_index_eviction_removes_orphaned_entries():
    async def scenario():
        owner = KVOwner()
        ids = [f"job-{index}" for index in range(200)]
        owner.values[JobManager.INDEX_KEY] = ids.copy()
        for job_id in ids[1:]:
            owner.values[f"imagegen_jobs:{job_id}"] = {"status": "running"}
        manager = JobManager(owner)

        await manager._save_job("job-new", {"status": "running"})

        assert "job-0" not in owner.values[JobManager.INDEX_KEY]
        assert len(owner.values[JobManager.INDEX_KEY]) == 200

    asyncio.run(scenario())


def test_job_manager_does_not_evict_running_jobs_when_index_is_full():
    async def scenario():
        owner = KVOwner()
        ids = [f"job-{index}" for index in range(200)]
        owner.values[JobManager.INDEX_KEY] = ids.copy()
        for job_id in ids:
            owner.values[f"imagegen_jobs:{job_id}"] = {"status": "running"}
        manager = JobManager(owner)

        await manager._save_job("job-new", {"status": "running"})

        assert len(owner.values[JobManager.INDEX_KEY]) == 201
        assert "job-0" in owner.values[JobManager.INDEX_KEY]
        assert "imagegen_jobs:job-0" in owner.values

    asyncio.run(scenario())


def test_job_manager_terminate_waits_for_active_delivery_watcher():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=0.01)
        delivery_finished = asyncio.Event()

        async def operation(on_handle):
            await asyncio.sleep(0.02)
            return "done"

        async def on_complete(job_id, result, error, metadata):
            await asyncio.sleep(0.04)
            delivery_finished.set()

        await manager.run(operation, on_complete, {"task_name": "image"})
        await asyncio.sleep(0.03)
        await manager.terminate()

        assert delivery_finished.is_set()
        assert manager._watchers == set()

    asyncio.run(scenario())


def test_job_manager_persists_partial_delivery_status():
    async def scenario():
        owner = KVOwner()
        manager = JobManager(owner, foreground_wait_seconds=0.01)

        async def operation(on_handle):
            await asyncio.sleep(0.03)
            return "done"

        async def on_complete(job_id, result, error, metadata):
            return {
                "status": "delivered_with_errors",
                "delivery_errors": 1,
                "delivered_count": 2,
            }

        run = await manager.run(operation, on_complete, {"task_name": "image"})
        await asyncio.sleep(0.08)

        state = owner.values[f"imagegen_jobs:{run.job_id}"]
        assert state["status"] == "delivered_with_errors"
        assert state["delivery_errors"] == 1
        assert state["delivered_count"] == 2

    asyncio.run(scenario())


def test_job_manager_logs_kv_failures(caplog):
    class BrokenOwner:
        async def get_kv_data(self, key, default=None):
            raise RuntimeError("read failed")

        async def put_kv_data(self, key, value):
            raise RuntimeError("write failed")

        async def delete_kv_data(self, key):
            raise RuntimeError("delete failed")

    async def scenario():
        manager = JobManager(BrokenOwner())
        await manager._get("read-key", None)
        await manager._put("write-key", {})
        await manager._delete("delete-key")

    with caplog.at_level("WARNING", logger="imagegen_core.jobs"):
        asyncio.run(scenario())

    assert "Failed to read plugin KV key read-key" in caplog.text
    assert "Failed to write plugin KV key write-key" in caplog.text
    assert "Failed to delete plugin KV key delete-key" in caplog.text


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

        await collect_async_generator(plugin.generate_media_tool(event, "画一只猫"))

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


def test_natural_generation_reuses_intent_optimization_decision():
    async def scenario():
        config = plugin_config()
        config["jobs"] = {"enabled": False}
        config["compatibility"] = {"mode": "router"}
        config["generation_options"]["intent_plan_enabled"] = True
        config["generation_options"]["prompt_enhance_enabled"] = True
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
                                    "capability": "text_to_image",
                                    "preset": "",
                                    "count": 1,
                                    "should_optimize": False,
                                    "prompt": "完整且明确的猫咪摄影提示词",
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
                provider_id="image",
                media=[MediaArtifact("image", "https://cdn.invalid/cat.png")],
            )
        )
        plugin._generation_service.generate = generate

        with patch.object(main.adapters, "openai_chat", chat):
            await collect_async_generator(
                plugin.generate_media_tool(event, "完整且明确的猫咪摄影提示词")
            )

        assert chat.await_count == 1
        assert generate.await_args.args[0].prompt == "完整且明确的猫咪摄影提示词"

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


def test_background_delivery_retries_and_reports_partial_success():
    class FlakyContext(FakeContext):
        def __init__(self):
            super().__init__()
            self.calls = {}

        async def send_message(self, unified_msg_origin, chain):
            component = chain[0]
            value = getattr(component, "url", "") or getattr(component, "file", "")
            self.calls[value] = self.calls.get(value, 0) + 1
            if value.endswith("always-fails.png"):
                raise RuntimeError("send failed")
            if value.endswith("retry.png") and self.calls[value] == 1:
                raise RuntimeError("transient")
            await super().send_message(unified_msg_origin, chain)

    async def scenario():
        config = plugin_config()
        config["jobs"] = {
            "delivery_retry_count": 1,
            "delivery_retry_delay_seconds": 0,
        }
        context = FlakyContext()
        plugin = main.ImageGenPlugin(context, config)
        caller = CallerContext(
            unified_msg_origin="test:private:user-1", sender_id="user-1"
        )
        result = GenerationResult(
            provider_id="image",
            media=[
                MediaArtifact("image", "https://cdn.invalid/retry.png"),
                MediaArtifact("image", "https://cdn.invalid/always-fails.png"),
            ],
        )

        delivery = await plugin._background_job_completed(
            "job-delivery",
            result,
            None,
            {"task_name": "image", "caller": caller.to_dict()},
        )

        assert context.calls["https://cdn.invalid/retry.png"] == 2
        assert context.calls["https://cdn.invalid/always-fails.png"] == 2
        assert delivery == {
            "status": "delivered_with_errors",
            "delivery_errors": 1,
            "delivered_count": 1,
        }

    asyncio.run(scenario())


def test_background_delivery_without_active_send_context_fails():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        result = GenerationResult(
            provider_id="image",
            media=[MediaArtifact("image", "https://cdn.invalid/image.png")],
        )

        with pytest.raises(RuntimeError, match="主动发送"):
            await plugin._background_job_completed(
                "job-no-origin",
                result,
                None,
                {"task_name": "image", "caller": {}},
            )

    asyncio.run(scenario())

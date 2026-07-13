from __future__ import annotations

from collections.abc import Awaitable, Callable

try:
    from .. import adapters
except ImportError:
    import adapters


class LegacyAdapterRunner:
    def __init__(
        self,
        config_getter: Callable[[str], dict],
        count_config: Callable[[dict, int | None], dict],
        complete_count: Callable[..., Awaitable[dict]],
        timeout_getter: Callable[[], int],
        proxy_getter: Callable[[], str],
    ):
        self._config = config_getter
        self._count_config = count_config
        self._complete_count = complete_count
        self._timeout = timeout_getter
        self._proxy = proxy_getter

    async def text_to_image(self, prompt: str, output_count=None) -> dict:
        base_cfg = self._config("adapter_image_generation")
        response = await adapters.image_generation(
            self._count_config(base_cfg, output_count),
            prompt,
            self._timeout(),
            proxy=self._proxy(),
        )
        return await self._complete_count(
            response,
            output_count,
            lambda count: adapters.image_generation(
                self._count_config(base_cfg, count),
                prompt,
                self._timeout(),
                proxy=self._proxy(),
            ),
            "文生图",
        )

    async def image_to_image(
        self,
        prompt: str,
        image_bytes,
        image_names,
        strategy: str,
        output_count=None,
    ) -> dict:
        config_key = (
            "adapter_image_generation"
            if strategy == "image_generation"
            else "adapter_image_edits"
        )
        base_cfg = self._config(config_key)

        async def invoke(count):
            cfg = self._count_config(base_cfg, count)
            if strategy == "image_generation":
                return await adapters.image_generation(
                    cfg,
                    prompt,
                    self._timeout(),
                    image_bytes,
                    image_names,
                    self._proxy(),
                )
            return await adapters.image_edits(
                cfg,
                prompt,
                image_bytes,
                image_names,
                self._timeout(),
                self._proxy(),
            )

        response = await invoke(output_count)
        return await self._complete_count(
            response,
            output_count,
            lambda count: invoke(count),
            "图生图",
        )

    async def text_to_video(self, prompt: str, strategy: str) -> dict:
        if strategy == "openai_chat":
            return await adapters.openai_chat(
                self._config("adapter_openai_chat"),
                prompt,
                timeout=self._timeout(),
                proxy=self._proxy(),
            )
        return await adapters.openai_video(
            self._config("adapter_openai_video"),
            prompt,
            None,
            None,
            self._timeout(),
            self._proxy(),
        )

    async def image_to_video(
        self, prompt: str, image_bytes, image_name: str, strategy: str
    ) -> dict:
        if strategy == "image_edits":
            return await adapters.image_edits(
                self._config("adapter_image_edits"),
                prompt,
                image_bytes,
                image_name,
                self._timeout(),
                self._proxy(),
            )
        return await adapters.openai_video(
            self._config("adapter_openai_video"),
            prompt,
            image_bytes,
            image_name,
            self._timeout(),
            self._proxy(),
        )

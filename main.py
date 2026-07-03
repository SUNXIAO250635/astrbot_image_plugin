"""astrbot_plugin_imagegen main.py

通过指令调用文生图、图生图、文生视频、图生视频。
适配四种 OpenAI 兼容接口。
"""
from __future__ import annotations

import os
import time
import secrets

import aiohttp

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

import adapters
from media import extract_media, download_to_file


@register("astrbot_plugin_imagegen", "sunx", "多模态生图视频插件", "0.1.0",
          repo="https://github.com/SUNXIAO250635/astrbot_image_plugin")
class ImageGenPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

    # ------------------------------------------------------------------ #
    # 指令组 /画
    # ------------------------------------------------------------------ #
    @filter.command_group("画", alias={"image", "img"})
    def image_group(self):
        """多模态生成指令组：文生图 / 图生图 / 文生视频 / 图生视频"""
        pass

    @image_group.command("help", alias={"帮助"})
    async def help_(self, event: AstrMessageEvent):
        '''显示帮助'''
        yield event.plain_result(
            "🖼️ 画图/视频插件\n"
            "/画 文 <提示词>          文生图(image-generation)\n"
            "/画 图 <提示词>          图生图(image-edits，需附带图片)\n"
            "/画 视频 <提示词>        文生视频\n"
            "/画 图生视频 <提示词>    图生视频(需附带图片)\n"
            "别名：文生图/文生视频"
        )

    @image_group.command("文", alias={"文生图", "txt2img"})
    async def text_to_image(self, event: AstrMessageEvent, prompt: str = ""):
        '''文生图，使用 image-generation 接口'''
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 文 一只猫")
            event.stop_event()
            return
        yield await self._do_text_to_image(event, prompt)

    @image_group.command("图", alias={"图生图", "img2img"})
    async def image_to_image(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生图，使用 image-edits 接口，需附带图片'''
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 图 改成水彩")
            event.stop_event()
            return
        img_bytes, img_name = await self._get_first_image_bytes(event)
        if not img_bytes:
            yield event.plain_result("❌ 图生图需要附带一张图片。请在同一消息里发送图片+指令。")
            event.stop_event()
            return
        yield await self._do_image_to_image(event, prompt, img_bytes, img_name)

    @image_group.command("视频", alias={"文生视频", "txt2video"})
    async def text_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''文生视频'''
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 视频 火车穿越雪山")
            event.stop_event()
            return
        strategy = self.config.get("video_via_strategy", "openai_video")
        yield await self._do_text_to_video(event, prompt, strategy)

    @image_group.command("图生视频", alias={"img2video"})
    async def image_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生视频，需附带图片'''
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 图生视频 让画面动起来")
            event.stop_event()
            return
        img_bytes, img_name = await self._get_first_image_bytes(event)
        if not img_bytes:
            yield event.plain_result("❌ 图生视频需要附带一张图片。")
            event.stop_event()
            return
        strategy = self.config.get("image_to_video_strategy", "openai_video")
        yield await self._do_image_to_video(event, prompt, img_bytes, img_name, strategy)

    # ------------------------------------------------------------------ #
    # 实现
    # ------------------------------------------------------------------ #
    @property
    def _save_dir(self) -> str:
        media_cfg = self.config.get("media", {}) or {}
        subdir = media_cfg.get("save_dir", "imagegen") or "imagegen"
        # AstrBot 把 data 目录作为工作区根，相对路径基于运行目录
        data_root = os.path.join("data", subdir)
        os.makedirs(data_root, exist_ok=True)
        return data_root

    @property
    def _timeout(self) -> int:
        return int((self.config.get("media", {}) or {}).get("timeout", 180) or 180)

    @property
    def _proxy(self) -> str:
        return (self.config.get("media", {}) or {}).get("proxy", "") or ""

    def _cfg(self, name: str) -> dict:
        return self.config.get(name, {}) or {}

    def _stem(self, event: AstrMessageEvent) -> str:
        """生成本次任务用的文件名前缀(基于会话+时长避免冲突)。"""
        sid = event.get_sender_id() or "u"
        return f"{sid}_{int(time.time())}_{secrets.token_hex(4)}"

    async def _get_first_image_bytes(
        self, event: AstrMessageEvent
    ) -> tuple:
        """从入站消息链里取第一张图片，返回 (bytes, filename)。"""
        chain = event.message_obj.message or []
        for comp in chain:
            if isinstance(comp, Comp.Image):
                url = None
                # Comp.Image 常见字段：file / url / path
                url = getattr(comp, "url", None) or getattr(comp, "file", None) \
                    or getattr(comp, "path", None)
                if not url:
                    continue
                if url.startswith("http"):
                    try:
                        timeout_cfg = aiohttp.ClientTimeout(total=self._timeout)
                        proxy_kw = {"proxy": self._proxy} if self._proxy else {}
                        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                            async with session.get(url, **proxy_kw) as resp:
                                if resp.status != 200:
                                    continue
                                data = await resp.read()
                                return data, "input.png"
                    except Exception as e:
                        logger.warning(f"下载入站图片失败: {e}")
                        continue
                elif os.path.exists(url):
                    with open(url, "rb") as f:
                        return f.read(), os.path.basename(url) or "input.png"
        return None, None

    # ---- 文生图 ----
    async def _do_text_to_image(self, event, prompt):
        cfg = self._cfg("adapter_image_generation")
        try:
            resp = await adapters.image_generation(cfg, prompt, self._timeout)
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "文生图")

    # ---- 图生图 ----
    async def _do_image_to_image(self, event, prompt, img_bytes, img_name):
        cfg = self._cfg("adapter_image_edits")
        try:
            resp = await adapters.image_edits(
                cfg, prompt, img_bytes, img_name, self._timeout, self._proxy
            )
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "图生图")

    # ---- 文生视频 ----
    async def _do_text_to_video(self, event, prompt, strategy):
        if strategy == "openai_chat":
            cfg = self._cfg("adapter_openai_chat")
            try:
                resp = await adapters.openai_chat(cfg, prompt, None, self._timeout)
            except adapters.ApiException as e:
                return event.plain_result(f"❌ {e}")
            return await self._send_result(event, resp, "文生视频", expect="video")
        cfg = self._cfg("adapter_openai_video")
        try:
            resp = await adapters.openai_video(cfg, prompt, None, None,
                                              self._timeout, self._proxy)
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "文生视频", expect="video")

    # ---- 图生视频 ----
    async def _do_image_to_video(self, event, prompt, img_bytes, img_name, strategy):
        if strategy == "image_edits":
            cfg = self._cfg("adapter_image_edits")
            try:
                resp = await adapters.image_edits(
                    cfg, prompt, img_bytes, img_name, self._timeout, self._proxy
                )
            except adapters.ApiException as e:
                return event.plain_result(f"❌ {e}")
            return await self._send_result(event, resp, "图生视频", expect="video")
        cfg = self._cfg("adapter_openai_video")
        try:
            resp = await adapters.openai_video(cfg, prompt, img_bytes, img_name,
                                              self._timeout, self._proxy)
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "图生视频", expect="video")

    # ---- 通用：解析并发送媒体 ----
    async def _send_result(self, event, resp, task_name, expect=None):
        media = extract_media(resp)
        if not media:
            logger.warning(f"{task_name} 响应未提取到媒体: {resp}")
            return event.plain_result(f"❌ {task_name}成功但响应中未找到图片/视频。")
        kind, value = media
        if expect == "video" and kind != "video":
            # 当期望视频但拿到图片URL时，仍按图片发送（兜底）
            logger.info(f"{task_name} 期望视频但接口返回图片，按图片发送。")
        stem = self._stem(event)
        try:
            kind2, path = await download_to_file(
                value, self._save_dir, stem, self._timeout, self._proxy
            )
        except Exception as e:
            return event.plain_result(f"❌ 媒体下载失败: {e}")
        kind = kind2 or kind
        if kind == "video":
            return event.chain_result([Comp.Video.fromFileSystem(path=path)])
        else:
            return event.image_result(path)

    async def terminate(self):
        pass

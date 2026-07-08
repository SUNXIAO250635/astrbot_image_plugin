"""astrbot_plugin_imagegen main.py

通过指令调用文生图、图生图、文生视频、图生视频。
适配四种 OpenAI 兼容接口。
"""
from __future__ import annotations

import base64
import os
import re
import time
import secrets
from urllib.parse import unquote, urlparse

import aiohttp

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

try:
    from . import adapters
    from .media import extract_media, download_to_file
except ImportError:
    import adapters
    from media import extract_media, download_to_file


@register("astrbot_plugin_imagegen", "sunx", "多模态生图视频插件", "0.1.0",
          repo="https://github.com/SUNXIAO250635/astrbot_image_plugin")
class ImageGenPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._last_image_cache = {}

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
            "/画 图 <提示词>          图生图(image-edits，可复用上一张图)\n"
            "/画 视频 <提示词>        文生视频\n"
            "/画 图生视频 <提示词>    图生视频(可复用上一张图)\n"
            "别名：文生图/文生视频"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def remember_incoming_image(self, event: AstrMessageEvent):
        '''记录用户最近发送的图片，供后续图生图/图生视频复用'''
        if not self._previous_image_enabled:
            return
        image_ref = self._extract_first_image_ref(event)
        if image_ref:
            self._remember_last_image(
                event, image_ref[0], image_ref[1], source="incoming"
            )

    @image_group.command("文", alias={"文生图", "txt2img"})
    async def text_to_image(self, event: AstrMessageEvent, prompt: str = ""):
        '''文生图，使用 image-generation 接口'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 文 一只猫")
            event.stop_event()
            return
        yield await self._do_text_to_image(event, prompt)

    @image_group.command("图", alias={"图生图", "img2img"})
    async def image_to_image(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生图，使用 image-edits 接口，需附带图片'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 图 改成水彩")
            event.stop_event()
            return
        img_bytes, img_name = await self._get_first_image_bytes(event)
        if not img_bytes:
            yield event.plain_result(
                "❌ 图生图需要一张图片。请在同一消息里附带图片，"
                "或先发送/生成一张图片后再使用本指令。"
            )
            event.stop_event()
            return
        strategy = self.config.get("image_to_image_strategy", "image_edits")
        yield await self._do_image_to_image(event, prompt, img_bytes, img_name, strategy)

    @image_group.command("视频", alias={"文生视频", "txt2video"})
    async def text_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''文生视频'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 视频 火车穿越雪山")
            event.stop_event()
            return
        strategy = self.config.get("video_via_strategy", "openai_video")
        yield await self._do_text_to_video(event, prompt, strategy)

    @image_group.command("图生视频", alias={"img2video"})
    async def image_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生视频，需附带图片'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 图生视频 让画面动起来")
            event.stop_event()
            return
        img_bytes, img_name = await self._get_first_image_bytes(event)
        if not img_bytes:
            yield event.plain_result(
                "❌ 图生视频需要一张图片。请在同一消息里附带图片，"
                "或先发送/生成一张图片后再使用本指令。"
            )
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

    @property
    def _previous_image_enabled(self) -> bool:
        cfg = self._cfg("image_reference")
        return self._cfg_bool(cfg.get("enable_previous_image", "true"), True)

    @property
    def _previous_image_ttl(self) -> int:
        cfg = self._cfg("image_reference")
        try:
            return max(0, int(cfg.get("previous_image_ttl", 1800)))
        except (TypeError, ValueError):
            return 1800

    def _cfg(self, name: str) -> dict:
        return self.config.get(name, {}) or {}

    @staticmethod
    def _cfg_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "开启", "启用"}

    def _access_denied_result(self, event: AstrMessageEvent):
        """检查用户/群聊白名单；白名单为空时默认不限制。"""
        access_cfg = self._cfg("access_control")
        user_whitelist = self._split_id_list(access_cfg.get("user_whitelist", ""))
        group_whitelist = self._split_id_list(access_cfg.get("group_whitelist", ""))

        sender_id = self._event_sender_id(event)
        group_id = self._event_group_id(event)

        if user_whitelist and sender_id not in user_whitelist:
            logger.info(f"拒绝非白名单用户使用生图插件: user={sender_id}")
            return event.plain_result(
                access_cfg.get("deny_message")
                or "❌ 你没有权限使用画图/视频插件。"
            )

        if group_whitelist and group_id and group_id not in group_whitelist:
            logger.info(f"拒绝非白名单群聊使用生图插件: group={group_id}")
            return event.plain_result(
                access_cfg.get("deny_message")
                or "❌ 当前群聊没有权限使用画图/视频插件。"
            )

        return None

    @staticmethod
    def _split_id_list(value) -> set[str]:
        if not value:
            return set()
        if isinstance(value, (list, tuple, set)):
            parts = value
        else:
            parts = re.split(r"[\s,，;；]+", str(value))
        return {str(item).strip() for item in parts if str(item).strip()}

    @staticmethod
    def _event_sender_id(event: AstrMessageEvent) -> str:
        try:
            sender_id = event.get_sender_id()
        except Exception:
            sender_id = ""
        if not sender_id:
            sender_id = getattr(event, "sender_id", "") or getattr(
                getattr(event, "message_obj", None), "sender_id", ""
            )
        return str(sender_id).strip()

    @staticmethod
    def _event_group_id(event: AstrMessageEvent) -> str:
        try:
            group_id = event.get_group_id()
        except Exception:
            group_id = ""
        if group_id:
            return str(group_id).strip()

        message_obj = getattr(event, "message_obj", None)
        for obj in (event, message_obj):
            if not obj:
                continue
            for attr in ("group_id", "groupid", "group"):
                value = getattr(obj, attr, "")
                if value:
                    return str(value).strip()
        return ""

    def _stem(self, event: AstrMessageEvent) -> str:
        """生成本次任务用的文件名前缀(基于会话+时长避免冲突)。"""
        sid = event.get_sender_id() or "u"
        return f"{sid}_{int(time.time())}_{secrets.token_hex(4)}"

    def _cache_key(self, event: AstrMessageEvent) -> str:
        sender_id = self._event_sender_id(event) or "unknown"
        origin = getattr(event, "unified_msg_origin", "") or ""
        if not origin:
            group_id = self._event_group_id(event)
            origin = f"group:{group_id}" if group_id else f"user:{sender_id}"
        return f"{origin}:{sender_id}"

    def _remember_last_image(
        self,
        event: AstrMessageEvent,
        value: str,
        filename: str = "input.png",
        source: str = "",
    ) -> None:
        if not self._previous_image_enabled or not value:
            return
        self._last_image_cache[self._cache_key(event)] = {
            "value": value,
            "filename": filename or "input.png",
            "source": source,
            "time": time.time(),
        }
        self._prune_last_image_cache()

    def _get_cached_image_ref(self, event: AstrMessageEvent) -> tuple:
        if not self._previous_image_enabled:
            return None, None
        item = self._last_image_cache.get(self._cache_key(event))
        if not item:
            return None, None
        ttl = self._previous_image_ttl
        if ttl and time.time() - float(item.get("time", 0)) > ttl:
            self._last_image_cache.pop(self._cache_key(event), None)
            return None, None
        return item.get("value"), item.get("filename") or "input.png"

    def _prune_last_image_cache(self) -> None:
        ttl = self._previous_image_ttl
        if ttl:
            expired_before = time.time() - ttl
            for key, item in list(self._last_image_cache.items()):
                if float(item.get("time", 0)) < expired_before:
                    self._last_image_cache.pop(key, None)
        max_items = 200
        if len(self._last_image_cache) <= max_items:
            return
        for key, _item in sorted(
            self._last_image_cache.items(), key=lambda kv: kv[1].get("time", 0)
        )[: len(self._last_image_cache) - max_items]:
            self._last_image_cache.pop(key, None)

    def _extract_first_image_ref(self, event: AstrMessageEvent) -> tuple:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None) or []
        for comp in chain:
            if isinstance(comp, Comp.Image):
                value = getattr(comp, "url", None) or getattr(comp, "file", None) \
                    or getattr(comp, "path", None)
                if value:
                    value = str(value)
                    return value, self._image_ref_filename(value)
        return None

    @staticmethod
    def _image_ref_filename(value: str) -> str:
        if not value or value.startswith(("base64://", "data:image/")):
            return "input.png"
        if value.startswith("http"):
            parsed = urlparse(value)
            return os.path.basename(parsed.path) or "input.png"
        if value.startswith("file://"):
            parsed = urlparse(value)
            return os.path.basename(unquote(parsed.path)) or "input.png"
        return os.path.basename(value) or "input.png"

    async def _load_image_bytes(self, value: str, filename: str = "input.png") -> tuple:
        if not value:
            return None, None
        value = str(value)
        if value.startswith("http"):
            try:
                timeout_cfg = aiohttp.ClientTimeout(total=self._timeout)
                proxy_kw = {"proxy": self._proxy} if self._proxy else {}
                async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                    async with session.get(value, **proxy_kw) as resp:
                        if resp.status != 200:
                            return None, None
                        data = await resp.read()
                        return data, filename or "input.png"
            except Exception as e:
                logger.warning(f"下载图片失败: {e}")
                return None, None

        if value.startswith("base64://"):
            try:
                data = base64.b64decode(value[len("base64://"):])
                return data, filename or "input.png"
            except Exception as e:
                logger.warning(f"解析 base64 图片失败: {e}")
                return None, None

        if value.startswith("data:image/") and ";base64," in value:
            try:
                data = base64.b64decode(value.split(";base64,", 1)[1])
                return data, filename or "input.png"
            except Exception as e:
                logger.warning(f"解析 data URI 图片失败: {e}")
                return None, None

        if value.startswith("file://"):
            parsed = urlparse(value)
            value = unquote(parsed.path or value[len("file://"):])
            if os.name == "nt" and re.match(r"^/[A-Za-z]:/", value):
                value = value[1:]

        if os.path.exists(value):
            with open(value, "rb") as f:
                return f.read(), os.path.basename(value) or filename or "input.png"

        return None, None

    async def _get_first_image_bytes(
        self, event: AstrMessageEvent
    ) -> tuple:
        """从入站消息链里取第一张图片，返回 (bytes, filename)。"""
        image_ref = self._extract_first_image_ref(event)
        if image_ref:
            data, filename = await self._load_image_bytes(*image_ref)
            if data:
                self._remember_last_image(
                    event, image_ref[0], filename or image_ref[1], source="current"
                )
                return data, filename

        cached_ref = self._get_cached_image_ref(event)
        if cached_ref[0]:
            data, filename = await self._load_image_bytes(*cached_ref)
            if data:
                return data, filename
        return None, None

    # ---- 文生图 ----
    async def _do_text_to_image(self, event, prompt):
        cfg = self._cfg("adapter_image_generation")
        try:
            resp = await adapters.image_generation(
                cfg, prompt, self._timeout, proxy=self._proxy
            )
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "文生图")

    # ---- 图生图 ----
    async def _do_image_to_image(self, event, prompt, img_bytes, img_name, strategy):
        if strategy == "image_generation":
            cfg = self._cfg("adapter_image_generation")
            try:
                resp = await adapters.image_generation(
                    cfg, prompt, self._timeout, img_bytes, img_name, self._proxy
                )
            except adapters.ApiException as e:
                return event.plain_result(f"❌ {e}")
            return await self._send_result(event, resp, "图生图")

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

        # 双容器(Docker)部署下：AstrBot 容器里的本地文件 file:// 路径
        # NapCat 容器读不到(ENOENT)。所以只要拿到的是公网 URL，就优先 fromURL 直发，
        # 让 NapCat 自己去 URL 拉流/拉图，跨容器不再依赖共享文件系统。
        if isinstance(value, str) and value.startswith("http"):
            if kind == "video":
                return event.chain_result([Comp.Video.fromURL(url=value)])
            # 图片直发 URL（同样规避 file:// 跨容器问题）
            self._remember_last_image(event, value, "generated.png", source=task_name)
            return event.image_result(value)

        # 非 URL（base64 data URI / 本地路径）：落盘后用本地路径发送。
        # 单容器部署下 file:// 可被 NapCat 读到；双容器下若仍 ENOENT，
        # 说明该渠道没返回 URL（只给 base64），此时只能靠部署层共享卷解决。
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
            self._remember_last_image(event, path, os.path.basename(path), source=task_name)
            return event.image_result(path)

    async def terminate(self):
        pass

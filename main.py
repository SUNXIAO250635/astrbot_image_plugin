"""astrbot_plugin_imagegen main.py

通过指令调用文生图、图生图、文生视频、图生视频。
适配四种 OpenAI 兼容接口。
"""
from __future__ import annotations

import base64
import asyncio
import json
import os
import re
import sys
import time
import secrets
from urllib.parse import unquote, urlparse

import aiohttp

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

LEGACY_PROMPT_ENHANCE_SYSTEM_PROMPT = (
    "你是专业的 AI 图像和视频生成提示词优化助手。"
    "请把用户的简短需求改写成适合图像/视频生成模型的高质量中文提示词。"
    "保留用户核心意图，补充主体、场景、构图、光线、风格、材质、细节和画面质量描述。"
    "不要输出解释、标题、编号、引号或 Markdown，只输出最终提示词。"
)

PREVIOUS_PROMPT_ENHANCE_SYSTEM_PROMPT = (
    "你是专业的 AI 图像和视频生成提示词优化助手。"
    "请把用户需求扩写成适合图像/视频生成模型的高质量中文提示词。"
    "必须保留用户原文中的所有主体、动作、场景、风格、限制和细节，不能摘要、不能压缩、不能删减关键条件。"
    "在不改变原意的前提下补充主体外观、场景环境、构图、镜头、光线、色彩、材质、氛围、细节和画面质量描述。"
    "输出应比原始提示词更具体、更信息密集；原文较短时通常扩写到 80-180 个中文字符，原文较长时不要明显短于原文。"
    "不要输出解释、标题、编号、引号或 Markdown，只输出最终提示词。"
)

DECISION_PROMPT_ENHANCE_SYSTEM_PROMPT = (
    "你是专业的 AI 图像和视频生成提示词决策助手。"
    "先判断用户原始提示词是否真的需要优化，再决定是否改写。"
    "如果原文已经清晰、信息密集、约束充分，包含明确构图/镜头/风格/保留/替换/不要等条件，"
    "或者用户要求按原文、不要优化、不要改写、保持提示词，则 should_optimize=false。"
    "只有原文过短、过泛、缺少画面描述，并且优化不会改变用户语义时，才 should_optimize=true。"
    "优化时必须保留所有主体、动作、场景、风格、限制和细节，不能摘要、不能压缩、不能删减关键条件。"
    "如果用户语义上要求生成多张图片，请把数量写入 image_count；没有明确要求则 image_count=null。"
    "prompt 字段只写给生成模型的一条提示词，不要包含“生成几张/输出几张/返回几张”这类数量指令。"
    "只输出 JSON，不要 Markdown。格式："
    "{\"should_optimize\":true,\"image_count\":null,\"image_count_explicit\":false,"
    "\"prompt\":\"最终提示词\",\"reason\":\"简短原因\"}"
)

PREVIOUS_PROMPT_PLAN_SYSTEM_PROMPT = (
    "你是 AI 图像和视频生成请求的语义规划助手。"
    "只做语义理解和参数提取，不要扩写、润色、补充画面细节。"
    "请判断用户是否明确要求不要优化，是否需要后续提示词优化，语义上要生成多少张图片，"
    "以及真正应该交给生成模型的原始提示词。"
    "如果用户写了“不要优化/不用优化/按原文/保持原提示词”等意思，should_optimize 必须为 false。"
    "如果用户说“给我三版方案/出两套/生成 4 张/多来几张”等，image_count 写对应数量；没有明确数量则为 null。"
    "prompt 字段只允许清理命令词、数量词、控制词，不能新增主体、镜头、光线、风格、画质等细节。"
    "只输出 JSON，不要 Markdown。格式："
    "{\"should_optimize\":false,\"image_count\":3,\"image_count_explicit\":true,"
    "\"prompt\":\"清理后的原始生成提示词\",\"reason\":\"简短原因\"}"
)

DEFAULT_PROMPT_PLAN_SYSTEM_PROMPT = (
    "你是 AI 图像和视频生成请求的语义规划助手。"
    "只做语义理解和参数提取，不要扩写、润色、补充画面细节。"
    "请判断用户是否明确要求不要优化，是否需要后续提示词优化，语义上要生成多少张图片，"
    "以及真正应该交给生成模型的原始提示词。"
    "如果用户写了“不要优化/不用优化/按原文/保持原提示词”等意思，should_optimize 必须为 false。"
    "如果用户只给了很短、很泛的需求，例如“画一个小猫/红烧肉/赛博城市”，should_optimize 应为 true。"
    "如果原文已经有明确主体、场景、构图、光线、风格、限制和细节，should_optimize 才应为 false。"
    "如果用户说“给我三版方案/出两套/生成 4 张/多来几张”等，image_count 写对应数量；没有明确数量则为 null。"
    "prompt 字段只允许清理命令词、数量词、控制词，不能新增主体、镜头、光线、风格、画质等细节。"
    "只输出 JSON，不要 Markdown。格式："
    "{\"should_optimize\":true,\"image_count\":3,\"image_count_explicit\":true,"
    "\"prompt\":\"清理后的原始生成提示词\",\"reason\":\"简短原因\"}"
)

DEFAULT_PROMPT_ENHANCE_SYSTEM_PROMPT = (
    "你是专业的 AI 图像和视频生成提示词优化助手。"
    "你只会在上一步语义规划确认需要优化时被调用。"
    "请把给定提示词改写成适合图像/视频生成模型的高质量中文提示词。"
    "必须保留原文中的所有主体、动作、场景、风格、限制和细节，不能摘要、不能压缩、不能删减关键条件。"
    "在不改变原意的前提下补充主体外观、场景环境、构图、镜头、光线、色彩、材质、氛围、细节和画面质量描述。"
    "不要输出解释、标题、编号、引号或 Markdown，只输出最终提示词。"
)

DEFAULT_IMAGE_EDIT_PLAN_SYSTEM_PROMPT = (
    "你是专业的图像编辑需求理解助手。用户会给出一段自然语言，可能同时带有按顺序编号的图片。"
    "请一次性完成意图判断、图片引用关系分析和最终图生图提示词改写。"
    "请理解“上一张/刚才那张/第一张图/第二张图/第 N 张图/基底/参考/替换/保留/角色特征”等指代关系。"
    "普通单图编辑可以允许使用上一张缓存；凡是用户语义上引用多张图、编号图片、参考图、基础图、替换对象、"
    "组合/融合多个来源，都必须使用当前同一条消息里的图片，不能从聊天记录拼接。"
    "如果用户要求以某一张为基础、用其他图片替换人物或参考角色特征，提示词必须明确："
    "保留基础图的背景、构图、氛围、动作、光线和镜头关系；"
    "将基础图中的目标人物或物体按用户指定关系替换为参考图中的角色身份与外观特征，"
    "包括眼睛、脸型、发型、身材、服装、材质和显著特征；多张参考图要逐一说明用途并保持自然融合。"
    "如果用户语义上要求生成多张结果图，请把数量写入 output_image_count；没有明确要求则为 null。"
    "prompt 字段不要包含“生成几张/输出几张/返回几张”这类结果数量指令。"
    "只输出 JSON，不要 Markdown。格式："
    "{\"requires_current_images\":true,\"required_image_count\":2,"
    "\"should_plan\":true,\"allow_cached_single_image\":false,"
    "\"prompt\":\"最终图生图提示词\",\"primary_image_index\":1,"
    "\"reference_image_indexes\":[2,3],\"output_image_count\":null,"
    "\"summary\":\"一句话说明理解结果\","
    "\"reason\":\"简短原因\"}"
)

try:
    from . import adapters
    from .media import extract_all_media, download_to_file
except ImportError:
    import adapters
    from media import extract_all_media, download_to_file


@register("astrbot_plugin_imagegen", "sunx", "多模态生图视频插件", "0.2.4",
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
            "/画 图 <提示词>          图生图(image-edits；多图需同条消息附图)\n"
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
        prompt = self._prompt_from_event_or_arg(event, prompt, "文", "文生图", "txt2img")
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 文 一只猫")
            event.stop_event()
            return
        prompt, prompt_notice, output_count = await self._prepare_image_prompt(
            prompt, "文生图"
        )
        if prompt_notice:
            yield event.plain_result(prompt_notice)
        yield await self._do_text_to_image(event, prompt, output_count)

    @image_group.command("图", alias={"图生图", "img2img"})
    async def image_to_image(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生图，使用 image-edits 接口，需附带图片'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        prompt = self._prompt_from_event_or_arg(event, prompt, "图", "图生图", "img2img")
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 图 改成水彩")
            event.stop_event()
            return
        requested_output_count = self._requested_output_count(prompt)
        current_image_items = await self._get_current_image_items(event)
        image_intent = await self._plan_image_edit_once(prompt, current_image_items)
        output_count = (
            self._to_positive_int(image_intent.get("output_image_count"))
            or requested_output_count
        )
        generation_prompt = (
            self._strip_requested_output_count(prompt, output_count)
            if output_count
            else prompt
        )
        image_ref_error = self._image_intent_error(
            image_intent, len(current_image_items)
        )
        if image_ref_error:
            yield event.plain_result(image_ref_error)
            event.stop_event()
            return

        using_cached_image = not current_image_items
        image_items = current_image_items
        if using_cached_image:
            if not image_intent.get("allow_cached_single_image", True):
                yield event.plain_result(
                    "❌ 这个图生图需求需要当前消息附图，不能使用上一张图片缓存。"
                )
                event.stop_event()
                return
            image_items = await self._get_cached_image_items(event)

        if not image_items:
            yield event.plain_result(
                "❌ 图生图需要一张图片。请在同一消息里附带图片，"
                "或先发送/生成一张图片后再使用本指令。"
            )
            event.stop_event()
            return
        if using_cached_image:
            prompt, prompt_notice, image_items = await self._prepare_planned_image_edit(
                generation_prompt, image_items, image_intent
            )
        else:
            prompt, prompt_notice, image_items = await self._prepare_planned_image_edit(
                generation_prompt, image_items, image_intent
            )
        if prompt_notice:
            yield event.plain_result(prompt_notice)
        strategy = self._cfg_value(
            "image_to_image_strategy", "image_edits", "generation_options"
        )
        yield await self._do_image_to_image(
            event,
            prompt,
            [item["bytes"] for item in image_items],
            [item["filename"] for item in image_items],
            strategy,
            output_count,
        )

    @image_group.command("视频", alias={"文生视频", "txt2video"})
    async def text_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''文生视频'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        prompt = self._prompt_from_event_or_arg(event, prompt, "视频", "文生视频", "txt2video")
        if not prompt:
            yield event.plain_result("❌ 请提供提示词，例如: /画 视频 火车穿越雪山")
            event.stop_event()
            return
        prompt, prompt_notice = await self._prepare_prompt(prompt, "文生视频")
        if prompt_notice:
            yield event.plain_result(prompt_notice)
        strategy = self._cfg_value(
            "video_via_strategy", "openai_video", "generation_options"
        )
        yield await self._do_text_to_video(event, prompt, strategy)

    @image_group.command("图生视频", alias={"img2video"})
    async def image_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        '''图生视频，需附带图片'''
        denied = self._access_denied_result(event)
        if denied is not None:
            yield denied
            event.stop_event()
            return
        prompt = self._prompt_from_event_or_arg(event, prompt, "图生视频", "img2video")
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
        prompt, prompt_notice = await self._prepare_prompt(prompt, "图生视频")
        if prompt_notice:
            yield event.plain_result(prompt_notice)
        strategy = self._cfg_value(
            "image_to_video_strategy", "openai_video", "generation_options"
        )
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
        return int((self.config.get("media", {}) or {}).get("timeout", 300) or 300)

    @property
    def _proxy(self) -> str:
        return (self.config.get("media", {}) or {}).get("proxy", "") or ""

    @property
    def _multi_media_send_mode(self) -> str:
        value = str((self.config.get("media", {}) or {}).get(
            "multi_media_send_mode", "sequential"
        ) or "sequential").strip().lower()
        return value if value in {"sequential", "chain"} else "sequential"

    @property
    def _multi_media_send_interval(self) -> float:
        value = (self.config.get("media", {}) or {}).get("multi_media_send_interval", 0.8)
        try:
            return max(0.0, min(5.0, float(value)))
        except (TypeError, ValueError):
            return 0.8

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

    def _cfg_value(self, key: str, default=None, *groups):
        if key in self.config:
            return self.config.get(key)
        for group in groups:
            cfg = self._cfg(group)
            if key in cfg:
                return cfg.get(key)
        return default

    def _prompt_from_event_or_arg(
        self, event: AstrMessageEvent, prompt: str, *subcommands: str
    ) -> str:
        """优先使用整条消息里的剩余文本，避免命令参数按空格截断。"""
        arg_prompt = (prompt or "").strip()
        full_text = self._event_message_text(event)
        event_prompt = self._extract_command_prompt(full_text, subcommands)
        if not event_prompt:
            return arg_prompt
        if not arg_prompt:
            return event_prompt
        if event_prompt == arg_prompt:
            return arg_prompt
        if event_prompt.startswith(arg_prompt):
            return event_prompt
        if self._prompt_requests_no_enhancement(event_prompt):
            return event_prompt
        if self._requested_output_count(event_prompt) and not self._requested_output_count(arg_prompt):
            return event_prompt
        return arg_prompt

    @staticmethod
    def _event_message_text(event: AstrMessageEvent) -> str:
        for method_name in ("get_message_str", "get_plain_text"):
            method = getattr(event, method_name, None)
            if not callable(method):
                continue
            try:
                value = method()
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()

        message_obj = getattr(event, "message_obj", None)
        for obj in (event, message_obj):
            if not obj:
                continue
            for attr in ("message_str", "raw_message", "text", "content"):
                value = getattr(obj, attr, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        chain = getattr(message_obj, "message", None) or []
        parts = []
        for comp in chain:
            for attr in ("text", "content", "message"):
                value = getattr(comp, attr, None)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                    break
        return " ".join(parts).strip()

    @staticmethod
    def _extract_command_prompt(text: str, subcommands: tuple) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        subcommands = tuple(cmd for cmd in subcommands if cmd)
        group_names = ("画", "image", "img")
        for group in group_names:
            for subcommand in subcommands:
                pattern = (
                    rf"^\s*/?\s*{re.escape(group)}\s+"
                    rf"{re.escape(subcommand)}\s+(?P<prompt>.+?)\s*$"
                )
                match = re.match(pattern, text, re.I | re.S)
                if match:
                    return match.group("prompt").strip()
        for subcommand in subcommands:
            pattern = rf"^\s*/?\s*{re.escape(subcommand)}\s+(?P<prompt>.+?)\s*$"
            match = re.match(pattern, text, re.I | re.S)
            if match:
                return match.group("prompt").strip()
        return ""

    @staticmethod
    def _cfg_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "开启", "启用"}

    @property
    def _prompt_enhance_enabled(self) -> bool:
        options_cfg = self._cfg("generation_options")
        legacy_cfg = self._cfg("prompt_enhance")
        value = self.config.get(
            "prompt_enhance_enabled",
            options_cfg.get("prompt_enhance_enabled", legacy_cfg.get("enabled", True)),
        )
        return self._cfg_bool(value, True)

    @property
    def _prompt_enhance_show_prompt(self) -> bool:
        options_cfg = self._cfg("generation_options")
        legacy_cfg = self._cfg("prompt_enhance")
        value = self.config.get(
            "prompt_enhance_show_prompt",
            options_cfg.get(
                "prompt_enhance_show_prompt", legacy_cfg.get("show_prompt", True)
            ),
        )
        return self._cfg_bool(value, True)

    @property
    def _prompt_enhance_system_prompt(self) -> str:
        options_cfg = self._cfg("generation_options")
        legacy_cfg = self._cfg("prompt_enhance")
        prompt = (
            self.config.get("prompt_enhance_system_prompt")
            or options_cfg.get("prompt_enhance_system_prompt")
            or legacy_cfg.get("system_prompt")
        )
        if not prompt or str(prompt).strip() in {
            LEGACY_PROMPT_ENHANCE_SYSTEM_PROMPT,
            PREVIOUS_PROMPT_ENHANCE_SYSTEM_PROMPT,
            DECISION_PROMPT_ENHANCE_SYSTEM_PROMPT,
        }:
            return DEFAULT_PROMPT_ENHANCE_SYSTEM_PROMPT
        return prompt

    @property
    def _prompt_plan_system_prompt(self) -> str:
        options_cfg = self._cfg("generation_options")
        prompt = (
            self.config.get("prompt_plan_system_prompt")
            or options_cfg.get("prompt_plan_system_prompt")
        )
        if not prompt or str(prompt).strip() in {
            LEGACY_PROMPT_ENHANCE_SYSTEM_PROMPT,
            PREVIOUS_PROMPT_ENHANCE_SYSTEM_PROMPT,
            DECISION_PROMPT_ENHANCE_SYSTEM_PROMPT,
            PREVIOUS_PROMPT_PLAN_SYSTEM_PROMPT,
        }:
            return DEFAULT_PROMPT_PLAN_SYSTEM_PROMPT
        return prompt

    @property
    def _image_edit_plan_enabled(self) -> bool:
        options_cfg = self._cfg("generation_options")
        value = self.config.get(
            "image_edit_plan_enabled",
            options_cfg.get("image_edit_plan_enabled", True),
        )
        return self._cfg_bool(value, True)

    @property
    def _image_edit_plan_send_images(self) -> bool:
        options_cfg = self._cfg("generation_options")
        value = self.config.get(
            "image_edit_plan_send_images",
            options_cfg.get("image_edit_plan_send_images", True),
        )
        return self._cfg_bool(value, True)

    @property
    def _image_edit_max_images(self) -> int:
        options_cfg = self._cfg("generation_options")
        value = self.config.get(
            "image_edit_max_images",
            options_cfg.get("image_edit_max_images", 4),
        )
        try:
            return max(1, min(10, int(value)))
        except (TypeError, ValueError):
            return 4

    @property
    def _image_edit_plan_system_prompt(self) -> str:
        options_cfg = self._cfg("generation_options")
        return (
            self.config.get("image_edit_plan_system_prompt")
            or options_cfg.get("image_edit_plan_system_prompt")
            or DEFAULT_IMAGE_EDIT_PLAN_SYSTEM_PROMPT
        )

    def _chat_cfg_for_prompt_tools(self, system_prompt: str) -> dict:
        cfg = dict(self._cfg("adapter_openai_chat"))
        prompt_cfg = self._cfg("adapter_prompt_chat")
        for key in ("base_url", "api_key", "model"):
            value = prompt_cfg.get(key)
            if value:
                cfg[key] = value
        options_cfg = self._cfg("generation_options")
        prompt_model = (
            self.config.get("prompt_chat_model")
            or options_cfg.get("prompt_chat_model")
            or cfg.get("model")
        )
        if prompt_model:
            cfg["model"] = prompt_model
        cfg["system_prompt"] = system_prompt
        return cfg

    async def _prepare_prompt(self, prompt: str, task_name: str) -> tuple:
        """可选地用 chat completions 优化提示词；失败时回退原文。"""
        final_prompt, notice, _count = await self._prepare_prompt_details(
            prompt, task_name, allow_image_count=False
        )
        return final_prompt, notice

    async def _prepare_image_prompt(self, prompt: str, task_name: str) -> tuple:
        """准备图片生成提示词，同时解析用户语义里的输出张数。"""
        return await self._prepare_prompt_details(
            prompt, task_name, allow_image_count=True
        )

    async def _prepare_prompt_details(
        self, prompt: str, task_name: str, allow_image_count: bool = False
    ) -> tuple:
        """返回 (最终提示词, 可发送说明, 输出图片数量或 None)。"""
        original = (prompt or "").strip()
        fallback_count = (
            self._requested_output_count(original) if allow_image_count else None
        )
        source_prompt = self._fallback_generation_prompt(original, fallback_count)
        if not original:
            return "", None, fallback_count
        if not self._prompt_enhance_enabled:
            return source_prompt, None, fallback_count

        chat_cfg = self._chat_cfg_for_prompt_tools(self._prompt_plan_system_prompt)
        if not chat_cfg.get("base_url"):
            return source_prompt, None, fallback_count

        explicit_no_enhance = self._prompt_requests_no_enhancement(original)
        try:
            plan = await self._plan_prompt_request(
                chat_cfg,
                original,
                task_name,
                fallback_count,
                allow_image_count,
                explicit_no_enhance,
            )
        except Exception as e:
            logger.warning(f"提示词语义规划失败，使用本地兜底: {e}")
            enhanced, notice = await self._fallback_enhance_after_plan_failure(
                source_prompt, task_name, explicit_no_enhance
            )
            if enhanced:
                return enhanced, notice, fallback_count
            return source_prompt, None, fallback_count

        planned_prompt = plan.get("prompt") or source_prompt
        output_count = plan.get("image_count")
        if explicit_no_enhance:
            source_prompt = planned_prompt or self._fallback_generation_prompt(
                original, output_count
            )
            return source_prompt, None, output_count
        if not plan.get("should_optimize"):
            return planned_prompt, None, output_count

        try:
            enhanced = await self._enhance_prompt_once(planned_prompt, task_name)
        except Exception as e:
            logger.warning(f"提示词优化失败，使用规划提示词: {e}")
            return planned_prompt, None, output_count

        if not enhanced:
            return planned_prompt, None, output_count
        if self._is_prompt_enhancement_degraded(planned_prompt, enhanced):
            logger.warning(
                "提示词优化结果过短或疑似丢失细节，使用原提示词: "
                f"original={planned_prompt!r}, enhanced={enhanced!r}"
            )
            return planned_prompt, None, output_count

        notice = (
            f"✨ 优化后的提示词：\n{enhanced}"
            if self._prompt_enhance_show_prompt
            else None
        )
        return enhanced, notice, output_count

    async def _fallback_enhance_after_plan_failure(
        self, prompt: str, task_name: str, explicit_no_enhance: bool
    ) -> tuple:
        if explicit_no_enhance or not self._should_optimize_prompt_fallback(prompt):
            return "", None
        try:
            enhanced = await self._enhance_prompt_once(prompt, task_name)
        except Exception as e:
            logger.warning(f"提示词规划失败后的兜底优化也失败: {e}")
            return "", None
        if not enhanced or self._is_prompt_enhancement_degraded(prompt, enhanced):
            return "", None
        notice = (
            f"✨ 优化后的提示词：\n{enhanced}"
            if self._prompt_enhance_show_prompt
            else None
        )
        return enhanced, notice

    async def _plan_prompt_request(
        self,
        chat_cfg: dict,
        original: str,
        task_name: str,
        fallback_count,
        allow_image_count: bool,
        explicit_no_enhance: bool,
    ) -> dict:
        plan_prompt = (
            f"任务类型：{task_name}\n"
            f"用户原始提示词：{original}\n"
            f"用户是否显式要求不要优化：{'true' if explicit_no_enhance else 'false'}\n"
            f"本地兜底解析到的输出图片数量：{fallback_count if fallback_count else 'null'}\n"
            "请只做语义规划，不要优化提示词。"
        )
        resp = await adapters.openai_chat(
            chat_cfg, plan_prompt, timeout=self._timeout, proxy=self._proxy
        )
        plan_text = self._extract_chat_text(resp)
        plan = self._extract_json_object(plan_text)
        if not plan:
            raise ValueError(f"规划响应不是 JSON: {plan_text[:120]}")
        return self._normalize_prompt_plan(
            plan, original, fallback_count, allow_image_count, explicit_no_enhance
        )

    async def _enhance_prompt_once(self, prompt: str, task_name: str) -> str:
        chat_cfg = self._chat_cfg_for_prompt_tools(self._prompt_enhance_system_prompt)
        if not chat_cfg.get("base_url"):
            return ""
        rewrite_prompt = (
            f"任务类型：{task_name}\n"
            f"待优化提示词：{prompt}\n"
            "请只输出优化后的最终提示词。"
        )
        resp = await adapters.openai_chat(
            chat_cfg, rewrite_prompt, timeout=self._timeout, proxy=self._proxy
        )
        return self._clean_prompt_text(self._extract_chat_text(resp))

    def _normalize_prompt_plan(
        self,
        plan: dict,
        original: str,
        fallback_count,
        allow_image_count: bool,
        explicit_no_enhance: bool,
    ) -> dict:
        output_count = fallback_count
        if allow_image_count:
            output_count = self._output_count_from_mapping(plan, fallback_count)

        candidate = self._clean_prompt_text(
            plan.get("prompt")
            or plan.get("clean_prompt")
            or plan.get("generation_prompt")
            or plan.get("final_prompt")
            or ""
        )
        if not candidate or self._looks_prompt_expanded(original, candidate):
            candidate = self._fallback_generation_prompt(original, output_count)
        else:
            candidate = self._fallback_generation_prompt(candidate, output_count)

        optimize_value = self._first_present(
            plan,
            "should_optimize",
            "optimize",
            "should_enhance",
            "need_optimize",
            "needs_optimization",
            "requires_optimization",
            "optimize_prompt",
            "enhance_prompt",
        )
        if optimize_value is None:
            should_optimize = self._should_optimize_prompt_fallback(candidate)
        else:
            should_optimize = self._json_bool(optimize_value, False)
        if explicit_no_enhance:
            should_optimize = False

        return {
            "prompt": candidate,
            "image_count": output_count,
            "should_optimize": should_optimize,
        }

    @staticmethod
    def _first_present(data: dict, *keys):
        for key in keys:
            if key in data:
                return data.get(key)
        return None

    @staticmethod
    def _should_optimize_prompt_fallback(prompt: str) -> bool:
        text = (prompt or "").strip()
        signal_len = ImageGenPlugin._prompt_signal_len(text)
        if signal_len < 18:
            return True
        detail_markers = (
            "镜头",
            "构图",
            "光线",
            "风格",
            "背景",
            "氛围",
            "材质",
            "色彩",
            "细节",
            "不要",
            "保持",
            "参考",
            "替换",
        )
        return signal_len < 36 and not any(marker in text for marker in detail_markers)

    @staticmethod
    def _looks_prompt_expanded(original: str, candidate: str) -> bool:
        original_len = ImageGenPlugin._prompt_signal_len(
            ImageGenPlugin._strip_prompt_control_phrases(original)
        )
        candidate_len = ImageGenPlugin._prompt_signal_len(candidate)
        return bool(original_len and candidate_len > max(original_len * 1.8, original_len + 40))

    @classmethod
    def _fallback_generation_prompt(cls, prompt: str, output_count=None) -> str:
        text = prompt or ""
        if output_count:
            text = cls._strip_requested_output_count(text, output_count)
        text = cls._strip_prompt_control_phrases(text)
        return text.strip() or (prompt or "").strip()

    @staticmethod
    def _prompt_requests_no_enhancement(prompt: str) -> bool:
        prompt = prompt or ""
        markers = (
            "不要优化",
            "不用优化",
            "无需优化",
            "别优化",
            "不要改写",
            "不用改写",
            "别改写",
            "按原文",
            "使用原文",
            "保持原提示词",
            "原提示词不变",
            "do not optimize",
            "no optimize",
            "no rewrite",
            "keep prompt",
        )
        lower = prompt.lower()
        return any(marker in lower for marker in markers)

    @staticmethod
    def _strip_prompt_control_phrases(prompt: str) -> str:
        text = prompt or ""
        controls = (
            "不要优化",
            "不用优化",
            "无需优化",
            "别优化",
            "不要改写",
            "不用改写",
            "别改写",
            "按原文",
            "使用原文",
            "保持原提示词",
            "原提示词不变",
            "do not optimize",
            "no optimize",
            "no rewrite",
            "keep prompt",
        )
        for control in controls:
            text = re.sub(re.escape(control), "", text, flags=re.I)
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"^[\s,，;；:：、。]+", "", text)
        text = re.sub(r"[\s,，;；:：、。]+$", "", text)
        return text.strip() or (prompt or "").strip()

    @staticmethod
    def _is_prompt_enhancement_degraded(original: str, enhanced: str) -> bool:
        original_len = ImageGenPlugin._prompt_signal_len(original)
        enhanced_len = ImageGenPlugin._prompt_signal_len(enhanced)
        if not enhanced_len:
            return True
        if original_len < 12:
            return enhanced_len < max(original_len + 8, 18)
        if original_len < 80:
            return enhanced_len < max(int(original_len * 1.15), original_len + 8)
        return enhanced_len < int(original_len * 0.85)

    @staticmethod
    def _prompt_signal_len(text: str) -> int:
        return len(re.sub(r"\s+", "", text or ""))

    @staticmethod
    def _json_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "是", "需要", "优化"}:
            return True
        if text in {"0", "false", "no", "off", "否", "不", "不需要", "不要"}:
            return False
        return default

    @staticmethod
    def _clamp_output_count(count) -> int:
        try:
            return max(1, min(10, int(count)))
        except (TypeError, ValueError):
            return None

    def _output_count_from_mapping(self, data: dict, fallback_count=None):
        if not isinstance(data, dict):
            return fallback_count
        count = None
        for key in (
            "image_count",
            "output_image_count",
            "n",
            "count",
            "num_images",
            "number_of_images",
        ):
            if key in data:
                count = self._to_positive_int(data.get(key))
                break
        if count is None:
            return fallback_count

        count = self._clamp_output_count(count)
        explicit = self._json_bool(
            data.get(
                "image_count_explicit",
                data.get("output_image_count_explicit", False),
            ),
            False,
        )
        if fallback_count is not None and count == 1 and not explicit:
            return fallback_count
        if count == 1 and fallback_count is None and not explicit:
            return None
        return count

    @classmethod
    def _requested_output_count(cls, prompt: str):
        matches = cls._output_count_matches(prompt)
        if not matches:
            return None
        return cls._clamp_output_count(matches[-1][0])

    @classmethod
    def _strip_requested_output_count(cls, prompt: str, count=None) -> str:
        text = prompt or ""
        matches = cls._select_output_count_matches(cls._output_count_matches(text))
        if count:
            matches = [match for match in matches if match[0] == count]
        for _match_count, start, end in reversed(matches):
            text = text[:start] + text[end:]
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"^[\s,，;；:：、。]+", "", text)
        text = re.sub(r"[\s,，;；:：、。]+$", "", text)
        return text.strip() or (prompt or "").strip()

    @classmethod
    def _output_count_matches(cls, prompt: str) -> list:
        prompt = prompt or ""
        num = r"(?P<num>\d{1,2}|[一二两三四五六七八九十])"
        target = r"(?:图|图片|照片|图像|画|插画|海报|壁纸|头像|表情包|方案|版本)"
        patterns = (
            rf"(?i)\b(?:n|num|count)\s*[:=：]\s*(?P<num>\d{{1,2}})\b",
            rf"(?:数量|张数|图片数|生成数量)\s*(?:[:=：为是]\s*)?{num}\s*(?:张|幅|个|份|版|套)?\s*{target}?",
            rf"{num}\s*(?:张|幅|份|版|套)\s*{target}",
            rf"{num}\s*个\s*{target}",
            rf"(?:生成|输出|返回|来|出|做|给我|帮我|我要|想要|要|弄|整|发)\s*"
            rf"(?:一共|总共|至少|大概|约)?\s*{num}\s*(?:张|幅|份|版|套)"
            rf"(?:\s*{target})?",
            rf"(?:画|绘制|制作|创建)\s*"
            rf"(?:一共|总共|至少|大概|约)?\s*{num}\s*(?:张|幅|份|版|套)"
            rf"(?:\s*{target})?",
            rf"(?:画|绘制|制作|创建)\s*{num}\s*(?:张|幅|份|版|套)\s*{target}",
        )
        matches = []
        for pattern in patterns:
            for match in re.finditer(pattern, prompt):
                count = cls._parse_count_text(match.group("num"))
                count = cls._clamp_output_count(count)
                if count:
                    matches.append((count, match.start(), match.end()))
        return cls._select_output_count_matches(matches)

    @staticmethod
    def _select_output_count_matches(matches: list) -> list:
        matches = sorted(matches, key=lambda item: (item[1], -(item[2] - item[1])))
        selected = []
        occupied = []
        for item in matches:
            _count, start, end = item
            if any(not (end <= used_start or start >= used_end) for used_start, used_end in occupied):
                continue
            selected.append(item)
            occupied.append((start, end))
        return sorted(selected, key=lambda item: (item[1], item[2]))

    @staticmethod
    def _parse_count_text(value):
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if re.fullmatch(r"\d{1,2}", text):
            return int(text)
        return ImageGenPlugin._parse_zh_int(text)

    @staticmethod
    def _parse_zh_int(text: str):
        text = (text or "").strip()
        values = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        return values.get(text)

    async def _plan_image_edit_once(self, prompt: str, current_image_items: list) -> dict:
        """用一次 Chat 完成图生图意图判断、图片选择和提示词规划。"""
        original = (prompt or "").strip()
        fallback = self._fallback_image_edit_intent(original, len(current_image_items))
        fallback.update({
            "prompt": "",
            "primary_image_index": None,
            "reference_image_indexes": [],
            "summary": "",
            "chat_used": False,
        })
        if not self._image_edit_plan_enabled:
            return fallback

        chat_cfg = self._chat_cfg_for_prompt_tools(self._image_edit_plan_system_prompt)
        if not chat_cfg.get("base_url"):
            return fallback

        plan_prompt = (
            f"用户原始图生图需求：{original}\n"
            f"当前同一条消息中附带的图片数量：{len(current_image_items)}，"
            "如果有图片则编号从 1 开始。\n"
            "请输出严格 JSON。字段必须包含："
            "requires_current_images, required_image_count, should_plan, "
            "allow_cached_single_image, prompt, primary_image_index, "
            "reference_image_indexes, output_image_count, summary, reason。"
        )
        image_bytes = [item["bytes"] for item in current_image_items]
        image_names = [item["filename"] for item in current_image_items]
        try:
            resp = await adapters.openai_chat(
                chat_cfg,
                plan_prompt,
                image_bytes=image_bytes if self._image_edit_plan_send_images else None,
                image_filename=image_names if self._image_edit_plan_send_images else None,
                timeout=self._timeout,
                proxy=self._proxy,
            )
            plan_text = self._extract_chat_text(resp)
            plan = self._extract_json_object(plan_text)
            if not plan and plan_text:
                plan = {"prompt": plan_text}
            normalized = self._normalize_image_edit_plan(plan, fallback)
            normalized["chat_used"] = True
            return normalized
        except Exception as e:
            logger.warning(f"图生图自然语言理解失败，使用兜底判断: {e}")
            return fallback

    async def _prepare_planned_image_edit(
        self, prompt: str, image_items: list, plan: dict
    ) -> tuple:
        original = (prompt or "").strip()
        selected_items = self._select_planned_images(image_items, plan)
        planned_prompt = self._clean_prompt_text((plan or {}).get("prompt", ""))
        if planned_prompt:
            return planned_prompt, self._image_edit_plan_notice(plan, planned_prompt), selected_items

        if (plan or {}).get("chat_used"):
            return original, None, selected_items

        enhanced, notice = await self._prepare_prompt(original, "图生图")
        return enhanced, notice, selected_items

    def _normalize_image_edit_plan(self, plan: dict, fallback: dict) -> dict:
        if not isinstance(plan, dict):
            return fallback

        normalized = dict(fallback)
        for key in (
            "requires_current_images",
            "should_plan",
            "allow_cached_single_image",
        ):
            if key in plan:
                normalized[key] = self._cfg_bool(plan.get(key), fallback.get(key))

        required = self._to_positive_int(
            plan.get("required_image_count"),
            normalized.get("required_image_count", 1),
        )
        normalized["required_image_count"] = max(1, min(10, required))

        primary = self._first_positive_index(
            plan,
            "primary_image_index",
            "base_image_index",
            "main_image_index",
            "source_image_index",
        )
        if primary:
            normalized["primary_image_index"] = primary

        refs = self._first_index_list(
            plan,
            "reference_image_indexes",
            "reference_images",
            "secondary_image_indexes",
            "selected_image_indexes",
            "image_indexes",
        )
        if refs:
            normalized["reference_image_indexes"] = refs
            normalized["required_image_count"] = max(
                normalized["required_image_count"], max(refs)
            )
        if primary:
            normalized["required_image_count"] = max(
                normalized["required_image_count"], primary
            )

        prompt = self._clean_prompt_text(
            plan.get("prompt")
            or plan.get("final_prompt")
            or plan.get("rewritten_prompt")
            or ""
        )
        output_count = self._output_count_from_mapping(
            plan, fallback.get("output_image_count")
        )
        if output_count:
            normalized["output_image_count"] = output_count
            if prompt:
                prompt = self._strip_requested_output_count(prompt, output_count)
        if prompt and (normalized.get("should_plan") or self._prompt_enhance_enabled):
            normalized["prompt"] = prompt

        for key in ("summary", "reason"):
            value = str(plan.get(key) or "").strip()
            if value:
                normalized[key] = value

        fallback_required = int(fallback.get("required_image_count") or 1)
        if fallback.get("requires_current_images") or fallback_required > 1:
            normalized["required_image_count"] = max(
                normalized["required_image_count"], fallback_required
            )
            normalized["requires_current_images"] = True
            normalized["allow_cached_single_image"] = False
            normalized["should_plan"] = True

        if normalized["required_image_count"] > 1:
            normalized["requires_current_images"] = True
            normalized["allow_cached_single_image"] = False
            normalized["should_plan"] = True
        return normalized

    def _image_edit_plan_notice(self, plan: dict, planned_prompt: str) -> str:
        if not self._prompt_enhance_show_prompt:
            return None
        summary = str((plan or {}).get("summary") or "").strip()
        return (
            "✨ 图生图理解：\n"
            f"{summary + chr(10) if summary else ''}"
            f"优化后的提示词：\n{planned_prompt}"
        )

    def _fallback_image_edit_intent(
        self, prompt: str, current_image_count: int
    ) -> dict:
        indexes = self._requested_image_indexes(prompt)
        mentions_multi = self._mentions_multi_image(prompt)
        required = max(indexes) if indexes else (2 if mentions_multi else 1)
        should_plan = self._should_plan_image_edit(prompt, current_image_count)
        requires_current = bool(indexes) or required > 1 or mentions_multi
        return {
            "requires_current_images": requires_current,
            "required_image_count": max(1, min(10, required)),
            "output_image_count": self._requested_output_count(prompt),
            "should_plan": should_plan,
            "allow_cached_single_image": not requires_current,
            "reason": "fallback",
        }

    @staticmethod
    def _should_plan_image_edit(prompt: str, image_count: int) -> bool:
        if image_count >= 2:
            return True
        markers = (
            "第一张",
            "第二张",
            "第三张",
            "第四张",
            "第1张",
            "第2张",
            "第3张",
            "第4张",
            "第n张",
            "第N张",
            "基于",
            "以",
            "为基础",
            "参考",
            "替换",
            "保留",
            "角色特征",
            "人物",
            "身材",
            "眼睛",
            "动作",
            "背景氛围",
        )
        return any(marker in (prompt or "") for marker in markers)

    @staticmethod
    def _image_intent_error(intent: dict, current_count: int) -> str:
        if not intent.get("requires_current_images"):
            return ""
        required = int(intent.get("required_image_count") or 1)
        if current_count < required:
            return (
                "❌ 这个图生图需求需要在同一条消息里附带对应图片。"
                f"当前语义分析至少需要 {required} 张当前消息图片，"
                f"但当前消息只有 {current_count} 张；"
                "插件不会从聊天记录或上一张缓存里拼接多图。"
            )
        return ""

    @staticmethod
    def _requested_image_indexes(prompt: str) -> set:
        prompt = prompt or ""
        indexes = set()
        zh_nums = ImageGenPlugin._zh_image_numbers()
        for match in re.finditer(r"第\s*(\d{1,2})\s*张", prompt, re.I):
            indexes.add(int(match.group(1)))
        for match in re.finditer(r"(?:图|image)\s*(\d{1,2})", prompt, re.I):
            indexes.add(int(match.group(1)))
        for match in re.finditer(r"(\d{1,2})\s*号\s*(?:图|图片)", prompt, re.I):
            indexes.add(int(match.group(1)))
        for num, index in zh_nums.items():
            if (
                f"第{num}张" in prompt
                or f"第{num}张图" in prompt
                or f"图{num}" in prompt
                or f"{num}号图" in prompt
            ):
                indexes.add(index)
        return indexes

    @staticmethod
    def _zh_image_numbers() -> dict:
        return {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }

    @staticmethod
    def _mentions_multi_image(prompt: str) -> bool:
        prompt = prompt or ""
        markers = (
            "多张",
            "两张",
            "几张",
            "另一张",
            "其它图",
            "其他图",
            "参考图",
            "素材图",
        )
        return any(marker in prompt for marker in markers)

    @staticmethod
    def _extract_json_object(text: str) -> dict:
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _select_planned_images(image_items: list, plan: dict) -> list:
        if not isinstance(plan, dict):
            return image_items
        indexes = []
        primary = ImageGenPlugin._to_positive_int(plan.get("primary_image_index"))
        if primary:
            indexes.append(primary)
        for key in (
            "reference_image_indexes",
            "selected_image_indexes",
            "image_indexes",
        ):
            refs = plan.get(key)
            if isinstance(refs, list):
                indexes.extend(
                    index
                    for index in (ImageGenPlugin._to_positive_int(ref) for ref in refs)
                    if index
                )
            elif refs:
                indexes.extend(ImageGenPlugin._extract_index_values(refs))
        selected = []
        seen = set()
        for index in indexes:
            zero_based = index - 1
            if 0 <= zero_based < len(image_items) and zero_based not in seen:
                selected.append(image_items[zero_based])
                seen.add(zero_based)
        return selected or image_items

    @staticmethod
    def _to_positive_int(value, default=None):
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, (int, float)):
            return int(value) if int(value) > 0 else default
        match = re.search(r"\d+", str(value))
        if match:
            number = int(match.group(0))
            return number if number > 0 else default
        number = ImageGenPlugin._parse_zh_int(str(value))
        if not number:
            return default
        return number if number > 0 else default

    @staticmethod
    def _first_positive_index(data: dict, *keys):
        for key in keys:
            index = ImageGenPlugin._to_positive_int(data.get(key))
            if index:
                return index
        return None

    @staticmethod
    def _first_index_list(data: dict, *keys) -> list:
        for key in keys:
            if key not in data:
                continue
            indexes = ImageGenPlugin._extract_index_values(data.get(key))
            if indexes:
                return indexes
        return []

    @staticmethod
    def _extract_index_values(value) -> list:
        if isinstance(value, (list, tuple, set)):
            values = []
            for item in value:
                index = ImageGenPlugin._to_positive_int(item)
                if index:
                    values.append(index)
            return values
        return [int(item) for item in re.findall(r"\d+", str(value)) if int(item) > 0]

    @staticmethod
    def _extract_chat_text(resp: dict) -> str:
        if not isinstance(resp, dict):
            return ""

        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] or {}
            if isinstance(choice, dict):
                message = choice.get("message") or {}
                if isinstance(message, dict):
                    text = ImageGenPlugin._content_to_text(message.get("content"))
                    if text:
                        return text
                text = ImageGenPlugin._content_to_text(choice.get("text"))
                if text:
                    return text

        for key in ("output_text", "content", "text"):
            text = ImageGenPlugin._content_to_text(resp.get(key))
            if text:
                return text

        data = resp.get("data")
        if isinstance(data, dict):
            for key in ("output_text", "content", "text"):
                text = ImageGenPlugin._content_to_text(data.get(key))
                if text:
                    return text
        return ""

    @staticmethod
    def _content_to_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if isinstance(value, dict):
                        value = value.get("text") or value.get("content")
                    if value:
                        parts.append(str(value))
            return "\n".join(parts)
        return ""

    @staticmethod
    def _clean_prompt_text(text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^```(?:\w+)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

        prefixes = (
            "优化后的提示词：",
            "优化提示词：",
            "最终提示词：",
            "提示词：",
            "Prompt:",
            "prompt:",
        )
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        quote_pairs = {('"', '"'), ("'", "'"), ("“", "”"), ("「", "」")}
        if len(text) >= 2 and (text[0], text[-1]) in quote_pairs:
            text = text[1:-1].strip()
        return text

    def _access_denied_result(self, event: AstrMessageEvent):
        """检查用户/群聊白名单；白名单为空时默认不限制。"""
        user_whitelist = self._split_id_list(
            self._cfg_value("user_whitelist", "", "access_control")
        )
        group_whitelist = self._split_id_list(
            self._cfg_value("group_whitelist", "", "access_control")
        )
        deny_message = (
            self._cfg_value("deny_message", "", "access_control")
            or "❌ 你没有权限使用画图/视频插件。"
        )

        sender_id = self._event_sender_id(event)
        group_id = self._event_group_id(event)

        if user_whitelist and sender_id not in user_whitelist:
            logger.info(f"拒绝非白名单用户使用生图插件: user={sender_id}")
            return event.plain_result(deny_message)

        if group_whitelist and group_id and group_id not in group_whitelist:
            logger.info(f"拒绝非白名单群聊使用生图插件: group={group_id}")
            return event.plain_result(deny_message)

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
        refs = self._extract_image_refs(event)
        return refs[0] if refs else None

    def _extract_image_refs(self, event: AstrMessageEvent) -> list:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None) or []
        refs = []
        for comp in chain:
            if isinstance(comp, Comp.Image):
                value = getattr(comp, "url", None) or getattr(comp, "file", None) \
                    or getattr(comp, "path", None)
                if value:
                    value = str(value)
                    refs.append((value, self._image_ref_filename(value)))
        return refs

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
        items = await self._get_image_items(event, max_images=1)
        if items:
            return items[0]["bytes"], items[0]["filename"]
        return None, None

    async def _get_image_items(
        self, event: AstrMessageEvent, max_images: int = None
    ) -> list:
        """从当前消息读取多张图；没有当前图时回退上一张缓存。"""
        items = await self._get_current_image_items(event, max_images)
        if items:
            return items
        return await self._get_cached_image_items(event)

    async def _get_current_image_items(
        self, event: AstrMessageEvent, max_images: int = None
    ) -> list:
        """只读取当前消息里的图片，不引用缓存。"""
        items = []
        max_images = self._image_edit_max_images if max_images is None else max_images
        refs = self._extract_image_refs(event)[:max(1, max_images)]
        for index, image_ref in enumerate(refs, start=1):
            data, filename = await self._load_image_bytes(*image_ref)
            if data:
                filename = filename or image_ref[1] or f"input_{index}.png"
                items.append({
                    "bytes": data,
                    "filename": filename,
                    "ref": image_ref[0],
                })

        if items:
            self._remember_last_image(
                event, items[0]["ref"], items[0]["filename"], source="current"
            )
            return items

        return []

    async def _get_cached_image_items(self, event: AstrMessageEvent) -> list:
        """只读取同会话同用户上一张图片缓存。"""
        cached_ref = self._get_cached_image_ref(event)
        if cached_ref[0]:
            data, filename = await self._load_image_bytes(*cached_ref)
            if data:
                return [{
                    "bytes": data,
                    "filename": filename or cached_ref[1] or "input.png",
                    "ref": cached_ref[0],
                }]
        return []

    @staticmethod
    def _cfg_with_output_count(cfg: dict, output_count=None) -> dict:
        cfg = dict(cfg or {})
        count = ImageGenPlugin._clamp_output_count(output_count)
        if count:
            cfg["n"] = count
        return cfg

    async def _complete_requested_media_count(
        self, resp: dict, output_count, request_one, task_name: str
    ) -> dict:
        target = self._clamp_output_count(output_count)
        if not target or target <= 1:
            return resp

        medias = extract_all_media(resp)
        if len(medias) >= target:
            return resp

        logger.info(
            f"{task_name} 请求 {target} 张，但首个响应只有 {len(medias)} 张，"
            "尝试追加请求补齐。"
        )
        attempts = 0
        max_attempts = max(1, target - len(medias)) * 2
        while len(medias) < target and attempts < max_attempts:
            attempts += 1
            extra_resp = await request_one(1)
            extra_medias = extract_all_media(extra_resp)
            if not extra_medias:
                logger.warning(f"{task_name} 追加请求未提取到媒体: {extra_resp}")
                break
            before_count = len(medias)
            for media in extra_medias:
                if media not in medias:
                    medias.append(media)
                if len(medias) >= target:
                    break
            if len(medias) == before_count:
                logger.warning(f"{task_name} 追加请求返回重复媒体，停止补齐。")
                break

        if not medias:
            return resp
        return self._response_from_media_list(medias[:target])

    @staticmethod
    def _response_from_media_list(medias: list) -> dict:
        data = []
        for kind, value in medias:
            if kind == "video":
                data.append({"video_url": value})
            elif isinstance(value, str) and value.startswith("data:"):
                data.append(value)
            else:
                data.append({"url": value})
        return {"data": data}

    # ---- 文生图 ----
    async def _do_text_to_image(self, event, prompt, output_count=None):
        base_cfg = self._cfg("adapter_image_generation")
        cfg = self._cfg_with_output_count(base_cfg, output_count)
        try:
            resp = await adapters.image_generation(
                cfg, prompt, self._timeout, proxy=self._proxy
            )
            resp = await self._complete_requested_media_count(
                resp,
                output_count,
                lambda n: adapters.image_generation(
                    self._cfg_with_output_count(base_cfg, n),
                    prompt,
                    self._timeout,
                    proxy=self._proxy,
                ),
                "文生图",
            )
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "文生图")

    # ---- 图生图 ----
    async def _do_image_to_image(
        self, event, prompt, img_bytes, img_name, strategy, output_count=None
    ):
        if strategy == "image_generation":
            base_cfg = self._cfg("adapter_image_generation")
            cfg = self._cfg_with_output_count(base_cfg, output_count)
            try:
                resp = await adapters.image_generation(
                    cfg, prompt, self._timeout, img_bytes, img_name, self._proxy
                )
                resp = await self._complete_requested_media_count(
                    resp,
                    output_count,
                    lambda n: adapters.image_generation(
                        self._cfg_with_output_count(base_cfg, n),
                        prompt,
                        self._timeout,
                        img_bytes,
                        img_name,
                        self._proxy,
                    ),
                    "图生图",
                )
            except adapters.ApiException as e:
                return event.plain_result(f"❌ {e}")
            return await self._send_result(event, resp, "图生图")

        base_cfg = self._cfg("adapter_image_edits")
        cfg = self._cfg_with_output_count(base_cfg, output_count)
        try:
            resp = await adapters.image_edits(
                cfg, prompt, img_bytes, img_name, self._timeout, self._proxy
            )
            resp = await self._complete_requested_media_count(
                resp,
                output_count,
                lambda n: adapters.image_edits(
                    self._cfg_with_output_count(base_cfg, n),
                    prompt,
                    img_bytes,
                    img_name,
                    self._timeout,
                    self._proxy,
                ),
                "图生图",
            )
        except adapters.ApiException as e:
            return event.plain_result(f"❌ {e}")
        return await self._send_result(event, resp, "图生图")

    # ---- 文生视频 ----
    async def _do_text_to_video(self, event, prompt, strategy):
        if strategy == "openai_chat":
            cfg = self._cfg("adapter_openai_chat")
            try:
                resp = await adapters.openai_chat(
                    cfg, prompt, timeout=self._timeout, proxy=self._proxy
                )
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
        medias = extract_all_media(resp)
        if not medias:
            logger.warning(f"{task_name} 响应未提取到媒体: {resp}")
            return event.plain_result(f"❌ {task_name}成功但响应中未找到图片/视频。")

        if len(medias) == 1:
            return await self._send_single_media(event, medias[0], task_name, expect)

        chain = []
        stem = self._stem(event)
        for index, media in enumerate(medias, start=1):
            item = await self._media_to_component(
                event, media, task_name, expect, f"{stem}_{index}"
            )
            if isinstance(item, str):
                return event.plain_result(item)
            chain.append(item)

        if self._multi_media_send_mode == "chain" or not hasattr(event, "send"):
            return event.chain_result(chain)

        sent_count = 0
        try:
            for item in chain[:-1]:
                await event.send(MessageChain([item]))
                sent_count += 1
                if self._multi_media_send_interval:
                    await asyncio.sleep(self._multi_media_send_interval)
        except Exception as e:
            logger.warning(f"{task_name} 多媒体逐条发送失败，发送剩余媒体: {e}")
            remaining = chain[sent_count:]
            if not remaining:
                return event.plain_result(f"⚠️ {task_name}结果已部分发送，但后续发送超时。")
            return event.chain_result(remaining)

        return event.chain_result([chain[-1]])

    async def _send_single_media(self, event, media, task_name, expect=None):
        kind, value = media
        kind = self._effective_media_kind(kind, value, task_name, expect)

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

    async def _media_to_component(
        self, event, media, task_name, expect=None, stem: str = None
    ):
        kind, value = media
        kind = self._effective_media_kind(kind, value, task_name, expect)

        if isinstance(value, str) and value.startswith("http"):
            if kind == "video":
                return Comp.Video.fromURL(url=value)
            self._remember_last_image(event, value, "generated.png", source=task_name)
            return Comp.Image.fromURL(url=value)

        try:
            kind2, path = await download_to_file(
                value, self._save_dir, stem or self._stem(event), self._timeout,
                self._proxy
            )
        except Exception as e:
            return f"❌ 媒体下载失败: {e}"
        kind = kind2 or kind
        if kind == "video":
            return Comp.Video.fromFileSystem(path=path)
        self._remember_last_image(event, path, os.path.basename(path), source=task_name)
        return Comp.Image.fromFileSystem(path=path)

    def _effective_media_kind(self, kind: str, value: str, task_name: str, expect=None):
        if expect == "video" and kind != "video":
            if self._looks_like_video_fallback(value):
                return "video"
            logger.info(f"{task_name} 期望视频但接口返回图片，按图片发送。")
        return kind

    @staticmethod
    def _looks_like_video_fallback(value: str) -> bool:
        if not isinstance(value, str):
            return False
        lower = value.lower()
        if re.search(r"\.(png|jpe?g|webp|gif|bmp)(?:$|[?#])", lower):
            return False
        return bool(
            lower.startswith("http")
            or lower.startswith("data:video/")
            or re.search(r"\.(mp4|mov|webm|mkv|avi)(?:$|[?#])", lower)
        )

    async def terminate(self):
        pass

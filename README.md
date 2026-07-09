# astrbot_plugin_imagegen

一个 AstrBot 多模态生成插件，支持文生图、图生图、文生视频、图生视频。
通过四种 OpenAI 兼容接口适配：
`/v1/images/generations`、`/v1/images/edits`、`/v1/chat/completions`、`/v1/video/generations`。

## 安装
在 AstrBot WebUI 的插件管理页面通过仓库地址安装，或 `plugin i <repo>`。
依赖 `aiohttp`（AstrBot 通常已自带）。

## 配置
插件管理 → 本插件 → 设置 中配置以下适配器（每个含 base_url / api_key / model）：
- `adapter_image_generation` 文生图
- `adapter_image_edits` 图生图 / 图像编辑
- `adapter_prompt_chat` 提示词优化 / 图生图理解
- `adapter_openai_chat` 走对话模型生视频；`adapter_prompt_chat` 留空时也可兼容用于提示词优化
- `adapter_openai_video` 文生视频

以及：
- `generation_options.video_via_strategy` 文生视频优先适配器
- `generation_options.image_to_image_strategy` 图生图优先适配器
- `generation_options.image_to_video_strategy` 图生视频优先适配器
- `generation_options.prompt_chat_model` 提示词优化/图生图理解使用的 Chat 模型（旧版覆盖项；新配置建议填 `adapter_prompt_chat.model`）
- `generation_options.prompt_enhance_enabled` 是否先用 `adapter_prompt_chat` 优化提示词
- `generation_options.prompt_enhance_show_prompt` 是否发送优化后的提示词
- `generation_options.prompt_enhance_system_prompt` 提示词优化系统提示词
- `generation_options.image_edit_plan_enabled` 是否用 Chat 理解自然语言图生图需求
- `generation_options.image_edit_plan_send_images` 图生图理解时是否把图片发送给 Chat
- `generation_options.image_edit_max_images` 图生图最多读取几张图片
- `access_control.user_whitelist` 用户白名单（可空；留空不限制用户）
- `access_control.group_whitelist` 群聊白名单（可空；留空不限制群聊）
- `access_control.deny_message` 无权限提示语
- `image_reference.enable_previous_image` 图生图/图生视频自动复用上一张图片
- `image_reference.previous_image_ttl` 上一张图片缓存有效期
- `media.save_dir` 保存目录（相对 `data/`）

> **图片尺寸**：`adapter_image_generation.size` / `adapter_image_edits.size` 为可手填文本框，支持任意尺寸（如 `1024x1024` / `2048x1152` / `4096x4096`，或 `16:9` 等比例）。最终能否真正输出该尺寸取决于上游渠道/模型支持，请按模型说明填写。

> **Seedream 4.5**：`doubao-seedream-4.5` 在 `/v1/images/generations` 同时支持文生图和图生图。使用它做图生图时，把 `generation_options.image_to_image_strategy` 设为 `image_generation`，`adapter_image_generation.model` 设为 `doubao-seedream-4.5`，`adapter_image_generation.size` 建议从 `1920x1920` 起。`adapter_image_generation.watermark` 默认是 `false`，即默认请求无水印输出；如上游不支持该字段，可改成 `auto`。

> **提示词处理路线**：`generation_options.prompt_enhance_enabled` 默认开启。流程是：用户原文 → `adapter_prompt_chat` 语义规划（是否优化、生成几张、清理后的原始提示词）→ 只有规划结果需要优化时才再次调用 `adapter_prompt_chat` 优化 → 生图。用户写了“不要优化/按原文/保持原提示词”时会强制跳过第二步优化，避免反向删细节。比如 `/画 文 画一个红烧肉，给我三版方案 不要优化` 会识别为生成 3 张，并跳过提示词优化。

> **生成数量**：文生图和图生图会从用户语义里识别输出数量，例如“画三张猫”“生成 3 张赛博城市”“基于这张图出两版不同风格”。识别到数量时会临时覆盖对应适配器的 `n`，没有明确数量时继续使用后台配置里的默认 `n`。如果上游忽略 `n` 只返回 1 张，插件会继续用 `n=1` 追加请求补齐到用户要求的数量；最终能否补齐仍取决于上游接口是否持续可用。

> **图生图语义理解**：`/画 图` 会用一次 `adapter_prompt_chat` 调用同时完成语义分析、图片编号选择、结果图数量和最终提示词改写，并把理解后的提示词发给你。普通“上一张/刚才那张”单图编辑可以复用同会话同用户缓存；多图/编号/参考图/替换角色等语义必须在同一条消息里附带对应图片，不会从聊天记录或上一张缓存里拼接多图。当前消息里的图片按出现顺序编号为第一张、第二张、第三张……默认最多读取 4 张，可用 `generation_options.image_edit_max_images` 调整。如果上游图生图接口支持多图，会把选中的图片一起传给接口，否则取决于上游兼容性。

> **白名单**：`access_control.user_whitelist` 和 `access_control.group_whitelist` 都支持用逗号、空格或换行分隔多个 ID；不填写时默认不限制。群聊白名单只限制群聊消息，私聊不会因为群聊白名单被拦截；如需限制私聊用户，请填写用户白名单。

> **上一张图片**：`/画 图` 和 `/画 图生视频` 会优先使用当前消息附带的图片；当前消息未带图时，会自动使用同一会话、同一用户最近发送的图片，或本插件最近回复给该用户的图片。该缓存只用于普通单图兜底；多图/编号引用必须在同一条消息内附图。默认缓存 1800 秒，可在 `image_reference.previous_image_ttl` 调整。

## 指令
| 指令 | 说明 | 示例 |
|---|---|---|
| `/画 help` | 帮助 | |
| `/画 文 <prompt>` | 文生图，可在提示词里写生成数量 | `/画 文 画三张赛博朋克猫` |
| `/画 图 <prompt>` | 图生图（普通单图可复用上一张图片；多图必须同条消息附图），可在提示词里写结果数量 | `/画 图 基于这张图出两版水彩风格` |
| `/画 视频 <prompt>` | 文生视频 | `/画 视频 火车穿越雪山` |
| `/画 图生视频 <prompt>` | 图生视频（可附带图片，也可复用上一张图片） | `/画 图生视频 让画面动起来` |

> 也可使用别名：`/画 文生图`、`/画 文生视频`。

## 注意
- 图生图会优先读取当前消息里的多张图片并按顺序编号；多图/编号引用不会读取聊天记录或上一张缓存；图生视频读取第一张图片；普通单图没有当前图片时会尝试读取上一张图片缓存。
- 上一张图片缓存按“会话 + 用户”隔离，群聊里不会复用其他用户发送或触发生成的图片。
- 白名单 ID 分别对应 AstrBot 事件中的 `get_sender_id()` 和 `get_group_id()`。
- 媒体文件保存在 `data/imagegen/` 下。
- 视频生成耗时较长，请耐心等待。

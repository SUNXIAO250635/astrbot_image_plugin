# astrbot_plugin_imagegen

一个 AstrBot 多模态生成插件，支持文生图、图生图、文生视频、图生视频。
支持多个供应商实例、按能力路由和失败自动切换。底层兼容四种常用接口：
`/v1/images/generations`、`/v1/images/edits`、`/v1/chat/completions`、`/v1/video/generations`。

## 安装
在 AstrBot WebUI 的插件管理页面通过仓库地址安装，或 `plugin i <repo>`。
依赖 `aiohttp`（AstrBot 通常已自带）和 `Pillow`（用于表情包智能切分）。

## 配置
插件管理 → 本插件 → 设置 中配置以下适配器（每个含 base_url / api_key / model）：
- `adapter_image_generation` 文生图
- `adapter_image_edits` 图生图 / 图像编辑
- `adapter_prompt_chat` 提示词优化 / 图生图理解
- `adapter_openai_chat` 走对话模型生视频；`adapter_prompt_chat` 留空时也可兼容用于提示词优化
- `adapter_openai_video` 文生视频

AstrBot `>=4.10.4` 可使用新的 `providers` 列表添加多个供应商实例。支持的供应商类型包括 OpenAI-compatible、OpenAI Images、Google Gemini、Agnes AI、xAI、MiniMax、阶跃星辰、Zai、grok2api、豆包和 SenseNova。

每个 provider 可配置：

- 唯一 `provider_id`、供应商类型、URL、Key、模型和启用状态
- `capabilities`：`text_to_image`、`image_to_image`、`text_to_video`、`image_to_video`
- 图像接口模式 `generation/edits`，视频接口模式 `video/chat/edits`
- 协议 `openai_compat/gemini/generic_json`；通用 JSON 可配置图片、视频、轮询和文件查询路径
- 同类型 `priority`、超时、代理、尺寸、数量、水印、视频时长和轮询参数

`routing` 可分别设置四种能力的供应商顺序。顺序支持直接填写 provider ID，也支持 `type:openai_compat` 这种类型占位；首选供应商发生网络错误、限流、服务端错误或媒体解析错误时会尝试后备供应商。远端视频已经返回 `task_id` 后，不会因为临时轮询错误盲目创建第二个任务。

`compatibility.mode` 默认是 `router`。需要紧急回滚时可切换为 `legacy`，原来的四个适配器配置无需重填；`providers` 留空时 Router 也会自动读取旧配置。

供应商协议说明：

- OpenAI Images、xAI、grok2api、豆包、阶跃星辰、Zai、Agnes 等可使用 OpenAI-compatible codec，并按实例声明能力。
- Google Gemini 使用原生 `generateContent` 图片输出，支持文本和内联参考图；默认只声明图片能力。
- MiniMax 默认使用 `/v1/image_generation`、`/v1/video_generation`、任务查询和文件下载流程。
- SenseNova 及其他异步 JSON 服务可通过 `generic_json` 填写 `image_path/video_path/poll_path/result_path`，其中任务和文件 ID 使用 `{task_id}`、`{file_id}` 占位。

以及：
- `generation_options.video_via_strategy` 文生视频优先适配器
- `generation_options.image_to_image_strategy` 图生图优先适配器
- `generation_options.image_to_video_strategy` 图生视频优先适配器
- `generation_options.prompt_chat_model` 提示词优化/图生图理解使用的 Chat 模型（旧版覆盖项；新配置建议填 `adapter_prompt_chat.model`）
- `generation_options.prompt_enhance_enabled` 是否先用 `adapter_prompt_chat` 优化提示词
- `generation_options.prompt_enhance_show_prompt` 是否发送优化后的提示词
- `generation_options.prompt_plan_system_prompt` 提示词语义规划系统提示词
- `generation_options.prompt_enhance_system_prompt` 提示词优化系统提示词
- `generation_options.intent_plan_enabled` 是否用 Chat 判断图片/视频模式、预设和数量
- `generation_options.image_edit_plan_enabled` 是否用 Chat 理解自然语言图生图需求
- `generation_options.image_edit_plan_send_images` 图生图理解时是否把图片发送给 Chat
- `generation_options.image_edit_max_images` 图生图最多读取几张图片
- `access_control.user_whitelist` 用户白名单（可空；留空不限制用户）
- `access_control.group_whitelist` 群聊白名单（可空；留空不限制群聊）
- `access_control.user_blacklist` / `group_blacklist` 黑名单（优先于白名单）
- `access_control.deny_message` 无权限提示语
- `image_reference.enable_previous_image` 图生图/图生视频自动复用上一张图片
- `image_reference.previous_image_ttl` 上一张图片缓存有效期
- `image_reference.max_reference_images` 当前请求最多解析的参考图数量
- `jobs.foreground_wait_seconds` 前台等待时间，超时后自动转后台
- `jobs.restore_remote_video_tasks` 插件重载后恢复已有视频 task ID 轮询
- `jobs.delivery_retry_count` / `delivery_retry_delay_seconds` 后台主动发送重试
- `jobs.terminal_retention_seconds` 完成、部分投递和失败任务的 KV 保留时间
- `rate_limit.*` 用户/群周期额度、能力 cost 和并发任务租约（限制值 0 表示关闭）
- `cleanup.*` 受管媒体目录的过期文件清理
- `meme_splitter.*` 表情包自适应切分、视觉兜底、手动网格和透明背景参数
- `meme_splitter.analysis_max_dimension` 大图切分分析最大边长，输出仍保持原分辨率
- `media.save_dir` 保存目录（必须是相对 `data/` 的子目录；绝对路径或 `..` 越界会回退为 `data/imagegen`）
- `media.multi_media_send_mode` 多图/多视频发送方式，默认逐条发送

头像、海报、壁纸、卡片、手机壁纸、手办化、表情包和风格转换预设只追加构图/风格提示，不覆盖供应商或旧版适配器中配置的 `size`。

> **图片尺寸**：`adapter_image_generation.size` / `adapter_image_edits.size` 为可手填文本框，支持任意尺寸（如 `1024x1024` / `2048x1152` / `4096x4096`，或 `16:9` 等比例）。最终能否真正输出该尺寸取决于上游渠道/模型支持，请按模型说明填写。

> **Seedream 4.5**：`doubao-seedream-4.5` 在 `/v1/images/generations` 同时支持文生图和图生图。使用它做图生图时，把 `generation_options.image_to_image_strategy` 设为 `image_generation`，`adapter_image_generation.model` 设为 `doubao-seedream-4.5`，`adapter_image_generation.size` 建议从 `1920x1920` 起。`adapter_image_generation.watermark` 默认是 `false`，即默认请求无水印输出；非 Seedream 模型使用 false 时会自动省略该字段，避免接口不兼容。

> **提示词处理路线**：`generation_options.prompt_enhance_enabled` 默认开启。流程是：用户原文 → `adapter_prompt_chat` 语义规划（是否优化、生成几张、清理后的原始提示词）→ 只有规划结果需要优化时才再次调用 `adapter_prompt_chat` 优化 → 生图。用户写了“不要优化/按原文/保持原提示词”时会强制跳过第二步优化，避免反向删细节。比如 `/画 文 画一个红烧肉，给我三版方案 不要优化` 会识别为生成 3 张，并跳过提示词优化。`adapter_prompt_chat` 的 URL/key/model 可独立配置；如果只填 model，会继承 `adapter_openai_chat` 的 URL/key。

> **生成数量**：文生图和图生图会从用户语义里识别输出数量，例如“画三张猫”“生成 3 张赛博城市”“基于这张图出两版不同风格”。识别到数量时会临时覆盖对应适配器的 `n`，没有明确数量时继续使用后台配置里的默认 `n`。如果上游忽略 `n` 只返回 1 张，插件会继续用 `n=1` 追加请求补齐到用户要求的数量；最终能否补齐仍取决于上游接口是否持续可用。

> **多图发送**：多张结果默认使用 `media.multi_media_send_mode=sequential` 逐条发送，避免 aiocqhttp/NapCat 一次消息链发送多张远程图片时 WebSocket API 超时。如确实需要旧的一条消息链行为，可改为 `chain`。

> **网络与视频任务**：HTTP 连接、代理、SSL 和超时错误会转换成可读提示；视频轮询支持顶层或 `data` 内的 `task_id/id`，401/403/404 等永久错误会直接返回，不再一直重试到超时。

> **前后台任务**：Router 模式下，生成在 `jobs.foreground_wait_seconds` 内完成会直接回复；超过时间会返回 job ID，底层任务不会被取消，完成后通过 AstrBot 主动消息逐条发送。远端视频的 provider 和 task ID 会写入插件 KV，插件重载后只恢复轮询，不会重新提交生成任务。

> **LLM Tool**：插件注册 `generate_media` 工具。LLM 可以根据自然语言自动判断文生图、图生图、文生视频、图生视频、预设和数量；Chat 规划失败时使用本地语义判断，并继续执行原始需求。

> **智能参考图**：`/画 图`、`/画 图生视频`、预设和 LLM Tool 会按顺序解析当前消息图片、显式回复图片、当前合并转发、图片群文件，以及用户明确要求的本人头像或 @ 对象头像。普通“上一张/刚才那张”单图编辑可以复用同会话同用户缓存；手办化、表情包和风格转换只在明确提到上一张，或使用无提示词的一键命令时自动读取缓存。多图只能使用当前请求明确提供或引用的来源，不会从未引用聊天历史或上一张缓存拼接。

> **白名单**：`access_control.user_whitelist` 和 `access_control.group_whitelist` 都支持用逗号、空格或换行分隔多个 ID；不填写时默认不限制。群聊白名单只限制群聊消息，私聊不会因为群聊白名单被拦截；如需限制私聊用户，请填写用户白名单。

> **黑名单与限流**：用户/群黑名单优先于白名单。周期额度和并发限制使用 AstrBot 插件 KV；图片默认 cost 为 1、视频为 3，但所有限制值默认是 0，不填写不会限制现有用户。Router 内部切换后备供应商只算一次用户请求。

> **上一张图片**：`/画 图` 和 `/画 图生视频` 会优先使用当前请求明确提供或引用的图片；未找到时可使用同一会话、同一用户最近发送的图片，或本插件最近回复给该用户的图片。该缓存只用于普通单图兜底，URL/本地文件索引会写入插件 KV。默认缓存 1800 秒，可在 `image_reference.previous_image_ttl` 调整。

> **临时文件清理**：清理器只处理 `media.save_dir` 解析后的受管目录，跳过当前缓存正在引用的本地文件，不会递归删除目录外路径。默认清理超过 24 小时的媒体。

> **表情包智能切分**：`/画 表情包` 会在生成后自动运行 SmartMemeSplitter。默认先使用背景颜色和黑描边连通区域进行自适应切分，再尝试 `grid_rows/grid_columns` 手动网格，最后可复用 `adapter_prompt_chat` 的视觉模型定位独立贴纸区域；所有路径都失败时保留原图，不会丢失生成结果。`transparent_background=true` 时自适应切片输出透明 PNG。可通过 `minimum_slices`、`expected_slices`、`background_tolerance`、`outline_threshold`、`connect_radius`、`min_area_ratio` 和 `padding` 调整质量门槛。

## 指令
| 指令 | 说明 | 示例 |
|---|---|---|
| `/画 help` | 帮助 | |
| `/画 文 <prompt>` | 文生图，可在提示词里写生成数量 | `/画 文 画三张赛博朋克猫` |
| `/画 图 <prompt>` | 图生图，支持直附、回复、转发、群文件和上一张单图缓存 | `/画 图 基于这张图出两版水彩风格` |
| `/画 视频 <prompt>` | 文生视频 | `/画 视频 火车穿越雪山` |
| `/画 图生视频 <prompt>` | 图生视频（可附带图片，也可复用上一张图片） | `/画 图生视频 让画面动起来` |
| `/画 头像/海报/壁纸/卡片/手机壁纸 <prompt>` | 快速版式预设 | `/画 海报 夏日音乐节` |
| `/画 手办化/表情包/风格转换 <prompt>` | 快速参考图预设 | `/画 手办化 把上一张图做成桌面收藏手办` |

> 也可使用别名：`/画 文生图`、`/画 文生视频`。

## 注意
- 图生图会把当前请求明确提供或引用的图片按解析顺序编号；多图/编号引用不会读取未引用聊天历史或上一张缓存；图生视频默认使用第一张解析到的图片。
- 上一张图片缓存按“会话 + 用户”隔离，群聊里不会复用其他用户发送或触发生成的图片。
- 白名单 ID 分别对应 AstrBot 事件中的 `get_sender_id()` 和 `get_group_id()`。
- 媒体文件保存在 `data/imagegen/` 下。
- 视频生成耗时较长，请耐心等待。

## 在线冒烟测试

离线回归不会请求真实供应商。需要验证实际渠道时，通过环境变量提供连接信息，Key 不会写入文件：

```powershell
$env:ASTRBOT_IMAGEGEN_BASE_URL="https://api.example.com"
$env:ASTRBOT_IMAGEGEN_API_KEY="<key>"
$env:ASTRBOT_IMAGEGEN_MODEL="<model>"
python scripts/live_provider_smoke.py text_to_image "一只红色纸鹤"
```

也可以设置 `ASTRBOT_IMAGEGEN_LIVE=1` 后运行 `tests/integration/test_live_provider.py`。真实 AstrBot、NapCat、群文件和 WebUI 渲染仍应在目标部署中完成最终验收。

# astrbot_plugin_imagegen

一个 AstrBot 多模态生成插件，支持文生图、图生图、文生视频、图生视频。
通过四种 OpenAI 兼容接口适配：
`/v1/images/generations`、`/v1/images/edits`、`/v1/chat/completions`、`/v1/video/generations`。

## 安装
在 AstrBot WebUI 的插件管理页面通过仓库地址安装，或 `plugin i <repo>`。
依赖 `aiohttp`（AstrBot 通常已自带）。

## 配置
插件管理 → 本插件 → 设置 中配置以下四个适配器（每个含 base_url / api_key / model）：
- `adapter_image_generation` 文生图
- `adapter_image_edits` 图生图 / 图像编辑
- `adapter_openai_chat` 走对话模型生视频
- `adapter_openai_video` 文生视频

以及：
- `video_via_strategy` 文生视频优先适配器
- `image_to_image_strategy` 图生图优先适配器
- `image_to_video_strategy` 图生视频优先适配器
- `access_control.user_whitelist` 用户白名单（可空）
- `access_control.group_whitelist` 群聊白名单（可空）
- `image_reference.enable_previous_image` 图生图/图生视频自动复用上一张图片
- `image_reference.previous_image_ttl` 上一张图片缓存有效期
- `media.save_dir` 保存目录（相对 `data/`）

> **图片尺寸**：`adapter_image_generation.size` / `adapter_image_edits.size` 为可手填文本框，支持任意尺寸（如 `1024x1024` / `2048x1152` / `4096x4096`，或 `16:9` 等比例）。最终能否真正输出该尺寸取决于上游渠道/模型支持，请按模型说明填写。

> **Seedream 4.5**：`doubao-seedream-4.5` 在 `/v1/images/generations` 同时支持文生图和图生图。使用它做图生图时，把 `image_to_image_strategy` 设为 `image_generation`，`adapter_image_generation.model` 设为 `doubao-seedream-4.5`，`adapter_image_generation.size` 建议从 `1920x1920` 起。`adapter_image_generation.watermark` 默认是 `false`，即默认请求无水印输出；如上游不支持该字段，可改成 `auto`。

> **白名单**：用户白名单和群聊白名单都支持用逗号、空格或换行分隔多个 ID；不填写时默认不限制。群聊白名单只限制群聊消息，私聊不会因为群聊白名单被拦截；如需限制私聊用户，请填写用户白名单。

> **上一张图片**：`/画 图` 和 `/画 图生视频` 会优先使用当前消息附带的图片；当前消息未带图时，会自动使用同一会话、同一用户最近发送的图片，或本插件最近回复给该用户的图片。默认缓存 1800 秒，可在 `image_reference.previous_image_ttl` 调整。

## 指令
| 指令 | 说明 | 示例 |
|---|---|---|
| `/画 help` | 帮助 | |
| `/画 文 <prompt>` | 文生图 | `/画 文 一只赛博朋克猫` |
| `/画 图 <prompt>` | 图生图（可附带图片，也可复用上一张图片） | `/画 图 改成水彩风格` |
| `/画 视频 <prompt>` | 文生视频 | `/画 视频 火车穿越雪山` |
| `/画 图生视频 <prompt>` | 图生视频（可附带图片，也可复用上一张图片） | `/画 图生视频 让画面动起来` |

> 也可使用别名：`/画 文生图`、`/画 文生视频`。

## 注意
- 图生图 / 图生视频 会优先读取当前消息里的图片；没有当前图片时会尝试读取上一张图片缓存。
- 上一张图片缓存按“会话 + 用户”隔离，群聊里不会复用其他用户发送或触发生成的图片。
- 白名单 ID 分别对应 AstrBot 事件中的 `get_sender_id()` 和 `get_group_id()`。
- 媒体文件保存在 `data/imagegen/` 下。
- 视频生成耗时较长，请耐心等待。

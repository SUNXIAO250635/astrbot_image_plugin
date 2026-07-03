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
- `image_to_video_strategy` 图生视频优先适配器
- `media.save_dir` 保存目录（相对 `data/`）

## 指令
| 指令 | 说明 | 示例 |
|---|---|---|
| `/画 help` | 帮助 | |
| `/画 文 <prompt>` | 文生图 | `/画 文 一只赛博朋克猫` |
| `/画 图 <prompt>` | 图生图（消息需附带图片） | `/画 图 改成水彩风格` |
| `/画 视频 <prompt>` | 文生视频 | `/画 视频 火车穿越雪山` |
| `/画 图生视频 <prompt>` | 图生视频（消息需附带图片） | `/画 图生视频 让画面动起来` |

> 也可使用别名：`/画 文生图`、`/画 文生视频`。

## 注意
- 图生图 / 图生视频 需要在同一条消息内附带一张图片（部分平台支持回复图片+指令，请按平台实际能力使用）。
- 媒体文件保存在 `data/imagegen/` 下。
- 视频生成耗时较长，请耐心等待。

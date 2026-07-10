# AstrBot 生图插件开发文档

> 插件名：`astrbot_plugin_imagegen`
> 目标：在 AstrBot 中通过指令调用多种图像/视频生成接口。

---

## 1. AstrBot 插件开发要点（v4 新版 API）

AstrBot 在 v4 之后使用基于 `Star` 类的装饰器风格 API，旧的 `run()/info()` 写法已废弃。

### 1.1 插件目录结构（必须是 git 仓库）
```
astrbot_plugin_imagegen/
├── main.py              # 插件主类所在文件，文件名必须叫 main.py
├── metadata.yaml        # 插件市场展示的元信息
├── _conf_schema.json    # 配置可视化 Schema（WebUI 上自动渲染）
├── requirements.txt     # pip 依赖
└── README.md            # 指令帮助
```

### 1.2 最小实例
```python
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        '''这是 hello world 指令'''  # docstring 会被解析为指令描述
        yield event.plain_result(f"Hello, {event.get_sender_name()}!")

    async def terminate(self):
        pass
```

### 1.3 关键 API
| 用途 | API |
|---|---|
| 注册指令 | `@filter.command("name", alias={"别名1"})` |
| 注册指令组 | `@filter.command_group("g")` → `@g.command("sub")` |
| 带参指令 | 在 handler 上声明 `a: int, b: int`，AstrBot 自动解析 |
| 管理员指令 | `@filter.permission_type(filter.PermissionType.ADMIN)` |
| 发送纯文本 | `yield event.plain_result("...")` |
| 发送图片(本地/URL) | `yield event.image_result("path 或 https URL")` |
| 发送富媒体消息链 | `yield event.chain_result([Comp.Plain("..."), Comp.Image.fromURL(url)])` |
| 主动消息 | `await self.context.send_message(umo, MessageChain().message("..").file_image(p))` |
| 获取发送者 ID | `event.get_sender_id()` |
| 获取会话标识 | `event.unified_msg_origin` |
| 插件配置 | `__init__(self, context, config: AstrBotConfig)` 自动注入 |
| 日志 | `from astrbot.api import logger` |

### 1.4 消息链与媒体
- `event.message_obj.message` 是入站消息链 `List[BaseMessageComponent]`，里面可能是 `Comp.Image`、`Comp.Plain` 等。
- 图片组件可拿到 `file` 或 `url`：
  - `Comp.Image.fromURL(url=...)` / `Comp.Image.fromFileSystem(path=...)`
  - 入站的 `Comp.Image` 通常带 `.file`（可能是 URL、base64://、file://或本地路径），用 `.url` 等属性可能存在，注意取值前判断。
- 视频：`Comp.Video.fromURL(url=...)` / `Comp.Video.fromFileSystem(path=...)`。

### 1.5 持久化要求（官方强制规则）
1. 持久化数据存到 `data/` 目录下，**不要**存插件自身目录，防止更新覆盖。
2. 网络请求用异步库 `aiohttp` / `httpx`，**不要**用 `requests`。
3. 良好错误处理，不要让插件因一个异常崩溃。
4. 提交前用 `ruff` 格式化代码。

---

## 2. 本插件设计

### 2.1 外部接口（四类，按 OpenAI 兼容风格适配）
| 模式 | 调用路径 | 返回 | 用途 |
|---|---|---|---|
| 文生图 image-generation | `POST {base}/v1/images/generations` | `data[0].url` 或 base64 | 文字→图片 |
| 图生图/图编辑 image-edits | `POST {base}/images/edits`（multipart） | `data[0].url` | 图片+文字→图片 |
| 文生视频(走 chat) openai | `POST {base}/v1/chat/completions` | 文本中含图片/视频 URL，或 `choices` 内媒体 | 文字→视频/图片(走对话模型) |
| 文生视频 openai-video | `POST {base}/v1/video/generations` | `data[0].url`(视频) | 文字→视频 |

> "openai (/v1/chat/completions)" 适配用于会话式多模态模型：发提示词，在回复文本里解析出媒体 URL（图片或视频），常见于某些聚合中转。我们把 `text-to-video` 走这里，并把 `image-to-video` 也优先走 openai-video 接口或 image-edits 接口取视频。

### 2.2 指令设计
插件以一个指令组 `画` 组织：

```
/画 help                          -- 帮助
/画 文 <prompt>                    -- 文生图（走 image-generation）
/画 图 <prompt>                    -- 图生图（需要回复/附带一张图片，走 image-edits）
/画 视频 <prompt>                  -- 文生视频（走 openai-video 或 openai chat）
/画 图生视频 <prompt>              -- 需要附带一张图片，图生视频
/画 设置 <adapter> <base> <key> <model>  --（管理员）运行时改适配器，非必需
```

附带图片的识别策略：handler 内扫描 `event.message_obj.message` 中的 `Comp.Image`；若当前消息里没有，尝试读取上一条消息（通过 `event` 不易获得历史，简化为：要求用户在同一条消息里带图。未来可扩展）。

### 2.3 配置项（`_conf_schema.json`）
为每个适配器独立配置 `base_url` / `api_key` / `model` / `extra`，外加全局开关。

```
adapter_image_generation: { base_url, api_key, model, size, n }
adapter_image_edits:       { base_url, api_key, model, mask },
adapter_openai_chat:       { base_url, api_key, model, system_prompt, parse_media }
adapter_openai_video:      { base_url, api_key, model, seconds }
media_download: { enabled, proxy, save_dir }
```

### 2.4 媒体落盘与发送流程
1. 接口返回图片/视频 URL（或 base64）。
2. 用 `aiohttp` 下载到 `data/imagegen/<session>/<ts>.ext`。
3. `yield event.image_result(path)` 或 `yield event.chain_result([Comp.Video.fromFileSystem(path)])`。
4. 失败：`yield event.plain_result("❌ ...")` 并 `event.stop_event()`。

### 2.5 文件结构
```
astrbot_plugin_imagegen/
├── main.py
├── adapters.py        # 四个适配器实现
├── media.py           # 下载、解析 URL/base64、保存到 data
├── _conf_schema.json
├── metadata.yaml
├── requirements.txt
└── README.md
```

---

## 3. 接口适配细节

### 3.1 image-generation `/v1/images/generations`
请求体（JSON）：
```json
{ "model": "...", "prompt": "...", "n": 1, "size": "1024x1024" }
```
Header: `Authorization: Bearer <key>`。
响应：`{ "data": [ { "url": "https..." } ] }` 或 `[{ "b64_json": "..." }]`。

### 3.2 image-edits `/v1/images/edits`
multipart/form-data：
- `image`: 输入图文件
- `prompt`: 编辑指令
- `model`, `n`, `size`, `mask`(可选)
响应同上。是否输出视频取决于提供方；若返回 url 后缀为 `.mp4` 判定为视频。

### 3.3 openai `/v1/chat/completions`（用于文生视频/多模态对话）
请求体：
```json
{ "model": "...", "messages": [ {"role":"system","content":"..."}, {"role":"user","content":"<prompt>"} ] }
```
响应解析：取 `choices[0].message.content`，用正则提取其中的 http(s) 图片/视频 URL（按扩展名区分 `png|jpg|jpeg|webp` → 图片，`mp4|mov|webm` → 视频）。部分中转会把 URL 包在 markdown `![](...)` 或裸 URL 中，都需兼容。

### 3.4 openai-video `/v1/video/generations`
请求体（各家略有差异，做兜底）：
```json
{ "model": "...", "prompt": "...", "seconds": 8 }
```
响应：`{ "data": [ { "url": "https://.../*.mp4" } ] }` 或 `{ "video_url" / "url" }`。统一在一个 `extract_media_from_json()` 里兜多种字段。

### 3.5 兜底与统一
`media.py::extract_media(resp_json)` 统一从上述四种响应里抠出 `(kind, value)`，`kind ∈ {image, video}`，`value` 为 URL 或 `data:image/...;base64,...`。下游只关心 kind+value。

## 4. 错误处理与超时
- 每个请求 `aiohttp.ClientTimeout(total=180)`（视频生成可能较慢）。
- HTTP 非 2xx：记录 body 前 500 字，回 `❌ 接口返回 <code>`。
- 解析不到媒体：回 `❌ 响应中未找到图片/视频`。
- 网络异常 `aiohttp.ClientError`：回 `❌ 网络错误: <e>`。

## 5. 依赖
- `aiohttp`（异步 HTTP，符合 AstrBot 规范）

## 6. 回归测试

测试使用最小 AstrBot stub，不需要安装完整 AstrBot，也不会请求真实供应商：

```powershell
python -m pytest -q
```

当前回归范围包括文生图、图生图、文生视频、图生视频、提示词数量解析、
Seedream 水印、视频任务轮询、多媒体逐条发送、白名单和上一张图片缓存隔离。
`pytest` 只用于开发和 CI，不加入插件运行时 `requirements.txt`。

# AstrBot 多模态生图视频插件开发文档

插件名：`astrbot_plugin_imagegen`

目标：在 AstrBot v4 中提供可配置、可恢复、可故障转移的图片和视频生成能力，同时保留 OpenAI 兼容接口的直接适配模式。

## 1. 运行要求

- AstrBot `>=4.10.4`
- Python 3.10+
- `aiohttp>=3.9.0`
- `Pillow>=10.0.0`

所有网络请求均为异步调用。运行时媒体只允许写入 `data/<media.save_dir>/`，`media.save_dir` 必须是 `data/` 下的相对子目录。

## 2. 文件结构

```text
astrbot_plugin_imagegen/
|- main.py                         # AstrBot 命令、LLM Tool、兼容路径和发送边界
|- adapters.py                     # OpenAI 风格 HTTP 请求与视频提交/轮询
|- media.py                        # 媒体响应解析、下载和本地文件落盘
|- imagegen_core/
|  |- models.py                    # 统一请求、结果、媒体、引用和远端任务模型
|  |- config.py                    # providers/routing/legacy 配置归一化
|  |- provider.py                  # MediaProvider 协议
|  |- providers.py                 # OpenAI-compatible Provider
|  |- native_providers.py          # Gemini、Generic JSON、MiniMax 等原生 codec
|  |- router.py                    # 顺序、优先级、轮询、冷却和故障转移
|  |- service.py                   # 统一 GenerationService
|  |- references.py                # 当前消息、回复、转发、群文件和头像解析
|  |- intent.py                    # 自然语言能力/预设/数量规划
|  |- presets.py                   # 头像、海报、壁纸、手办、表情包等预设
|  |- jobs.py                      # 前台短等待、后台发送和远端任务恢复
|  |- legacy.py                    # legacy 四适配器请求与数量补齐
|  |- http_client.py               # 按事件循环复用 aiohttp Session
|  |- policy.py                    # KV 持久化限流和并发租约
|  |- cleanup.py                   # 受管目录临时文件清理
|  |- meme.py                      # SmartMemeSplitter
|  `- delivery.py                  # Provider 响应转统一结果
|- _conf_schema.json               # AstrBot WebUI 配置
|- metadata.yaml
|- requirements.txt
|- README.md
`- tests/                          # AstrBot stub 与离线回归测试
```

## 3. 核心模型

四种能力统一使用 `Capability`：

- `text_to_image`
- `image_to_image`
- `text_to_video`
- `image_to_video`

`GenerationRequest` 保存提示词、参考图、数量、尺寸、调用者和供应商选项。`GenerationResult` 保存多个 `MediaArtifact`、供应商尝试记录、远端任务 ID 和警告。异步视频提交后用 `GenerationHandle` 持久化 provider、task ID 和轮询元数据。

## 4. 请求流程

### 4.1 命令和 LLM Tool

1. 检查用户/群黑白名单。
2. 解析当前消息、显式回复、合并转发、群文件和头像引用。
3. Chat 一次完成图片/视频、文生/图生、预设、结果数量和是否优化判断；失败时使用本地规则。
4. 仅在语义规划明确需要扩写时执行第二次 Chat 改写。
5. 创建 `GenerationRequest` 并申请持久化额度/并发租约。
6. Router 选择供应商并执行；短等待超时后转入后台。
7. 统一发送媒体，多结果默认逐条发送。

### 4.2 图生图引用规则

- 单图编辑可显式使用“上一张/刚才那张”缓存。
- 多图和编号引用只使用当前请求明确附带或引用的来源。
- 缓存键包含会话和用户，群聊中不会跨用户复用。
- 单图与总字节数分别受 `image_reference` 配置限制。

### 4.3 后台任务

`JobManager` 使用 `asyncio.shield` 保留超过前台等待时间的任务。远端视频返回 task ID 后立即写入 AstrBot 插件 KV；插件重载只恢复轮询，不重复提交。任务完成后通过 UMO 主动发送，每个媒体单独发送并有限重试。任务状态区分 `delivered`、`delivered_with_errors` 和 `failed`，终态记录按保留时间清理。

## 5. Provider 与路由

`providers` 是 WebUI `template_list`。每个实例至少包含：

- `provider_id`
- `provider_type`
- `base_url`
- `api_key`
- `model`
- `capabilities`
- `priority`

启动时会拒绝重复 `provider_id`，避免 Router 字典静默覆盖配置。

支持协议：

- `openai_compat`：OpenAI Images、OpenAI Chat、OpenAI Video 及兼容中转
- `gemini`：Google `generateContent` 图片输出和内联参考图
- `generic_json`：可配置提交、轮询和文件结果路径

内置类型覆盖 OpenAI Images、Google Gemini、Agnes AI、xAI、MiniMax、阶跃星辰、Zai、grok2api、豆包和 SenseNova。类型表示默认 codec 和路径，不保证任意模型自动拥有所有能力；实际能力由 `capabilities` 声明。

Router 支持显式 provider 顺序、`type:<provider_type>` 占位、同类型优先级、round-robin、失败阈值、冷却时间和最大尝试数。网络错误、限流、服务端错误与媒体解析错误可切换后备供应商。远端任务已被接受后，除非明确开启终态失败切换，否则不会创建重复任务。

`compatibility.mode=legacy` 保留四个旧适配器的直接调用路径。默认使用 `router`；`providers` 留空时 Router 会从旧配置生成 legacy profiles。legacy 路径仍执行相同的持久限流、并发租约和媒体目录清理策略。

## 6. SmartMemeSplitter

表情包预设生成后依次尝试：

1. 背景颜色与黑描边连通区域自适应切分。
2. 手动 `grid_rows x grid_columns` 网格。
3. 使用提示词 Chat 配置的视觉模型返回 boxes。
4. 全部失败时保留原图。

自适应结果可输出透明 PNG，并通过最少数量、期望数量、最小面积、最小尺寸和重叠率做质量检查。蒙版使用 Pillow 通道运算，超大图片按 `analysis_max_dimension` 缩小分析但保留原始切片分辨率。切片及源文件必须位于插件受管媒体目录；重启恢复的异步表情包任务也会执行后处理。

## 7. 策略与存储

- 黑名单优先于白名单；白名单为空表示不限制。
- 周期额度和并发租约存入插件 KV，限制值为 `0` 表示关闭。
- 上一张图片索引存入插件 KV，并按 TTL 和最大内存条目数清理。
- 清理器只遍历 `data/<media.save_dir>/`，跳过活动缓存文件和未过期文件。
- 本地生成结果发送前再次校验路径，拒绝发送受管目录外文件。
- API Key 不写日志、不写任务恢复元数据、不写测试夹具。
- HTTP 请求按事件循环复用 `aiohttp.ClientSession`；参考图下载在流式读取阶段执行字节上限。

## 8. 验证

```powershell
python -m pytest -q
ruff check main.py adapters.py media.py imagegen_core tests
python -m compileall -q main.py adapters.py media.py imagegen_core tests
git diff --check
```

测试完全离线，使用最小 AstrBot stub，不请求真实供应商。覆盖命令流程、提示词语义与数量、Provider 路由、原生 codec、引用来源、后台任务恢复、限流、清理、媒体发送、Schema 配置合约和 SmartMemeSplitter 合成图片回归。

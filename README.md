# AstrBot MemoryOS

MemoryOS 是一个面向 AstrBot 的结构化长期记忆插件。它不是简单的“聊天记录总结 + 向量检索”，而是围绕私聊、群聊、成员、会话和人格建立可审计、可删除、可检索、可门控的长期记忆系统。

English documentation: [README_EN.md](README_EN.md)

## 功能特性

- 结构化记忆条目：支持偏好、项目状态、任务状态、群事实、昵称、纠正、实体关系等类型。
- 严格作用域隔离：支持 `user_private`、`user_in_group`、`group_shared`、`session`、`persona`、`global`。
- 隐私安全默认值：私聊记忆默认不会进入群聊，群聊记忆默认不会进入私聊。
- 默认轻量存储：SQLite 元数据、SQLite FTS5 关键词检索、本地向量表，不强依赖 Milvus/FAISS/Qdrant。
- Embedding 可选增强：配置 embedding provider 后启用向量检索，未配置时自动降级为关键词检索。
- 显式记忆优先：支持 `/mem remember` 等命令；自动记忆默认保守，避免乱记群聊玩笑和敏感信息。
- 历史初始化：支持从 AstrBot 已保存的当前会话历史中批量初始化长期记忆，不需要用户额外上传聊天文件。
- 召回门控：检索后按作用域、相关性、时效、重要性和敏感度筛选，再注入当前请求。
- 动态上下文注入：通过 `req.extra_user_content_parts` 和 `.mark_as_temp()` 注入，不污染 `system_prompt`。
- Web 管理台：支持 AstrBot Pages 和独立端口访问，提供查看、搜索、新增、编辑、删除、过期、导入、导出、重建索引和任务管理。

## 安装

将本仓库目录放到 AstrBot 插件目录，并确保目录名为：

```text
AstrBot/data/plugins/astrbot_plugin_memoryos
```

插件运行数据会写入 AstrBot 数据目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_memoryos/
```

默认 SQLite 后端不需要第三方运行时依赖。当前仓库目录如果带有空格或不是 `astrbot_plugin_memoryos`，建议在安装到 AstrBot 时重命名。

## 配置

插件提供 `_conf_schema.json`，可在 AstrBot WebUI 中配置。关键配置项：

- `enabled`：启用或停用插件。
- `llm_provider_id`：用于自动抽取和 LLM 门控的模型；留空时使用当前会话模型。
- `embedding_provider_id`：用于向量检索的 embedding provider；留空时降级为关键词检索。
- `auto_memory_enabled`：是否启用自动记忆抽取。
- `private_auto_capture_level`：私聊自动记忆强度，默认 `normal`。
- `group_auto_capture_level`：群聊自动记忆强度，默认 `conservative`。
- `memory_gate_mode`：记忆门控模式，支持 `off`、`heuristic`、`llm`，默认 `heuristic`。
- `allow_private_memory_in_group`：是否允许私聊记忆进入群聊，默认关闭。
- `allow_group_memory_in_private`：是否允许群聊记忆进入私聊，默认关闭。
- `retrieval_top_k`：每轮最多注入的记忆条数。
- `injection_token_budget`：记忆上下文注入预算。
- `history_bootstrap_enabled`：是否允许从 AstrBot 当前会话历史初始化记忆。
- `history_bootstrap_max_messages`：单次历史初始化最多读取的消息数。
- `history_bootstrap_chunk_size` / `history_bootstrap_chunk_overlap`：历史消息分块大小和重叠条数。
- `history_bootstrap_min_importance` / `history_bootstrap_min_confidence`：历史初始化写入阈值，默认比普通自动记忆更严格。
- `history_bootstrap_store_raw_snapshot`：是否把 AstrBot 历史规范化保存到 `raw_messages` 便于审计。
- `standalone_web_enabled`：是否启用独立端口 Web 管理台，默认开启。
- `standalone_web_host`：独立 Web 监听地址，默认 `127.0.0.1`。
- `standalone_web_port`：独立 Web 端口，默认 `8765`。
- `standalone_web_auth_token`：访问令牌。监听非本机地址时必须填写。

## 命令

```text
/mem remember <内容>
/mem search <查询>
/mem list
/mem forget <memory_id>
/mem forget all
/mem summarize
/mem bootstrap current [数量]
/mem bootstrap dry-run [数量]
/mem bootstrap status
/mem bootstrap cancel <job_id>
/mem web status
/mem web start
/mem web stop
/mem web restart
/mem status
/mem on
/mem off
/mem export
/mem import <json>
/mem rebuild-index
```

群聊管理员命令：

```text
/mem group on
/mem group off
/mem group remember <内容>
/mem group bootstrap [数量]
/mem group bootstrap dry-run [数量]
/mem group list
/mem group forget <memory_id>
/mem group policy conservative|normal|aggressive
```

说明：

- 普通 `/mem remember <内容>` 在群聊中默认写入当前发言人的 `user_in_group`，不会自动升级成整个群的共同记忆。
- `/mem group remember <内容>` 才会写入 `group_shared`，并要求管理员权限。
- `/mem bootstrap current [数量]` 会从 AstrBot 已保存的当前会话历史中初始化记忆；如果 AstrBot 没有保存历史，会返回“无可用 AstrBot 历史”。
- `/mem bootstrap dry-run [数量]` 只预览候选记忆，不写入数据库，建议首次使用先 dry-run。
- `/mem group bootstrap [数量]` 默认要求群管理员权限，并使用更保守的群聊抽取规则。
- 群管理员权限会优先使用 AstrBot 的 `event.is_admin()` / `role` 字段判断。

## 隐私与作用域

私聊检索默认只允许：

```text
user_private
session
persona
```

群聊检索默认只允许：

```text
当前群 group_shared
当前发言人 user_in_group
session
persona
```

默认禁止：

- 在群聊中使用用户私聊记忆。
- 在私聊中使用群聊记忆。
- 把群里某个成员的偏好自动记成整个群的偏好。
- 把玩笑、调侃、临时情绪、敏感个人信息作为长期记忆。

## Web 管理台

插件包含 `pages/memoryos/`，同一套页面支持两种入口：

```text
AstrBot 插件详情页 Pages
http://127.0.0.1:8765
```

独立端口默认只监听 `127.0.0.1`。如果配置为 `0.0.0.0` 或其他非本机地址，必须设置 `standalone_web_auth_token`，访问 API 时需要：

```text
Authorization: Bearer <token>
```

或在浏览器 URL 中临时追加：

```text
http://<host>:<port>/?token=<token>
```

页面功能包括：

- 查看和搜索记忆。
- 按状态和类型过滤。
- 新增、编辑、删除、过期记忆。
- 查看最近任务。
- 触发 AstrBot 历史初始化 dry-run / 写入任务，并取消任务。
- 导入、导出记忆。
- 触发索引重建。
- 查看诊断错误和 API 返回详情。

注意：聊天内命令可以直接拿到当前 `AstrMessageEvent`，因此是历史初始化的推荐入口。WebUI 请求本身没有聊天事件上下文，页面中的历史初始化面板需要手动填写 `unified_origin` 等会话标识；不熟悉这些字段时建议使用 `/mem bootstrap current`。

新版页面会自动列出 MemoryOS 已知会话。插件会在收到 AstrBot 消息、LLM 请求、LLM 回复和 `/mem` 命令时登记当前 `unified_origin`、`session_id`、`user_id`、`group_id` 等信息；Web 管理台“历史初始化”区域可以从下拉框选择会话并自动填充字段。刚安装后如果列表为空，先在目标私聊或群聊中发送一次 `/mem status` 或任意正常对话，让插件捕获当前会话。

如果“预览”按钮点击后没有任务，请先看页面右上角提示和“诊断”面板。新版前端会先绑定按钮再刷新数据，初始化 API 失败不会导致按钮失效；后端返回的 `unified_origin` 缺失、队列不可用、端口未启动等错误都会显示在页面里。

如果 `ss -lntp | grep 8765` 没有输出，先在 AstrBot 中执行：

```text
/mem web status
/mem web start
```

`/mem status` 也会显示独立 Web 的 URL 或最近启动错误。常见原因包括：插件尚未完成 ready、配置关闭了 `standalone_web_enabled`、端口被占用、监听非本机地址但未配置 `standalone_web_auth_token`。

## 数据结构

核心表包括：

- `memory_items`：结构化长期记忆。
- `raw_messages`：原始消息审计和自动抽取依据。
- `memory_edges`：记忆之间的更新、冲突、支持等关系。
- `memory_access_log`：召回和注入日志。
- `memory_vectors`：默认 SQLite 本地向量表。
- `memory_jobs`：后台任务状态。

所有迁移都是幂等的，插件启动时自动初始化。

## 详细实现思路

MemoryOS 的核心目标是让 AstrBot 在回答前能安全地“想起”相关长期上下文，同时避免私聊泄露、群聊污染和无关记忆干扰。整体链路分成写入、整理、检索、门控、注入五个阶段。

### 1. 插件入口与生命周期

插件入口是 `main.py`，符合 AstrBot 插件规范。启动时会初始化：

- `MemoryOSConfig`：读取 `_conf_schema.json` 生成的配置。
- `IdentityResolver`：把 AstrBot 消息事件解析成统一身份。
- `SQLiteMemoryStore`：创建 SQLite 表、索引、FTS5 表和本地向量表。
- `MemoryRetriever`：负责候选召回、过滤和排序。
- `MemoryGate`：负责判断候选记忆是否真的应该进入当前请求。
- `MemoryInjector`：把最终记忆打包成 `<memory_context>`。
- `MemoryTaskQueue`：在后台处理自动抽取，避免阻塞正常回复。
- `MemoryWebAPI`：注册 WebUI 管理页所需的后端接口。

为了适配 AstrBot 通过文件路径加载插件的方式，插件内部包使用 `memoryos_core`、`memoryos_storage`、`memoryos_commands`、`memoryos_web` 这类唯一前缀，避免与 AstrBot 或其他插件的 `core`、`storage` 等通用包名冲突。

### 2. 身份与作用域模型

记忆系统最重要的不是“能不能搜到”，而是“该不该在这个场景使用”。MemoryOS 会从 `AstrMessageEvent.message_obj` 中读取：

- 平台：`platform_id`
- 机器人账号：`bot_id`
- 用户：`user_id`
- 群：`group_id`
- 会话：`session_id`
- 人格：`persona_id`
- AstrBot 统一会话来源：`unified_msg_origin`

然后生成稳定 key：

```text
private_space = <platform>:private:<user_id>
group_space = <platform>:group:<group_id>
user_in_group_key = <platform>:group:<group_id>:user:<user_id>
session_key = <platform>:session:<session_id>
persona_key = persona:<persona_id>
```

每条记忆都必须属于一个明确作用域：

- `user_private`：只属于某个用户的私聊记忆。
- `user_in_group`：某个用户在某个群里的称呼、偏好或上下文。
- `group_shared`：整个群共享的上下文或决策。
- `session`：当前会话临时状态。
- `persona`：特定人格相关记忆。
- `global`：全局记忆，默认谨慎使用。

默认检索规则：

- 私聊只检索 `user_private`、`session`、`persona`。
- 群聊只检索当前群的 `group_shared`、当前发言人的 `user_in_group`、`session`、`persona`。
- 私聊记忆默认不会进入群聊。
- 群聊记忆默认不会进入私聊。

### 3. 显式记忆优先

自动总结永远不可能完全可靠，所以 MemoryOS 把显式命令作为最高优先级：

```text
/mem remember <内容>
```

在私聊中，这会写入 `user_private`。

在群聊中，普通 `/mem remember` 默认写入当前发言人的 `user_in_group`，例如“在这个群里叫我小王”。它不会自动升级成整个群的共同记忆。

只有管理员使用：

```text
/mem group remember <内容>
```

才会写入 `group_shared`，用于群公告、群项目背景、共同决策等真正群共享的信息。

### 4. 自动抽取策略

每次 LLM 回复后，插件会保存当前用户消息和机器人回复到 `raw_messages`，同时更新短期上下文。当会话累计达到 `auto_extract_every_n_pairs` 后，后台任务会启动自动抽取：

```text
最近 N 条短期上下文
→ LLM 结构化抽取候选记忆
→ 过滤低重要性/低置信度候选
→ 去重、更新、冲突处理
→ 写入 memory_items
→ 生成 embedding 并写入 memory_vectors
```

群聊默认使用 `conservative` 策略，只保存显式记忆、明确群体决策和稳定群上下文，避免把玩笑、临时话题或单个成员的个人偏好误记成群共识。

### 5. 历史初始化（Bootstrap）

MemoryOS 支持从 AstrBot 已保存的当前 LLM 会话历史中初始化长期记忆，适合插件安装后快速把已有上下文整理成结构化记忆。这个能力只读取 AstrBot 自身的 `conversation_manager`，不会要求用户上传 QQ、微信、Telegram 或其他外部聊天记录文件。

边界很重要：

- 如果 AstrBot 或平台没有保存过去的消息，MemoryOS 不能凭空恢复历史。
- 第一版只读取当前 AstrBot 会话，不扫描平台全部会话，避免跨人、跨群、跨人格混入。
- 群聊历史初始化默认只允许管理员执行。
- dry-run 不写入记忆，只把候选结果保存在 `memory_jobs.result` 中供检查。

实现流程：

```text
context.conversation_manager
→ get_curr_conversation_id(unified_msg_origin)
→ get_conversation(unified_msg_origin, conversation_id)
→ 解析 Conversation.history
→ 规范化为 RawMessage
→ 按 chunk_size/overlap 分块
→ bootstrap 专用 LLM prompt 抽取候选
→ 独立阈值过滤和作用域校验
→ resolver 去重/合并/冲突处理
→ 写入 memory_items
→ 可选生成 embedding 和向量索引
→ memory_jobs 记录进度和预览
```

历史消息 ID 使用 `astrhist:<conversation_id>:<index>:<hash>`，同一段 AstrBot 历史重复初始化时可以保持幂等。配置 `history_bootstrap_store_raw_snapshot=true` 时，规范化后的历史会写入 `raw_messages`，用于审计和后续排查。

私聊 bootstrap 默认写入 `user_private`。群聊 bootstrap 默认只允许 `group_shared` 和明确的 `user_in_group`，禁止从群聊历史生成 `user_private`，也不会把单个群成员的一句话直接升级为群共识。

### 6. 存储设计

MemoryOS 默认使用 SQLite，而不是强制用户安装大型向量库。原因是 AstrBot 插件应尽量开箱即用，并降低插件市场发布体积。

SQLite 负责：

- 结构化元数据。
- 状态管理：`active`、`superseded`、`deleted`、`uncertain`。
- 记忆编辑、删除、过期。
- 访问日志和审计。
- FTS5 关键词检索。
- 本地向量表。

Embedding provider 可用时，会把记忆文本写入本地向量表；不可用时插件不会失败，而是降级为关键词检索。`/mem status` 会显示当前是否处于 embedding 降级状态。

### 7. 检索与排序

用户发起新请求时，插件在 `on_llm_request` 中执行检索：

```text
解析当前身份
→ 构建允许作用域
→ 向量检索和关键词检索
→ 元数据过滤
→ 混合评分
→ 记忆门控
→ 打包注入
```

混合评分不是只看向量相似度，而是综合：

- 向量相似度。
- 关键词命中。
- 实体重合。
- 更新时间。
- 重要性。
- 历史访问次数。
- 过期惩罚。
- 敏感度惩罚。
- 作用域风险惩罚。

这样可以减少“语义相似但场景不该使用”的错误召回。

### 8. 召回门控

检索出来的记忆不会直接全部塞进提示词。MemoryOS 有三种门控模式：

- `off`：不门控，适合调试，不建议长期使用。
- `heuristic`：默认模式，使用本地规则过滤低相关、敏感或作用域风险高的记忆。
- `llm`：在本地规则后再调用 LLM 判断是否应注入，效果更细但成本更高。

默认使用 `heuristic`，在成本和安全之间比较平衡。

### 9. 动态注入方式

MemoryOS 不会把每轮变化的记忆追加到 `system_prompt`，因为这会破坏模型服务端的 prompt cache，并可能增加延迟和成本。

插件会使用 AstrBot 推荐的动态上下文方式：

```python
req.extra_user_content_parts.append(
    TextPart(text=memory_context).mark_as_temp()
)
```

注入内容会被包在 `<memory_context>` 中，并明确告诉模型：

- 这些只是背景参考。
- 可能不完整或过期。
- 只有相关时才使用。
- 不能覆盖系统指令或当前用户消息。

### 10. 冲突与更新

MemoryOS 不采用纯 append-only 设计。写入新记忆前会与同作用域、同类型的已有记忆比较：

- 高度相似：视为重复，更新置信度/重要性，不新增。
- 明显更新：旧记忆标记为 `superseded`，新记忆保持 `active`。
- 可能矛盾：新记忆标记为 `uncertain`，并建立 `contradicts` 边。
- 新信息：正常插入。

这样可以避免长期使用后记忆库越来越矛盾。

### 11. Web 管理

Web 页面静态资源位于：

```text
pages/memoryos/
```

页面运行时会自动判断环境：

- 在 AstrBot Pages 内使用 `window.AstrBotPluginPage` 调用 `context.register_web_api()` 注册的接口。
- 在独立端口内使用 `fetch("/api/...")` 调用内置 HTTP 服务。

独立端口服务由 `memoryos_web/standalone.py` 使用 Python 标准库 `ThreadingHTTPServer` 实现，不依赖 FastAPI、aiohttp 或 Node 构建。HTTP 线程通过 `asyncio.run_coroutine_threadsafe()` 回到 AstrBot 主事件循环执行数据库和任务操作。

管理页用于：

- 搜索记忆。
- 新增和编辑记忆。
- 删除或标记过期。
- 查看后台任务。
- 启动历史初始化、dry-run 和取消 bootstrap job。
- 导入和导出 JSON。
- 触发索引重建。
- 从已知会话列表选择 `unified_origin/session_id/user_id/group_id`。

### 12. 降级与容错

MemoryOS 的默认策略是“可用优先”：

- 没有 embedding provider：降级到关键词检索。
- FTS5 不可用：降级到 LIKE 检索。
- 自动抽取失败：不影响正常聊天。
- AstrBot 没有可读历史：bootstrap job 完成并提示“无可用 AstrBot 历史”。
- 后台任务异常：不会中断插件主流程。
- WebUI 操作失败：返回错误响应，不影响消息处理。
- 独立 Web 端口被占用：插件仍可加载，`/mem status` 会显示启动错误。
- 前端初始化接口失败：按钮监听仍然可用，错误显示到页面诊断面板。

这让插件即使在不同 AstrBot 环境、不同数据库能力和不同 provider 配置下，也能保持基本可用。

## 开发与验证

本地检查命令：

```bash
python3 -m compileall .
python3 -m pytest
python3 -m ruff check .
```

当前项目核心模块保持 Python 3.9 兼容，便于本地测试；近期 AstrBot 版本可能要求更高 Python 版本，实际部署请以 AstrBot 当前运行环境为准。

如果本机没有安装 `pytest` 或 `ruff`，至少可以先运行：

```bash
python3 -m compileall .
python3 -m json.tool _conf_schema.json >/dev/null
```

## 发布说明

- 插件名：`astrbot_plugin_memoryos`
- 展示名：`MemoryOS`
- 版本：`1.0.0`
- AstrBot 版本要求：`>=4.23.1,<5`
- 默认发布体积远低于 AstrBot 插件市场 16 MB 限制。

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
- 召回门控：检索后按作用域、相关性、时效、重要性和敏感度筛选，再注入当前请求。
- 动态上下文注入：通过 `req.extra_user_content_parts` 和 `.mark_as_temp()` 注入，不污染 `system_prompt`。
- WebUI 管理页：支持查看、搜索、新增、编辑、删除、过期、导入、导出、重建索引和查看任务状态。

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

## 命令

```text
/mem remember <内容>
/mem search <查询>
/mem list
/mem forget <memory_id>
/mem forget all
/mem summarize
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
/mem group list
/mem group forget <memory_id>
/mem group policy conservative|normal|aggressive
```

说明：

- 普通 `/mem remember <内容>` 在群聊中默认写入当前发言人的 `user_in_group`，不会自动升级成整个群的共同记忆。
- `/mem group remember <内容>` 才会写入 `group_shared`，并要求管理员权限。
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

## WebUI 管理页

插件包含 `pages/memoryos/`，可在 AstrBot WebUI 的插件详情页打开 MemoryOS 页面。页面功能包括：

- 查看和搜索记忆。
- 按状态和类型过滤。
- 新增、编辑、删除、过期记忆。
- 查看最近任务。
- 导入、导出记忆。
- 触发索引重建。

## 数据结构

核心表包括：

- `memory_items`：结构化长期记忆。
- `raw_messages`：原始消息审计和自动抽取依据。
- `memory_edges`：记忆之间的更新、冲突、支持等关系。
- `memory_access_log`：召回和注入日志。
- `memory_vectors`：默认 SQLite 本地向量表。
- `memory_jobs`：后台任务状态。

所有迁移都是幂等的，插件启动时自动初始化。

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


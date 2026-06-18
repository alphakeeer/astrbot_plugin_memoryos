
# 1. 插件定位

这个插件的目标不是简单“聊天总结存 Milvus”，而是：

```text
AstrBot 消息事件
→ 识别私聊 / 群聊 / 发言人 / session / persona
→ 短期上下文缓存
→ 显式记忆 / 自动记忆候选抽取
→ 结构化长期记忆写入
→ 后续请求前检索相关记忆
→ 经过权限、时效、相关性门控
→ 注入当前 LLM 请求
```

AstrBot 的插件体系正好适合这个设计。官方文档里 `AstrMessageEvent` 包含 `session_id`、`group_id`、`sender`、`message_str` 等消息字段，这些正好可以用来做私聊、群聊、发言人和会话隔离。([AstrBot][1])

你的插件可以叫：

```text
astrbot_plugin_memoryos
```

或者更轻量一点：

```text
astrbot_plugin_ltm
astrbot_plugin_deepmemory
astrbot_plugin_context_memory
```

我下面先叫它 **MemoryOS**。

---

# 2. 总体架构

推荐拆成 8 个核心模块。

```text
MemoryOSPlugin
├── IdentityResolver          # 解析用户、群、session、persona
├── ShortTermContextManager   # 当前会话短期上下文
├── MemoryExtractor           # LLM 抽取记忆候选
├── MemoryStore               # SQLite/PostgreSQL 元数据存储
├── VectorStore               # Milvus / Qdrant / FAISS / pgvector
├── MemoryResolver            # 去重、合并、冲突、过期
├── MemoryRetriever           # 检索、过滤、重排
├── MemoryInjector            # 注入 LLM 请求
└── Admin / Command / WebUI   # 管理命令与面板
```

核心请求链路：

```text
用户消息进入 AstrBot
    ↓
on_llm_request
    ↓
解析当前身份与作用域
    ↓
检索相关长期记忆
    ↓
过滤 / 重排 / 门控
    ↓
作为临时动态上下文注入 req.extra_user_content_parts
    ↓
LLM 回复
    ↓
on_llm_response 或 on_agent_done
    ↓
记录本轮对话
    ↓
后台抽取 / 更新长期记忆
```

这里有一个重要实现点：AstrBot 文档明确建议，动态上下文不要每轮追加到 `system_prompt`，因为会破坏 prompt cache、增加价格和首 token 延迟；动态记忆更适合放到 `req.extra_user_content_parts`，必要时用 `.mark_as_temp()` 标记为本轮临时内容。([AstrBot][2])

所以你的插件不应该优先用：

```python
req.system_prompt += memory_text
```

而应该用类似：

```python
req.extra_user_content_parts.append(
    TextPart(text=memory_context).mark_as_temp()
)
```

---

# 3. 插件生命周期设计

## 3.1 初始化阶段

在插件加载时做这些事情：

```text
1. 读取配置
2. 初始化 LLM provider
3. 初始化 embedding provider
4. 初始化 SQLite / PostgreSQL
5. 初始化向量库
6. 检查 embedding 维度
7. 创建表和索引
8. 启动后台任务队列
9. 注册命令
```

AstrBot 支持插件配置通过 `_conf_schema.json` 定义，并可以在管理面板可视化配置，这很适合放 memory 插件的 provider、阈值、开关、top-k 等参数。([AstrBot][3])

推荐配置项：

```json
{
  "enabled": true,
  "llm_provider_id": "",
  "embedding_provider_id": "",
  "vector_backend": "milvus_lite",
  "db_backend": "sqlite",

  "auto_memory_enabled": true,
  "explicit_memory_enabled": true,
  "group_memory_enabled": true,
  "private_memory_enabled": true,

  "auto_extract_every_n_pairs": 5,
  "min_importance_to_store": 0.55,
  "retrieval_top_k": 8,
  "injection_token_budget": 1200,

  "use_user_scope_filter": true,
  "use_group_scope_filter": true,
  "use_persona_scope_filter": true,
  "allow_private_memory_in_group": false,
  "allow_group_memory_in_private": false,

  "enable_memory_gate": true,
  "enable_conflict_resolution": true,
  "enable_time_decay": true,

  "memory_ttl_days_for_tasks": 30,
  "memory_ttl_days_for_group_topics": 14
}
```

其中最重要的是这两个默认值：

```text
allow_private_memory_in_group = false
allow_group_memory_in_private = false
```

因为私聊和群聊天然是不同语境，默认不能互相污染。

---

# 4. 私聊与群聊的身份模型

这是整个插件最关键的部分。

你不能只用 `session_id`，因为记忆需要区分：

```text
平台
bot 账号
用户
群
session
persona
发言人
```

建议统一生成几个 key。

## 4.1 基础身份字段

```python
platform_id       # QQ / WeChat / Telegram / Discord 等
bot_id            # 当前 bot 自身 ID
user_id           # 当前发言人 ID
group_id          # 群 ID，私聊为空
session_id        # AstrBot session
persona_id        # 当前人格 / persona
unified_origin    # AstrBot unified_msg_origin
```

AstrBot 的消息对象里本身有 `session_id`、`group_id`、`sender`、`message_str` 等字段，可以作为身份解析基础。([AstrBot][1])

建议构造这些规范 key：

```text
actor_key        = "{platform}:{user_id}"
private_space    = "{platform}:private:{user_id}"
group_space      = "{platform}:group:{group_id}"
session_key      = "{platform}:session:{session_id}"
persona_key      = "{persona_id}"
```

---

## 4.2 记忆作用域 scope

每条记忆必须有 scope。

推荐 scope 类型：

| scope           | 含义                | 是否可跨场景使用       |
| --------------- | ----------------- | -------------- |
| `user_private`  | 私聊中关于某个用户的长期记忆    | 默认只在私聊用        |
| `user_in_group` | 群聊中某个成员的群内偏好/发言习惯 | 只在该群内用         |
| `group_shared`  | 整个群的共同上下文         | 只在该群内用         |
| `session`       | 当前会话短期状态          | 只在当前 session 用 |
| `persona`       | 某个人格下的设定或偏好       | 只在该 persona 用  |
| `global`        | 全局记忆，一般慎用         | 仅系统级规则         |

---

# 5. 私聊场景怎么处理

私聊最简单。

用户和 bot 一对一交互时，记忆默认属于：

```text
scope = user_private
owner_key = platform:user_id
```

例如用户在私聊中说：

```text
以后讲课件的时候，关键术语中英文对照。
```

应该存成：

```json
{
  "scope": "user_private",
  "owner_key": "qq:123456",
  "memory_type": "preference",
  "content": "用户在课程讲解场景中偏好中文解释，关键专业术语中英文对照。",
  "context_condition": "course explanation",
  "visibility": "private",
  "confidence": 0.95
}
```

私聊检索时使用：

```text
user_private memory
+ current session memory
+ 当前 persona memory
```

不要自动检索群记忆，除非用户明确说：

```text
我们之前在某某群讨论的那个项目...
```

或者用户配置允许：

```text
allow_group_memory_in_private = true
```

---

# 6. 群聊场景怎么处理

群聊复杂很多。你要避免三种错误：

```text
1. 把 A 的偏好记成整个群的偏好
2. 把群聊里的临时玩笑记成长期事实
3. 在群里泄露某个用户私聊中告诉 bot 的信息
```

所以群聊默认应该更保守。

---

## 6.1 群聊中有哪些记忆类型

群聊里建议分三类。

### 第一类：group_shared memory

整个群都可见、可用的共同上下文。

例如：

```text
这个群主要讨论 RoboMaster 机械组。
这个群正在一起做 reviewer recommendation 项目。
这个群决定周五晚上开会。
```

存储：

```json
{
  "scope": "group_shared",
  "owner_key": "qq_group:987654",
  "memory_type": "group_fact",
  "content": "该群当前主要讨论 reviewer recommendation system 项目。",
  "visibility": "group",
  "confidence": 0.86
}
```

---

### 第二类：user_in_group memory

某个成员在某个群里的偏好或上下文。

例如群里 A 说：

```text
以后在这个群里叫我小王。
```

存成：

```json
{
  "scope": "user_in_group",
  "owner_key": "qq_group:987654:user:123456",
  "memory_type": "nickname",
  "content": "用户 123456 在该群中希望被称为小王。",
  "visibility": "group_member_context",
  "confidence": 0.96
}
```

这条不能拿到其他群里用，也不能默认拿到私聊里用。

---

### 第三类：session memory

当前群聊正在进行的话题。

例如：

```text
当前群聊正在讨论今晚服务器部署失败的问题。
```

这类记忆很短期，可能只保留几小时或几天。

---

## 6.2 群聊自动记忆必须保守

群聊里不要每 10 条就机械总结长期记忆。

推荐规则：

```text
私聊：可以适度自动抽取长期偏好和项目状态
群聊：默认只自动抽取 group_shared 的显著事实，不自动建立个人画像
```

群聊自动写入条件应该更严格：

| 内容             | 是否自动记    |
| -------------- | -------- |
| 群公告、明确决定       | 可以       |
| 多人反复讨论的项目背景    | 可以       |
| 某人说“记住我在本群叫 X” | 可以       |
| 某人临时抱怨         | 不记       |
| 某人隐私信息         | 不记       |
| 玩笑、调侃、梗        | 默认不记     |
| 涉及第三方个人事实      | 默认不记或需确认 |

建议群聊只在这些触发下写长期记忆：

```text
1. 用户显式说“记住”
2. 管理员使用 /mem remember group ...
3. 多轮对话中出现明确群体决策
4. bot 被 @ 且话题持续
5. 群配置允许自动记忆
```

---

# 7. 数据库设计

不要把所有东西都塞进 Milvus content 里。建议用：

```text
SQLite / PostgreSQL：结构化元数据
Milvus / Qdrant / FAISS：向量检索
```

如果你想保持插件轻量，MVP 可以：

```text
SQLite + sqlite-fts5 + FAISS / hnswlib
```

如果你想兼容 Mnemosyne 方案，可以：

```text
SQLite + Milvus Lite
```

---

## 7.1 核心表：memory_items

```sql
CREATE TABLE memory_items (
    memory_id TEXT PRIMARY KEY,

    scope TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    visibility TEXT NOT NULL,

    platform_id TEXT,
    bot_id TEXT,
    user_id TEXT,
    group_id TEXT,
    session_id TEXT,
    persona_id TEXT,

    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    canonical_text TEXT,

    entities_json TEXT,
    tags_json TEXT,

    source_message_ids_json TEXT,
    source_summary TEXT,

    confidence REAL DEFAULT 0.8,
    importance REAL DEFAULT 0.5,
    sensitivity TEXT DEFAULT 'normal',

    status TEXT DEFAULT 'active',
    valid_from INTEGER,
    valid_to INTEGER,

    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_accessed_at INTEGER,
    access_count INTEGER DEFAULT 0
);
```

关键字段解释：

| 字段                   | 作用                                   |
| -------------------- | ------------------------------------ |
| `scope`              | 私聊、群聊、用户在群内、session、persona          |
| `owner_key`          | 这条记忆属于谁                              |
| `visibility`         | 是否可在群里展示                             |
| `memory_type`        | preference、fact、project、episode、task |
| `confidence`         | LLM 对记忆可靠性的置信度                       |
| `importance`         | 是否值得长期保存                             |
| `sensitivity`        | normal/private/sensitive             |
| `status`             | active/superseded/deleted/uncertain  |
| `valid_to`           | 过期时间                                 |
| `source_message_ids` | 可追溯来源                                |

---

## 7.2 原始消息表：raw_messages

```sql
CREATE TABLE raw_messages (
    message_id TEXT PRIMARY KEY,
    platform_id TEXT,
    bot_id TEXT,
    user_id TEXT,
    group_id TEXT,
    session_id TEXT,
    persona_id TEXT,

    role TEXT,
    content TEXT,
    message_type TEXT,

    timestamp INTEGER,
    processed_for_memory INTEGER DEFAULT 0
);
```

原始消息用于证据追溯，不建议直接长期注入 prompt。

---

## 7.3 记忆关系表：memory_edges

先不要一上来上 Neo4j。用关系表就够了。

```sql
CREATE TABLE memory_edges (
    edge_id TEXT PRIMARY KEY,
    source_memory_id TEXT,
    target_memory_id TEXT,
    relation_type TEXT,
    confidence REAL,
    created_at INTEGER
);
```

关系类型可以有：

```text
same_topic
updates
contradicts
supports
belongs_to_project
mentions_entity
same_user
same_group
```

---

## 7.4 访问日志表：memory_access_log

```sql
CREATE TABLE memory_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT,
    request_id TEXT,
    session_id TEXT,
    used_in_prompt INTEGER,
    score REAL,
    timestamp INTEGER
);
```

这个表很有用。以后你可以分析：

```text
哪些记忆经常被召回？
哪些记忆从来没用过？
哪些记忆经常导致错误回答？
```

---

# 8. 向量库 schema

如果继续用 Milvus，可以建一个 collection：

```text
memory_vectors
```

字段：

| 字段            | 类型                    |
| ------------- | --------------------- |
| `memory_id`   | VarChar / primary key |
| `embedding`   | FloatVector           |
| `scope`       | VarChar               |
| `owner_key`   | VarChar               |
| `group_id`    | VarChar               |
| `user_id`     | VarChar               |
| `persona_id`  | VarChar               |
| `memory_type` | VarChar               |
| `status`      | VarChar               |
| `updated_at`  | Int64                 |

向量库负责：

```text
embedding top-k search
```

SQLite/PostgreSQL 负责：

```text
权限过滤
状态过滤
过期过滤
source 追溯
编辑删除
统计分析
```

不要把复杂业务逻辑全部压到 Milvus 里。

---

# 9. LLM API 的使用位置

你的插件有 LLM API 和 embedding API。LLM 不应该只用于总结，而应该用于 5 个地方。

---

## 9.1 显式记忆解析

用户说：

```text
记住：以后在这个群里叫我火子。
```

LLM 解析成结构化 JSON：

```json
{
  "should_store": true,
  "scope": "user_in_group",
  "memory_type": "nickname",
  "content": "用户在当前群中希望被称为火子。",
  "confidence": 0.98
}
```

---

## 9.2 自动记忆候选抽取

后台从最近 N 条消息里抽取：

```json
[
  {
    "should_store": true,
    "memory_type": "project_state",
    "scope": "user_private",
    "content": "用户正在调研 AI 长期记忆系统，并计划实现为 AstrBot 插件。",
    "importance": 0.85,
    "confidence": 0.91
  }
]
```

---

## 9.3 冲突判断

新候选记忆：

```text
用户现在使用 DeepSeek V4 做 summary LLM。
```

旧记忆：

```text
用户使用 GPT 做 summary LLM。
```

LLM 判断：

```json
{
  "relation": "updates",
  "action": "supersede_old",
  "reason": "新记忆描述了当前配置，旧记忆可能已过时。"
}
```

---

## 9.4 召回门控

候选记忆检索出来后，让 LLM 判断是否应该注入。

输入：

```text
当前问题：帮我写一封给导师的英文邮件。
候选记忆：用户喜欢 DJ 和 Coachella。
```

输出：

```json
{
  "use": false,
  "reason": "与当前写邮件任务无关。"
}
```

---

## 9.5 记忆压缩

如果一个项目已经有 30 条相关记忆，可以周期性压缩成 project state：

```text
项目：AstrBot MemoryOS 插件
当前目标：实现群聊/私聊长期记忆
已定方案：结构化记忆 + embedding 检索 + scope 过滤 + 动态注入
待解决：schema、群聊权限、记忆管理面板
```

---

# 10. Embedding API 的使用位置

Embedding 主要用于：

```text
1. 记忆写入时生成 memory embedding
2. 查询时生成 query embedding
3. 去重时查找相似旧记忆
4. 项目聚类 / topic 聚类
5. 重建索引
```

注意两个工程问题：

## 10.1 embedding 模型变更要重建索引

配置里要保存：

```text
embedding_provider_id
embedding_model_name
embedding_dimension
embedding_version
```

如果用户换了 embedding 模型，插件应该提示：

```text
当前 embedding 维度与历史索引不一致，需要 rebuild-index。
```

---

## 10.2 同一条记忆最好有两个文本

```text
content：中文自然语言，给用户和模型看
canonical_text：中英混合或标准化表达，用来 embedding
```

例如：

```json
{
  "content": "用户讲课件时希望中文解释，关键术语中英文对照。",
  "canonical_text": "The user prefers course slide explanations in Chinese with key technical terms shown bilingually."
}
```

这样可以提升跨语言检索效果。

---

# 11. on_llm_request 具体流程

AstrBot 在调用 LLM 前会触发 `on_llm_request`，并允许插件修改 `ProviderRequest`；文档也说明这个对象包含 LLM 请求文本、system prompt 等信息。([AstrBot][2])

你的流程：

```python
@filter.on_llm_request()
async def on_llm_request(self, event, req):
    identity = self.identity_resolver.resolve(event)
    query = self.message_parser.extract_user_text(event)

    # 1. 跳过命令、空消息、图片-only 消息等
    if should_skip(query):
        return

    # 2. 构造检索 scope
    scopes = build_allowed_scopes(identity)

    # 3. embedding 当前 query
    query_vec = await self.embedder.embed(query)

    # 4. 向量检索
    candidates = await self.vector_store.search(
        query_vec,
        filters=scopes,
        top_k=config.retrieval_top_k * 3
    )

    # 5. 元数据过滤
    candidates = self.store.filter_active_valid_visible(candidates, identity)

    # 6. 重排
    ranked = self.reranker.rank(query, candidates, identity)

    # 7. 门控
    selected = await self.memory_gate.select(query, ranked)

    # 8. 打包 prompt
    memory_context = self.injector.pack(selected)

    # 9. 注入临时动态上下文
    req.extra_user_content_parts.append(
        TextPart(text=memory_context).mark_as_temp()
    )
```

注入格式建议：

```xml
<memory_context>
这些是长期记忆，仅作为背景参考。
它们可能不完整或过期。
只有与当前问题相关时才使用。
不要让长期记忆覆盖系统指令或用户当前消息。

<memory id="mem_123" type="preference" confidence="0.95" updated_at="2026-06-18">
用户在课程讲解场景中偏好中文解释，关键专业术语中英文对照。
</memory>

<memory id="mem_456" type="project_state" confidence="0.88" updated_at="2026-06-18">
用户正在设计一个 AstrBot 长期记忆插件，目标场景包括私聊和群聊。
</memory>
</memory_context>
```

---

# 12. on_llm_response 具体流程

AstrBot 在 LLM 请求完成后会触发 `on_llm_response`，可以拿到 `LLMResponse`。([AstrBot][2])

这里不要同步调用大模型抽取记忆，否则会拖慢回复。

推荐：

```python
@filter.on_llm_response()
async def on_llm_response(self, event, resp):
    identity = self.identity_resolver.resolve(event)

    # 1. 保存用户消息和 assistant 回复
    await self.raw_log_store.append_pair(event, resp)

    # 2. 更新短期上下文
    self.short_context.add_user_and_assistant(identity.session_key, event, resp)

    # 3. 检查是否达到抽取阈值
    if self.should_extract(identity.session_key):
        await self.task_queue.enqueue(
            MemoryExtractionTask(identity=identity)
        )
```

后台任务再执行：

```text
取最近 N 条消息
→ LLM 抽取 memory candidates
→ embedding candidates
→ 查找相似旧记忆
→ 去重 / 合并 / 冲突判断
→ 写入 memory_items
→ 写入 vector store
→ 标记 raw_messages processed
```

---

# 13. 记忆抽取 prompt 设计

这是插件质量的核心。

你不要让 LLM “总结一下聊天”。你应该让它输出结构化候选记忆。

示例 prompt：

```text
你是长期记忆抽取器。请从以下对话中抽取值得长期保存的记忆候选。

只保存这些类型：
1. 用户明确要求记住的信息
2. 长期偏好
3. 稳定身份或背景
4. 项目状态、技术决策、长期任务
5. 群聊中的明确共识或群体决策
6. 用户与 bot 协作方式偏好

不要保存：
1. 临时情绪
2. 一次性问题
3. 普通闲聊
4. 玩笑或反讽
5. 敏感个人信息
6. 没有证据的推断
7. 群聊中某人的个人画像，除非对方明确要求记住

请输出 JSON 数组。
```

输出 schema：

```json
[
  {
    "should_store": true,
    "scope": "user_private | user_in_group | group_shared | session",
    "memory_type": "preference | project_state | project_fact | group_fact | nickname | procedure | episode",
    "content": "...",
    "canonical_text": "...",
    "entities": ["..."],
    "tags": ["..."],
    "confidence": 0.0,
    "importance": 0.0,
    "validity": {
      "valid_from": null,
      "valid_to": null
    },
    "reason": "为什么值得记"
  }
]
```

---

# 14. 群聊记忆抽取 prompt 要更严格

群聊专用规则：

```text
如果当前是群聊：
- 不要把单个成员的偏好升级为整个群偏好。
- 不要从玩笑、调侃、表情包中抽取长期记忆。
- 不要保存成员的敏感个人信息。
- 只有当用户明确说“记住我...”时，才保存 user_in_group 记忆。
- 只有当多名成员达成明确共识，或管理员明确要求时，才保存 group_shared 记忆。
```

比如群聊：

```text
A：以后叫我火子。
B：哈哈火子哥。
```

应该保存：

```json
{
  "scope": "user_in_group",
  "owner_key": "current_group:A",
  "memory_type": "nickname",
  "content": "A 在当前群中希望被称为火子。"
}
```

不应该保存：

```text
这个群的人都喜欢叫用户火子哥。
```

---

# 15. 显式记忆命令设计

必须支持命令，因为自动记忆永远不可靠。

推荐命令：

```text
/mem remember <内容>
/mem remember group <内容>
/mem remember me <内容>
/mem search <query>
/mem list
/mem list group
/mem forget <memory_id>
/mem forget all
/mem summarize
/mem status
/mem off
/mem on
/mem export
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

AstrBot 支持管理员权限过滤器，所以 group-level 管理命令应该加 admin permission。官方文档示例里可以用 `@filter.permission_type(filter.PermissionType.ADMIN)` 限制指令权限。([AstrBot][2])

---

# 16. 记忆检索规则

私聊检索：

```text
允许：
- 当前用户 user_private
- 当前 session
- 当前 persona
- 用户显式绑定的 project memory

默认禁止：
- group_shared
- 其他用户 memory
```

群聊检索：

```text
允许：
- 当前 group_shared
- 当前 speaker 的 user_in_group
- 当前 session
- 当前 persona

默认禁止：
- speaker 的 user_private
- 其他群的 group_shared
- 其他成员的 user_in_group
```

特殊情况：

如果用户在群里问：

```text
你还记得我之前私聊说的那个项目吗？
```

也不建议直接暴露私聊记忆。可以回答：

```text
我可以使用你的私聊记忆来辅助回答，但这可能会在群聊中暴露私聊上下文。请确认是否允许。
```

这对隐私非常重要。

---

# 17. 召回打分设计

不要只用向量相似度。

推荐最终分数：

```text
final_score =
  0.45 * vector_similarity
+ 0.15 * keyword_score
+ 0.15 * entity_overlap
+ 0.10 * recency_score
+ 0.10 * importance_score
+ 0.05 * access_score
- 0.20 * stale_penalty
- 0.30 * sensitivity_penalty
- 0.40 * scope_risk_penalty
```

其中：

```text
vector_similarity：embedding 相似度
keyword_score：关键词命中
entity_overlap：实体重合
recency_score：最近更新时间
importance_score：重要性
access_score：历史使用频率
stale_penalty：过期惩罚
sensitivity_penalty：敏感信息惩罚
scope_risk_penalty：私聊/群聊边界风险
```

MVP 可以先不用 LLM reranker，先用规则打分。等基本可用后，再加 LLM gate。

---

# 18. 记忆门控器

这是区分高级插件和普通 RAG 插件的核心。

检索出来的记忆必须再问：

```text
这条记忆是否真的应该进入本轮 prompt？
```

门控输入：

```json
{
  "current_query": "帮我分析 AstrBot 记忆插件设计",
  "conversation_type": "private",
  "candidate_memories": [
    {
      "id": "mem_001",
      "type": "project_state",
      "content": "用户正在设计 AstrBot 长期记忆插件。",
      "scope": "user_private"
    },
    {
      "id": "mem_002",
      "type": "hobby",
      "content": "用户喜欢 DJ。",
      "scope": "user_private"
    }
  ]
}
```

门控输出：

```json
[
  {
    "id": "mem_001",
    "use": true,
    "reason": "与当前插件设计任务直接相关。"
  },
  {
    "id": "mem_002",
    "use": false,
    "reason": "与当前任务无关。"
  }
]
```

为了省成本，可以配置：

```text
enable_memory_gate = false / heuristic / llm
```

MVP 用 heuristic；高级版本用 LLM。

---

# 19. 冲突更新机制

这一步非常重要。

不要 append-only。

候选新记忆写入前，做：

```text
candidate embedding
→ 查 top-k similar existing memories
→ 判断 relation
```

relation 类型：

| relation     | 处理                          |
| ------------ | --------------------------- |
| `duplicate`  | 不写入，只更新 access / confidence |
| `complement` | 合并或新增补充                     |
| `update`     | 新记忆 active，旧记忆 superseded   |
| `contradict` | 标记 uncertain，必要时询问用户        |
| `new`        | 直接写入                        |

例子：

旧记忆：

```text
用户使用 DeepSeek V4 flash 做 summary LLM。
```

新记忆：

```text
用户决定改用 GPT 模型做 summary LLM。
```

处理：

```text
旧记忆 status = superseded
新记忆 status = active
新记忆 supersedes = old_memory_id
```

---

# 20. 记忆类型设计

推荐第一版支持这些类型。

| 类型                | 说明     | 示例                   |
| ----------------- | ------ | -------------------- |
| `preference`      | 用户长期偏好 | 喜欢中文讲解，术语中英文对照       |
| `procedure`       | 协作流程偏好 | 讲课件时一部分一部分讲          |
| `project_state`   | 项目整体状态 | 正在做 AstrBot 记忆插件     |
| `project_fact`    | 项目具体事实 | 使用 LLM API 和 emb API |
| `task_state`      | 短中期任务  | 正在调试某个 bug           |
| `group_fact`      | 群聊共同事实 | 本群讨论某个项目             |
| `nickname`        | 群内称呼   | 在本群叫用户火子             |
| `episode`         | 一段对话摘要 | 今天讨论了记忆系统架构          |
| `correction`      | 用户纠正   | 用户说某个理解是错的           |
| `entity_relation` | 实体关系   | 用户参与某项目              |

---

# 21. 自动写入策略

推荐分三个等级。

## conservative

默认建议群聊使用。

```text
只保存：
- 显式 remember
- 管理员 group remember
- 非敏感、明确、稳定的群体事实
```

## normal

默认建议私聊使用。

```text
保存：
- 显式 remember
- 长期偏好
- 项目状态
- 用户反复强调的协作方式
- 当前任务阶段
```

## aggressive

不建议默认开启。

```text
会保存更多对话摘要和偏好推断
```

配置：

```json
{
  "private_auto_capture_level": "normal",
  "group_auto_capture_level": "conservative"
}
```

---

# 22. 记忆注入预算

不要把所有记忆都塞进去。

推荐：

```text
最多 5-8 条记忆
总 token 不超过 800-1500
每条记忆不超过 150-250 字
```

优先级：

```text
1. 当前任务直接相关的 project_state
2. 用户当前场景偏好
3. 最新的纠正 correction
4. 当前 session 摘要
5. 其他事实
```

如果记忆太多，先压缩：

```text
以下是与当前问题相关的长期记忆摘要：
- 用户正在设计 AstrBot 记忆插件，目标支持私聊和群聊。
- 用户希望该插件不是简单 RAG，而是具备结构化记忆、权限隔离和召回门控。
```

---

# 23. 插件目录建议

```text
astrbot_plugin_memoryos/
├── metadata.yaml
├── main.py
├── _conf_schema.json
├── requirements.txt
├── README.md
├── core/
│   ├── identity.py
│   ├── context_manager.py
│   ├── extractor.py
│   ├── resolver.py
│   ├── retriever.py
│   ├── injector.py
│   ├── gate.py
│   ├── prompts.py
│   └── scheduler.py
├── storage/
│   ├── sqlite_store.py
│   ├── vector_store_base.py
│   ├── milvus_store.py
│   ├── faiss_store.py
│   └── migrations/
├── commands/
│   ├── memory_commands.py
│   └── admin_commands.py
└── pages/
    └── dashboard/
```

AstrBot 的新插件文档建议插件仓库名以 `astrbot_plugin_` 开头，并说明插件目录需要 `metadata.yaml` 供 AstrBot 识别元数据。([AstrBot][4])

---

# 24. main.py 结构

大概长这样：

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart

@register(
    "memoryos",
    "your_name",
    "Structured long-term memory system for AstrBot",
    "0.1.0"
)
class MemoryOSPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config

        self.identity_resolver = IdentityResolver(config)
        self.short_context = ShortTermContextManager(config)
        self.store = SQLiteMemoryStore(config)
        self.vector_store = MilvusVectorStore(config)
        self.extractor = MemoryExtractor(context, config)
        self.resolver = MemoryResolver(config)
        self.retriever = MemoryRetriever(self.store, self.vector_store, config)
        self.injector = MemoryInjector(config)
        self.task_queue = MemoryTaskQueue(config)

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        await self.store.init()
        await self.vector_store.init()
        await self.task_queue.start()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        identity = self.identity_resolver.resolve(event)
        query = self.extract_query(event)

        if not query:
            return

        memories = await self.retriever.retrieve(query, identity)

        memory_context = self.injector.pack(memories)
        if memory_context:
            req.extra_user_content_parts.append(
                TextPart(text=memory_context).mark_as_temp()
            )

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        identity = self.identity_resolver.resolve(event)

        await self.store.save_raw_turn(event, resp, identity)
        self.short_context.add_turn(identity.session_key, event, resp)

        if self.short_context.should_extract(identity.session_key):
            await self.task_queue.enqueue_extract(identity)
```

具体 API 名称要以你本地 AstrBot 版本为准，但整体 hook 位置是这个设计。

---

# 25. WebUI / 管理面板

第一版可以先做命令，不做页面。

但最终最好做页面，因为记忆系统一定要可审计。

页面功能：

```text
1. 查看全部记忆
2. 按 user / group / session / type 过滤
3. 搜索记忆
4. 编辑记忆
5. 删除记忆
6. 标记过期
7. 查看来源消息
8. 查看最近召回记录
9. 重建索引
10. 导出 / 导入
```

你提到的 Mnemosyne 有 AdminPanelServer，这个方向是对的。但在 AstrBot 里更自然的是接插件 Pages 或 WebUI 管理能力，而不是单独起一个完全独立的服务。

---

# 26. MVP 实现路线

不要一开始就做全。

我建议按 4 个版本推进。

## V0：基础可跑

目标：先跑通记忆闭环。

```text
- 私聊记忆
- /mem remember
- embedding 写入
- vector search
- on_llm_request 注入
- /mem search
- /mem forget
```

暂时不做自动总结、不做群聊复杂逻辑。

---

## V1：私聊自动记忆

加入：

```text
- on_llm_response 后台抽取
- 结构化 memory candidate
- 去重
- 简单冲突处理
- preference / project_state / episode 三种类型
```

---

## V2：群聊支持

加入：

```text
- group_shared memory
- user_in_group memory
- 群管理员命令
- 群聊 conservative auto capture
- 禁止 private memory 注入群聊
```

这是你插件真正区别于普通 memory 插件的关键。

---

## V3：高级检索和门控

加入：

```text
- 混合检索
- time decay
- importance score
- LLM memory gate
- conflict resolver
- access log
```

---

## V4：管理面板和图谱

加入：

```text
- WebUI dashboard
- memory edge
- entity graph
- project state 聚合
- 自动压缩
- 备份 / 迁移 / rollback
```

---

# 27. 最小可用版本的具体功能范围

我建议你的第一个版本只做这些：

```text
1. 支持私聊 / 群聊身份解析
2. 支持 /mem remember
3. 支持 /mem search
4. 支持 /mem forget
5. 支持 on_llm_request 检索注入
6. 支持 on_llm_response 保存 raw log
7. 支持每 5 轮后台抽取记忆
8. 默认群聊只保存显式记忆
```

不要一上来做：

```text
复杂图谱
复杂 WebUI
多模型 rerank
自动长期群画像
超复杂权限系统
```

因为记忆插件最容易失败在“记太多、用太多、串上下文”。

---

# 28. 这个插件最需要避免的坑

## 坑 1：群聊记忆污染私聊

群聊里的玩笑、称呼、上下文，不应该默认进入私聊。

---

## 坑 2：私聊记忆泄露到群聊

这是最严重的问题。

默认规则必须是：

```text
私聊记忆永远不在群聊中使用，除非用户明确授权。
```

---

## 坑 3：把动态记忆加到 system_prompt

AstrBot 文档已经提醒，动态内容追加到 `system_prompt` 容易破坏缓存、增加成本；长期记忆、检索摘要这类动态内容应优先通过额外用户内容块注入，或作为工具按需调用。([AstrBot][2])

---

## 坑 4：只 append 不 update

append-only 会让记忆越来越矛盾。

必须有：

```text
duplicate
update
conflict
supersede
delete
expire
```

---

## 坑 5：LLM 总结幻觉

解决方式：

```text
每条记忆保留 source_message_ids
低置信度记忆不自动使用
用户可以查看和删除
重要记忆尽量由显式 remember 产生
```

---

## 坑 6：embedding 模型切换

一换 embedding provider，历史向量可能全失效。

必须做：

```text
embedding_model_id 检查
dimension 检查
rebuild-index 命令
```

---

# 29. 我建议的最终插件形态

你要做的不是“Mnemosyne 改良版”，而是：

```text
AstrBot MemoryOS：
一个面向私聊与群聊的结构化长期记忆插件。
```

核心卖点：

```text
1. 结构化记忆，不只是对话摘要
2. 私聊 / 群聊 / 成员 / persona 严格隔离
3. 显式记忆优先，自动记忆保守
4. 支持记忆更新、冲突处理、过期和删除
5. 检索后有门控，不是相似就注入
6. 动态记忆通过 extra_user_content_parts 临时注入
7. 用户和管理员可查看、搜索、删除、导出记忆
```

最终回答效果应该是：

```text
用户：继续讲我们之前说的那个 AstrBot 记忆插件。
系统检索到：
- 用户正在设计 AstrBot MemoryOS 插件
- 场景包括私聊和群聊
- 工具包括 LLM API 和 embedding API
- 用户希望它具备结构化记忆、权限隔离、召回门控
- 用户不想只做普通 Mnemosyne 摘要型 RAG

LLM 回答：
直接接着分析插件架构，而不是重新问背景。
```

这就是你要实现的目标。

[1]: https://docs.astrbot.app/en/dev/star/guides/listen-message-event.html "Handling Message Events | AstrBot"
[2]: https://docs.astrbot.app/dev/star/plugin.html "插件开发指南（旧） | AstrBot"
[3]: https://docs.astrbot.app/en/dev/star/guides/plugin-config.html "Plugin Configuration | AstrBot"
[4]: https://docs.astrbot.app/en/dev/star/plugin-new.html "AstrBot Plugin Development Guide  | AstrBot"


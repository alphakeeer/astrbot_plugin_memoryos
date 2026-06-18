可以加，而且我认为这是 **MemoryOS 第二个最该做的功能**。你现在 v1 已经有 `raw_messages`、`memory_jobs`、`MemoryExtractor`、`MemoryResolver`、`embed_and_index` 这些基础模块，所以“从历史消息初始化记忆”不需要重写一套系统，应该做成一个 **bootstrap / backfill pipeline**。

不过要先明确一个边界：

> 插件只能从“它能访问到的历史消息”里初始化记忆。
> 如果 MemoryOS 安装前 AstrBot 或平台没有保存历史消息，插件不能凭空读取 QQ/微信/Telegram 过去的聊天记录。

所以这个功能应该支持三种来源：

```text
1. MemoryOS 自己的 raw_messages 表
2. AstrBot 本身已有的会话历史 / 消息数据库，如果能访问
3. 用户手动导入的历史聊天记录文件，例如 JSON / TXT / CSV
```

---

# 1. 你现在的 v1 代码已经很适合加这个功能

从 README 看，你的 v1 已经声明了核心表：`memory_items`、`raw_messages`、`memory_edges`、`memory_access_log`、`memory_vectors` 和 `memory_jobs`，而且已有 WebUI、导入导出、索引重建等设计。

代码里实际也已经有这条正常自动记忆链路：

```text
on_llm_response
→ append_raw_turn
→ short_context.add_turn_pair
→ short_context.should_extract
→ task_queue.enqueue_extract
```

也就是说，现在自动记忆依赖的是 **短期上下文 `short_context`**，而不是直接扫数据库。`on_llm_response` 会把当前用户消息和 assistant 回复写入 `raw_messages`，然后把这一轮加入短期上下文，达到阈值后提交 `ExtractionTask`。

你的 `MemoryTaskQueue` 目前处理的是 `ExtractionTask`，它从 `short_context.snapshot()` 里取最近若干轮，然后调用 `extractor.extract_from_turns()`，再经过 resolver 和 embedding 写入存储。

所以 bootstrap 功能应该复用这条链路，只是把输入从：

```text
short_context.snapshot(...)
```

换成：

```text
history_messages / raw_messages / imported_messages
```

---

# 2. 这个功能应该叫 Bootstrap，而不是普通 summarize

我建议命名为：

```text
Memory Bootstrap
History Backfill
Memory Initialization
```

功能语义是：

> 从已有聊天历史中批量抽取长期记忆，用于初始化 MemoryOS，而不是等待未来对话慢慢积累。

命令可以设计成：

```text
/mem bootstrap current
/mem bootstrap current 200
/mem bootstrap private 500
/mem bootstrap group 1000
/mem bootstrap import
/mem bootstrap dry-run
/mem bootstrap status
/mem bootstrap cancel
```

群聊里建议：

```text
/mem group bootstrap 500
/mem group bootstrap dry-run 200
```

其中：

| 命令                           | 含义                                     |
| ---------------------------- | -------------------------------------- |
| `/mem bootstrap current 200` | 从当前 session 最近 200 条 raw message 初始化记忆 |
| `/mem bootstrap group 1000`  | 从当前群最近 1000 条消息初始化群记忆，管理员可用            |
| `/mem bootstrap private 500` | 从当前私聊最近 500 条消息初始化用户私聊记忆               |
| `/mem bootstrap dry-run 200` | 只预览候选记忆，不写入                            |
| `/mem bootstrap import`      | 从导入文件中初始化                              |
| `/mem bootstrap status`      | 查看后台任务进度                               |

---

# 3. 核心架构：History → Chunk → Extract → Resolve → Store

完整流程应该是：

```text
历史消息来源
    ↓
消息规范化
    ↓
按 session / group / user / 时间窗口分块
    ↓
LLM 批量抽取候选记忆
    ↓
过滤低价值候选
    ↓
去重 / 合并 / 冲突更新
    ↓
写入 memory_items
    ↓
生成 embedding
    ↓
写入 memory_vectors
    ↓
标记 raw_messages processed_for_memory = 1
```

这和你现有自动抽取流程基本一致，只是多了“批处理”和“历史消息读取”。

---

# 4. 最重要的问题：历史消息从哪里来？

## 4.1 来源一：MemoryOS 自己的 `raw_messages`

你现在已经有 `raw_messages` 表，而且 `SQLiteMemoryStore.append_raw_turn()` 会保存 user 和 assistant 两类消息。

现在已有方法：

```python
recent_raw_messages(session_id, limit)
```

它只按 `session_id` 取最近消息。

这对当前 session 总结够用，但 bootstrap 不够，因为 bootstrap 需要更多查询方式：

```text
按 session_id 取
按 user_id 取
按 group_id 取
按时间范围取
只取 processed_for_memory = 0 的消息
分页取
```

所以需要给 `SQLiteMemoryStore` 增加这些方法：

```python
async def raw_messages_for_session(
    self,
    session_id: str,
    limit: int = 1000,
    before_ts: int | None = None,
    after_ts: int | None = None,
    only_unprocessed: bool = True,
) -> list[RawMessage]:
    ...

async def raw_messages_for_private_user(
    self,
    platform_id: str,
    user_id: str,
    limit: int = 1000,
    only_unprocessed: bool = True,
) -> list[RawMessage]:
    ...

async def raw_messages_for_group(
    self,
    platform_id: str,
    group_id: str,
    limit: int = 2000,
    only_unprocessed: bool = True,
) -> list[RawMessage]:
    ...
```

这部分是最容易实现的。

缺点是：只能处理 **MemoryOS 安装后记录过的消息**。

---

## 4.2 来源二：AstrBot 自己的历史记录

如果 AstrBot 本身存了消息历史，那么可以写一个 adapter：

```text
AstrBotHistoryAdapter
```

负责从 AstrBot 的历史存储读取消息，再转换成 MemoryOS 的 `RawMessage`。

但是这个要看 AstrBot 当前版本到底有没有稳定公开的历史消息 API。这个地方我不建议在 v1.1 里强绑定内部数据库路径，因为 AstrBot 升级后可能会变。

推荐设计成可选适配器：

```python
class HistorySource:
    async def fetch(self, identity, options) -> list[RawMessage]:
        raise NotImplementedError

class MemoryOSRawHistorySource(HistorySource):
    ...

class AstrBotNativeHistorySource(HistorySource):
    ...

class ImportedFileHistorySource(HistorySource):
    ...
```

如果 AstrBot native history 不稳定，就先只实现 `MemoryOSRawHistorySource` 和 `ImportedFileHistorySource`。

---

## 4.3 来源三：用户导入聊天记录

这个很有必要，因为很多时候用户想初始化的是：

```text
插件安装之前的历史聊天
其他 bot 的聊天记录
QQ / 微信 / Telegram 导出的记录
```

可以支持一个标准 JSON 格式：

```json
{
  "source": "manual_import",
  "conversation_type": "private",
  "platform_id": "qq",
  "user_id": "123456",
  "group_id": "",
  "session_id": "manual:qq:private:123456",
  "messages": [
    {
      "role": "user",
      "sender_id": "123456",
      "content": "以后讲课件的时候术语中英文对照。",
      "timestamp": 1710000000000
    },
    {
      "role": "assistant",
      "sender_id": "bot",
      "content": "好的，我会这样讲。",
      "timestamp": 1710000005000
    }
  ]
}
```

这条路线适合后续做 WebUI 上传文件。

---

# 5. Bootstrap 不能一整段全塞给 LLM

历史消息可能很多，比如 500 条、5000 条。不能直接：

```text
把所有历史消息拼成一个 prompt
```

这样会遇到：

```text
上下文超长
成本过高
抽取质量下降
重复记忆很多
群聊串人
```

所以一定要 chunk。

推荐 chunk 策略：

```text
按 session / group / user 先分桶
每 20~40 条消息一个 chunk
或者每 3000~6000 tokens 一个 chunk
如果时间间隔超过 30~60 分钟，切新 chunk
如果话题明显变化，切新 chunk
```

第一版可以简单点：

```python
chunk_size_messages = 30
chunk_overlap_messages = 4
```

流程：

```text
messages[0:30]
messages[26:56]
messages[52:82]
...
```

用 overlap 是为了避免重要信息刚好卡在 chunk 边界。

---

# 6. Bootstrap 应该分两阶段：候选抽取 + 全局合并

普通自动记忆是局部抽取，bootstrap 是全局初始化，所以最好分两阶段。

## 阶段一：每个 chunk 抽取候选记忆

例如 500 条历史消息切成 20 个 chunk：

```text
chunk_001 → 5 条候选记忆
chunk_002 → 3 条候选记忆
...
chunk_020 → 4 条候选记忆
```

候选先不一定立即写入，或者写入前先进入 pending list。

## 阶段二：候选记忆全局去重、合并、冲突处理

因为历史消息里很可能反复出现：

```text
用户在做 AstrBot 插件
用户在做 MemoryOS 插件
用户正在设计长期记忆系统
```

这些应该合并成一条更稳定的 project_state，而不是存三条。

最终写入：

```text
candidate memories
→ group by scope + memory_type + entities
→ similarity dedup
→ resolver.resolve_and_store
→ embed_and_index
```

你现有 `MemoryResolver` 已经适合接这个步骤。

---

# 7. 私聊 bootstrap 规则

私聊比较直接。

默认写入：

```text
scope = user_private
owner_key = identity.private_space
```

可以抽取：

```text
用户长期偏好
用户项目状态
用户学习习惯
用户反复纠正
用户明确要求记住的信息
用户和 bot 的协作方式
```

不要抽取：

```text
临时问题
短期情绪
敏感信息
一次性任务
已经明显过期的计划
```

例如从历史消息中抽取：

```json
{
  "scope": "user_private",
  "memory_type": "procedure",
  "content": "用户在学习课件时希望按页分段讲解，用户说“继续”后再进入下一部分。",
  "confidence": 0.93,
  "importance": 0.9
}
```

这是非常适合 bootstrap 的记忆。

---

# 8. 群聊 bootstrap 规则要更保守

群聊历史 bootstrap 是最危险的，因为群聊有玩笑、多人、上下文漂移、隐私信息。

默认策略应该是：

```text
只抽取 group_shared 和当前用户的 user_in_group
不从群聊自动生成 user_private
不把单个成员的话升级为群共识
不抽取敏感个人画像
```

群聊 bootstrap 最好只允许管理员执行：

```text
/mem group bootstrap 1000
```

而且默认只写：

```text
scope = group_shared
```

除非消息里有非常明确的个人声明：

```text
以后在这个群里叫我小王。
```

才写：

```text
scope = user_in_group
owner_key = 当前群 + 当前发言人
```

群聊 bootstrap prompt 必须强调：

```text
不要把单个成员观点记成群体事实。
不要保存玩笑、调侃、临时情绪。
不要保存敏感个人信息。
只有明确群体共识、项目背景、固定规则、管理员决策才可写入 group_shared。
```

---

# 9. 需要新增的配置项

建议加入这些配置：

```json
{
  "history_bootstrap_enabled": true,
  "history_bootstrap_source": "memoryos_raw",
  "history_bootstrap_max_messages": 1000,
  "history_bootstrap_chunk_size": 30,
  "history_bootstrap_chunk_overlap": 4,
  "history_bootstrap_only_unprocessed": true,
  "history_bootstrap_mark_processed": true,
  "history_bootstrap_dry_run_limit": 20,
  "history_bootstrap_group_requires_admin": true,
  "history_bootstrap_group_policy": "conservative",
  "history_bootstrap_min_importance": 0.65,
  "history_bootstrap_min_confidence": 0.7,
  "history_bootstrap_allow_user_in_group": true,
  "history_bootstrap_allow_group_shared": true,
  "history_bootstrap_allow_user_private_from_group": false
}
```

注意 bootstrap 的阈值应该比普通自动记忆更严格：

```text
普通自动记忆 min_importance = 0.55
bootstrap min_importance = 0.65 或 0.7
```

因为历史消息里噪声更多。

---

# 10. 需要新增的 job 类型

你现在已经有 `memory_jobs` 表，而且主命令里已经用它提交 `rebuild_index` 任务。

所以 bootstrap 也应该作为 job：

```text
job_type = "bootstrap_history"
```

payload 示例：

```json
{
  "source": "memoryos_raw",
  "scope_mode": "current_session",
  "session_id": "...",
  "group_id": "...",
  "user_id": "...",
  "limit": 500,
  "chunk_size": 30,
  "overlap": 4,
  "dry_run": false,
  "only_unprocessed": true
}
```

result 示例：

```json
{
  "read_messages": 500,
  "chunks": 18,
  "candidate_count": 42,
  "stored_count": 17,
  "deduped_count": 19,
  "skipped_low_confidence": 6,
  "failed_chunks": 0
}
```

---

# 11. 现有 `MemoryTaskQueue` 怎么改

现在 `MemoryTaskQueue` 只支持 `ExtractionTask`，而且队列类型是：

```python
asyncio.Queue[Optional[ExtractionTask]]
```

它的 `_process_extract()` 固定从 `short_context.snapshot()` 取 turns。

建议把任务队列升级成多任务类型。

## 方案 A：最小改动

新增一个 `BootstrapTask`，然后 queue 接收 union：

```python
@dataclass
class BootstrapTask:
    event: Any
    identity: Identity
    job_id: str
    source: str
    limit: int
    scope_mode: str
    dry_run: bool = False
```

然后：

```python
async def enqueue_bootstrap(self, task: BootstrapTask) -> None:
    await self._queue.put(task)
```

`_run()` 里判断：

```python
if isinstance(task, ExtractionTask):
    await self._process_extract(task)
elif isinstance(task, BootstrapTask):
    await self._process_bootstrap(task)
```

这是最快能跑通的。

## 方案 B：更干净

拆成两个队列：

```text
MemoryExtractionQueue
MemoryBootstrapQueue
```

我建议第一版用方案 A，简单。

---

# 12. 需要给 extractor 增加一个方法

现在 `MemoryExtractor.extract_from_turns()` 接收的是 `turns`，内部 `_format_turns()` 只处理 role/content。

bootstrap 可以复用它，但最好新增：

```python
async def extract_from_history_chunk(
    self,
    event: Any,
    identity: Identity,
    messages: list[RawMessage],
    bootstrap_mode: bool = True,
) -> list[MemoryCandidate]:
    ...
```

原因是历史消息比当前短期上下文需要更多 metadata：

```text
timestamp
sender_id
group_id
role
message_id
```

尤其群聊 bootstrap，需要知道每句话是谁说的。

格式化时建议这样：

```text
[2026-06-18 10:23] user:123456: 以后在这个群里叫我小王
[2026-06-18 10:24] assistant: 好的
[2026-06-18 10:25] user:789000: 我们这个群主要讨论 MemoryOS 插件
```

而不是只写：

```text
user: ...
assistant: ...
```

否则群聊里容易串人。

---

# 13. Bootstrap 专用 prompt

不要直接用普通 `MEMORY_EXTRACTION_PROMPT`。可以复用主体规则，但加上历史初始化规则。

核心 prompt 应该强调：

```text
你正在从历史聊天记录中初始化长期记忆。
这些消息可能包含过期内容、玩笑、临时话题和重复信息。
只抽取对未来对话长期有用的信息。
不要保存一次性问题。
不要保存临时情绪。
不要保存敏感个人信息。
不要从单个群成员的话推断整个群共识。
如果是群聊，只在明确群体共识、项目背景、固定规则、管理员决策时输出 group_shared。
```

输出格式仍然用 `MemoryCandidate` JSON。

额外字段建议加：

```json
{
  "source_message_ids": ["raw_1", "raw_2"],
  "evidence_summary": "来自用户多次要求课程讲解按页分段进行。",
  "time_validity": "long_term | temporary | expired | uncertain"
}
```

如果 `time_validity = expired`，默认不写入。

---

# 14. 需要新增 store 查询方法

你现在有 `recent_raw_messages(session_id, limit)`，它只适合当前 session 总结。

Bootstrap 至少需要这些：

```python
async def raw_messages_for_bootstrap(
    self,
    *,
    session_id: str = "",
    user_id: str = "",
    group_id: str = "",
    platform_id: str = "",
    limit: int = 1000,
    offset: int = 0,
    only_unprocessed: bool = True,
    after_ts: int | None = None,
    before_ts: int | None = None,
) -> list[RawMessage]:
    ...
```

SQL 逻辑：

```sql
SELECT * FROM raw_messages
WHERE 1=1
  AND (:session_id = '' OR session_id = :session_id)
  AND (:user_id = '' OR user_id = :user_id)
  AND (:group_id = '' OR group_id = :group_id)
  AND (:only_unprocessed = 0 OR processed_for_memory = 0)
  AND (:after_ts IS NULL OR timestamp >= :after_ts)
  AND (:before_ts IS NULL OR timestamp <= :before_ts)
ORDER BY timestamp ASC
LIMIT :limit OFFSET :offset;
```

此外建议新增：

```python
async def count_raw_messages_for_bootstrap(...) -> int
```

这样 `/mem bootstrap status` 和 WebUI 进度更好做。

---

# 15. 需要避免重复处理

你已有 `processed_for_memory` 字段，也有 `mark_raw_processed()`。

Bootstrap 应该使用它。

默认：

```text
only_unprocessed = true
mark_processed = true
```

但是要注意：普通自动抽取现在似乎没有在 `MemoryTaskQueue._process_extract()` 里调用 `mark_raw_processed()`，它只调用了 `short_context.mark_extracted()`。

所以如果你要认真用 `processed_for_memory`，需要统一策略：

```text
普通自动抽取成功后，也标记对应 raw_messages processed_for_memory = 1
bootstrap 成功处理后，也标记 processed_for_memory = 1
dry-run 不标记
```

否则 `only_unprocessed` 的意义会变弱。

---

# 16. 命令设计建议

在 `main.py._handle_mem_command()` 里加：

```python
if head == "bootstrap":
    return await self._cmd_bootstrap(event, identity, tail)
```

你现在的 `_handle_mem_command()` 已经集中处理 `remember/search/list/forget/summarize/status/export/import/rebuild-index/group`，所以加 `bootstrap` 很自然。

建议命令：

```text
/mem bootstrap
/mem bootstrap current 300
/mem bootstrap dry-run 300
/mem bootstrap private 500
/mem bootstrap group 1000
/mem bootstrap status
```

第一版可以只做三个：

```text
/mem bootstrap current [limit]
/mem bootstrap dry-run [limit]
/mem bootstrap status
```

群聊支持可以放第二版。

---

# 17. 伪代码设计

## 17.1 main.py 增加命令

```python
if head == "bootstrap":
    return await self._cmd_bootstrap(event, identity, tail)
```

## 17.2 command handler

```python
async def _cmd_bootstrap(self, event, identity, tail: str) -> str:
    if not self.config.history_bootstrap_enabled:
        return "MemoryOS：历史初始化功能已在配置中关闭。"

    mode, rest = _split_head(tail)
    dry_run = mode == "dry-run"

    if mode in {"", "current", "dry-run"}:
        limit = _parse_int(rest, self.config.history_bootstrap_default_limit)
        job_id = await self.store.create_job(
            "bootstrap_history",
            {
                "mode": "current_session",
                "session_id": identity.session_id,
                "limit": limit,
                "dry_run": dry_run,
            },
        )
        await self.task_queue.enqueue_bootstrap(
            BootstrapTask(
                event=event,
                identity=identity,
                job_id=job_id,
                source="memoryos_raw",
                scope_mode="current_session",
                limit=limit,
                dry_run=dry_run,
            )
        )
        return f"MemoryOS：已提交历史记忆初始化任务，任务 ID：{job_id}"

    if mode == "status":
        jobs = await self.store.list_jobs(limit=10)
        return format_jobs(jobs)

    return "用法：/mem bootstrap current [数量] 或 /mem bootstrap dry-run [数量]"
```

---

## 17.3 task queue 增加处理

```python
@dataclass
class BootstrapTask:
    event: Any
    identity: Identity
    job_id: str
    source: str
    scope_mode: str
    limit: int
    dry_run: bool = False
```

```python
async def _process_bootstrap(self, task: BootstrapTask) -> None:
    await self.store.update_job(task.job_id, "running", {})

    messages = await self.store.raw_messages_for_bootstrap(
        session_id=task.identity.session_id,
        limit=task.limit,
        only_unprocessed=True,
    )

    chunks = chunk_raw_messages(
        messages,
        chunk_size=self.config.history_bootstrap_chunk_size,
        overlap=self.config.history_bootstrap_chunk_overlap,
    )

    total_candidates = 0
    stored_ids = []
    processed_ids = []

    for chunk in chunks:
        candidates = await self.extractor.extract_from_history_chunk(
            task.event,
            task.identity,
            chunk,
            bootstrap_mode=True,
        )

        total_candidates += len(candidates)

        for candidate in candidates:
            if not should_keep_bootstrap_candidate(candidate, self.config):
                continue

            memory = candidate.to_memory(task.identity)
            memory.source_message_ids = [
                m.message_id for m in chunk
            ]

            if task.dry_run:
                continue

            stored_id = await self.resolver.resolve_and_store(memory)
            stored_ids.append(stored_id)

            vector = await self.ai.embed(memory.embedding_text())
            if vector:
                stored = await self.store.get_memory(stored_id)
                if stored:
                    await self.store.upsert_vector(stored, vector)

        processed_ids.extend([m.message_id for m in chunk])

    if not task.dry_run:
        await self.store.mark_raw_processed(processed_ids)

    await self.store.update_job(
        task.job_id,
        "done",
        {
            "read_messages": len(messages),
            "chunks": len(chunks),
            "candidate_count": total_candidates,
            "stored_count": len(stored_ids),
            "dry_run": task.dry_run,
        },
    )
```

---

# 18. Dry-run 很重要

这个功能一定要有 dry-run。

原因是：历史消息噪声大，第一次跑很容易抽出奇怪记忆。

`dry-run` 输出应该是：

```text
MemoryOS 历史初始化预览：

读取消息：300 条
切分 chunk：12 个
候选记忆：27 条
建议写入：9 条

候选：
1. [preference/user_private/conf=0.93]
   用户在课程讲解中偏好中文解释，关键术语中英文对照。

2. [project_state/user_private/conf=0.88]
   用户正在开发 AstrBot MemoryOS 插件，目标支持私聊和群聊长期记忆。

3. [procedure/user_private/conf=0.91]
   用户希望讲课件时一部分一部分讲，用户说继续后再往后讲。
```

然后用户可以：

```text
/mem bootstrap apply <job_id>
```

这会比直接写入更安全。

如果第一版想简单，可以 dry-run 只返回前 20 条候选，不存 pending。

---

# 19. WebUI 也可以加这个功能

你 README 里已经有 WebUI 管理页能力。

WebUI 中可以加一个页面：

```text
Memory Bootstrap
```

字段：

```text
历史来源：MemoryOS raw / 导入文件 / AstrBot history
范围：当前私聊 / 当前群 / 当前 session / 全部
消息数量：100 / 500 / 1000 / 自定义
时间范围：最近 7 天 / 30 天 / 全部
模式：dry-run / 直接写入
群聊策略：conservative / normal
```

结果页展示：

```text
读取消息数
chunk 数
候选记忆数
实际写入数
跳过原因统计
错误 chunk
候选记忆预览
```

---

# 20. 这个功能的最大风险

## 风险 1：历史消息没有 owner

群聊导入时，如果每条消息没有 sender_id，那就很危险。

没有 sender_id 时，只允许抽取：

```text
group_shared
```

而且必须更保守。

不允许生成：

```text
user_in_group
```

更不允许生成：

```text
user_private
```

---

## 风险 2：过期信息被当成长期事实

比如历史里有：

```text
我明天要交作业。
```

这不应该写成长记忆。

Bootstrap prompt 要求 LLM 判断：

```text
是否仍可能有效？
是否只是当时的一次性计划？
```

一旦不确定，写成 `episode` 或直接跳过。

---

## 风险 3：重复记忆爆炸

历史里同一个项目可能被提了几十次。

所以 bootstrap 必须先查相似旧记忆，再通过 resolver 合并。

不要每个 chunk 都直接写入。

---

## 风险 4：群聊隐私

群聊历史里可能包含成员隐私。

默认不要抽取：

```text
手机号
地址
学校/公司个人身份
健康状况
家庭关系
政治宗教
财务信息
```

除非用户显式要求保存，而且也要考虑是否适合在群作用域里保存。

---

# 21. 我建议的实现顺序

## 第一步：只支持从 `raw_messages` 初始化当前 session

最小功能：

```text
/mem bootstrap current 300
/mem bootstrap dry-run 300
/mem bootstrap status
```

实现内容：

```text
store.raw_messages_for_bootstrap(session_id=...)
chunk_raw_messages()
extractor.extract_from_history_chunk()
task_queue.enqueue_bootstrap()
memory_jobs 记录进度
```

这是最稳的第一版。

---

## 第二步：支持当前私聊 / 当前群

新增：

```text
/mem bootstrap private 1000
/mem group bootstrap 1000
```

私聊按 `user_id` 查，群聊按 `group_id` 查。

---

## 第三步：支持文件导入

新增：

```text
/mem bootstrap import
```

或者 WebUI 上传 JSON/TXT。

---

## 第四步：支持 AstrBot 原生历史 adapter

如果确认 AstrBot 有稳定历史消息 API，再加：

```text
history_source = astrbot_native
```

否则不要强依赖。

---

# 22. 最终判断

这个功能非常值得加。

它解决的是 MemoryOS 的冷启动问题：

```text
没有 bootstrap：
插件刚装上时什么都不知道，只能从未来对话慢慢积累。

有 bootstrap：
插件可以从已有聊天记录中快速建立用户偏好、项目状态、群聊上下文和协作习惯。
```

但它必须做成 **受控、可预览、可回滚、按作用域隔离的后台任务**，不能简单粗暴地“一键总结全部历史”。

我建议最终功能定义为：

```text
History Bootstrap：
从 AstrBot 原生历史中读取历史消息，
按作用域和时间窗口分块，
使用 LLM 抽取结构化候选记忆，
经过去重、冲突处理、敏感过滤和 embedding 索引后，
初始化当前用户/群聊/会话的长期记忆。
```

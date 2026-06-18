from __future__ import annotations

from typing import Iterable, List

from memoryos_core.models import MemoryItem, RetrievalCandidate


def memory_line(memory: MemoryItem, index: int = 0) -> str:
    prefix = "%d. " % index if index else ""
    tags = ""
    if memory.tags:
        tags = " #" + " #".join(memory.tags[:3])
    return (
        "%s%s [%s/%s] %.2f/%.2f %s%s"
        % (
            prefix,
            memory.memory_id,
            memory.scope,
            memory.memory_type,
            memory.importance,
            memory.confidence,
            _trim(memory.content, 120),
            tags,
        )
    )


def format_memory_list(memories: Iterable[MemoryItem]) -> str:
    items = list(memories)
    if not items:
        return "MemoryOS：当前范围内没有找到记忆。"
    lines = ["MemoryOS 记忆列表："]
    for idx, memory in enumerate(items, 1):
        lines.append(memory_line(memory, idx))
    return "\n".join(lines)


def format_search_results(candidates: List[RetrievalCandidate]) -> str:
    if not candidates:
        return "MemoryOS：没有找到匹配的记忆。"
    lines = ["MemoryOS 搜索结果："]
    for idx, candidate in enumerate(candidates, 1):
        lines.append(
            "%d. %s 相关度=%.3f %s"
            % (
                idx,
                candidate.memory.memory_id,
                candidate.final_score,
                _trim(candidate.memory.content, 140),
            )
        )
    return "\n".join(lines)


def status_text(stats: dict, ai: object, enabled: bool) -> str:
    return "\n".join(
        [
            "MemoryOS 状态：",
            "启用状态：%s" % ("已启用" if enabled else "已停用"),
            "数据库：%s" % stats.get("db_path", ""),
            "数据库版本：%s" % stats.get("schema_version", ""),
            "关键词索引：%s" % ("可用" if stats.get("fts_enabled") else "不可用"),
            "活跃记忆：%s" % stats.get("active_memories", 0),
            "向量数量：%s" % stats.get("vectors", 0),
            "原始消息：%s" % stats.get("raw_messages", 0),
            "Embedding：%s"
            % (
                "可用"
                if getattr(ai, "embedding_available", False)
                else "未配置或不可用，当前降级为关键词检索"
            ),
            "最近 Embedding 错误：%s"
            % (getattr(ai, "last_embedding_error", "") or "无"),
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "MemoryOS 命令：",
            "/mem remember <内容>  记住一条与当前会话相关的内容",
            "/mem search <查询>  搜索当前可用范围内的记忆",
            "/mem list  列出当前可用范围内的记忆",
            "/mem forget <memory_id>  删除指定记忆",
            "/mem forget all  删除当前范围内所有记忆",
            "/mem summarize  总结最近会话",
            "/mem status  查看插件状态",
            "/mem on | /mem off  在当前会话启用/停用记忆",
            "/mem export  导出记忆 JSON",
            "/mem import <json>  导入记忆 JSON",
            "/mem rebuild-index  重建向量索引",
            "/mem group on | off | remember <内容> | list | forget <memory_id> | policy conservative|normal|aggressive",
        ]
    )


def _trim(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."

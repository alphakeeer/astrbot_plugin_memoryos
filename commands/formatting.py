from __future__ import annotations

from typing import Iterable, List

from core.models import MemoryItem, RetrievalCandidate


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
        return "MemoryOS: no memories found."
    lines = ["MemoryOS memories:"]
    for idx, memory in enumerate(items, 1):
        lines.append(memory_line(memory, idx))
    return "\n".join(lines)


def format_search_results(candidates: List[RetrievalCandidate]) -> str:
    if not candidates:
        return "MemoryOS: no matching memories."
    lines = ["MemoryOS search results:"]
    for idx, candidate in enumerate(candidates, 1):
        lines.append(
            "%d. %s score=%.3f %s"
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
            "MemoryOS status:",
            "enabled: %s" % ("yes" if enabled else "no"),
            "db: %s" % stats.get("db_path", ""),
            "schema: %s" % stats.get("schema_version", ""),
            "fts: %s" % ("on" if stats.get("fts_enabled") else "off"),
            "active memories: %s" % stats.get("active_memories", 0),
            "vectors: %s" % stats.get("vectors", 0),
            "raw messages: %s" % stats.get("raw_messages", 0),
            "embedding: %s"
            % (
                "available"
                if getattr(ai, "embedding_available", False)
                else "not configured / degraded to keyword search"
            ),
            "last embedding error: %s"
            % (getattr(ai, "last_embedding_error", "") or "none"),
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "MemoryOS commands:",
            "/mem remember <content>",
            "/mem search <query>",
            "/mem list",
            "/mem forget <memory_id>",
            "/mem forget all",
            "/mem summarize",
            "/mem status",
            "/mem on | /mem off",
            "/mem export",
            "/mem import <json>",
            "/mem rebuild-index",
            "/mem group on | off | remember <content> | list | forget <memory_id> | policy conservative|normal|aggressive",
        ]
    )


def _trim(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


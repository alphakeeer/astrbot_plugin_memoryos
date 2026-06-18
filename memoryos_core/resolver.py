from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, List, Optional

from .models import ACTIVE_STATUS, SUPERSEDED_STATUS, UNCERTAIN_STATUS, MemoryItem, new_id, now_ms


class MemoryResolver:
    def __init__(self, store: Any, config: Any) -> None:
        self.store = store
        self.config = config

    async def resolve_and_store(self, memory: MemoryItem) -> str:
        existing = await self.store.list_memories(
            allowed_scopes=[_Rule(memory.scope, memory.owner_key)],
            status=ACTIVE_STATUS,
            limit=30,
            memory_type=memory.memory_type,
        )
        relation, target = self._classify(memory, existing)
        if relation == "duplicate" and target:
            target.confidence = max(target.confidence, memory.confidence)
            target.importance = max(target.importance, memory.importance)
            target.updated_at = now_ms()
            await self.store.upsert_memory(target)
            return target.memory_id
        if relation == "update" and target:
            await self.store.set_memory_status(target.memory_id, SUPERSEDED_STATUS)
            await self.store.upsert_memory(memory)
            await self._edge(memory.memory_id, target.memory_id, "updates", memory.confidence)
            return memory.memory_id
        if relation == "contradict" and target:
            memory.status = UNCERTAIN_STATUS
            await self.store.upsert_memory(memory)
            await self._edge(memory.memory_id, target.memory_id, "contradicts", memory.confidence)
            return memory.memory_id
        await self.store.upsert_memory(memory)
        return memory.memory_id

    def _classify(
        self, memory: MemoryItem, existing: List[MemoryItem]
    ) -> tuple[str, Optional[MemoryItem]]:
        best = None
        best_score = 0.0
        for item in existing:
            score = _text_similarity(memory.content, item.content)
            if score > best_score:
                best = item
                best_score = score
        if best is None:
            return "new", None
        if best_score >= 0.92:
            return "duplicate", best
        if best_score >= 0.7 and _looks_like_update(memory.content):
            return "update", best
        if best_score >= 0.6 and _looks_like_contradiction(memory.content, best.content):
            return "contradict", best
        return "new", None

    async def _edge(
        self, source_id: str, target_id: str, relation: str, confidence: float
    ) -> None:
        if not hasattr(self.store, "conn"):
            return
        await self.store._run(self._edge_sync, source_id, target_id, relation, confidence)

    def _edge_sync(
        self, source_id: str, target_id: str, relation: str, confidence: float
    ) -> None:
        self.store.conn.execute(
            """
            INSERT OR REPLACE INTO memory_edges(edge_id, source_memory_id, target_memory_id, relation_type, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("edge"), source_id, target_id, relation, confidence, now_ms()),
        )
        self.store.conn.commit()


class _Rule:
    def __init__(self, scope: str, owner_key: str) -> None:
        self.scope = scope
        self.owner_key = owner_key


def _text_similarity(a: str, b: str) -> float:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    overlap = 0.0
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / float(len(tokens_a | tokens_b))
    return max(seq, overlap)


def _looks_like_update(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in ["改用", "现在", "更新", "变成", "instead", "now uses", "switch"]
    )


def _looks_like_contradiction(new_text: str, old_text: str) -> bool:
    lower = (new_text + " " + old_text).lower()
    return any(marker in lower for marker in ["不是", "不要", "不再", "not ", "no longer"])


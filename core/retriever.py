from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List

from .models import (
    ACTIVE_STATUS,
    Identity,
    MemoryItem,
    RetrievalCandidate,
    allowed_scope_rules,
    now_ms,
)


class MemoryRetriever:
    def __init__(self, store: Any, ai: Any, config: Any) -> None:
        self.store = store
        self.ai = ai
        self.config = config

    async def retrieve(self, query: str, identity: Identity) -> List[RetrievalCandidate]:
        rules = allowed_scope_rules(identity, self.config)
        top_k = int(getattr(self.config, "retrieval_top_k", 8))
        multiplier = int(getattr(self.config, "retrieval_candidate_multiplier", 3))
        candidate_limit = top_k * multiplier

        vector_scores: Dict[str, float] = {}
        query_vector = await self.ai.embed(query)
        if query_vector:
            for memory_id, score in await self.store.vector_search(
                query_vector, rules, candidate_limit
            ):
                vector_scores[memory_id] = max(vector_scores.get(memory_id, 0.0), score)

        keyword_scores: Dict[str, float] = {}
        for memory_id, score in await self.store.keyword_search(
            query, rules, candidate_limit
        ):
            keyword_scores[memory_id] = max(keyword_scores.get(memory_id, 0.0), score)

        ids = list(dict.fromkeys(list(vector_scores.keys()) + list(keyword_scores.keys())))
        if not ids:
            fallback = await self.store.list_memories(
                allowed_scopes=rules,
                status=ACTIVE_STATUS,
                limit=min(top_k, 5),
            )
            ids = [item.memory_id for item in fallback]

        candidates = []
        for memory_id in ids:
            memory = await self.store.get_memory(memory_id)
            if not memory or not self._is_visible(memory, identity):
                continue
            candidate = RetrievalCandidate(
                memory=memory,
                vector_similarity=vector_scores.get(memory_id, 0.0),
                keyword_score=keyword_scores.get(memory_id, 0.0),
            )
            self._score_candidate(candidate, query, identity)
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.final_score, reverse=True)
        return candidates[:candidate_limit]

    def _is_visible(self, memory: MemoryItem, identity: Identity) -> bool:
        if memory.status != ACTIVE_STATUS:
            return False
        current = now_ms()
        if memory.valid_to and memory.valid_to < current:
            return False
        if identity.is_group and memory.visibility == "private":
            return bool(getattr(self.config, "allow_private_memory_in_group", False))
        if not identity.is_group and memory.scope.startswith("group"):
            return bool(getattr(self.config, "allow_group_memory_in_private", False))
        return True

    def _score_candidate(
        self, candidate: RetrievalCandidate, query: str, identity: Identity
    ) -> None:
        memory = candidate.memory
        candidate.entity_overlap = _entity_overlap(query, memory.entities)
        candidate.recency_score = _recency(memory.updated_at)
        candidate.importance_score = memory.importance
        candidate.access_score = min(1.0, math.log1p(memory.access_count) / 5.0)
        candidate.stale_penalty = _stale_penalty(memory.valid_to)
        candidate.sensitivity_penalty = {
            "normal": 0.0,
            "private": 0.2,
            "sensitive": 0.5,
        }.get(memory.sensitivity, 0.0)
        candidate.scope_risk_penalty = _scope_risk(memory, identity, self.config)
        candidate.final_score = (
            0.45 * candidate.vector_similarity
            + 0.15 * candidate.keyword_score
            + 0.15 * candidate.entity_overlap
            + 0.10 * candidate.recency_score
            + 0.10 * candidate.importance_score
            + 0.05 * candidate.access_score
            - 0.20 * candidate.stale_penalty
            - 0.30 * candidate.sensitivity_penalty
            - 0.40 * candidate.scope_risk_penalty
        )


def _entity_overlap(query: str, entities: Iterable[str]) -> float:
    entity_list = list(entities)
    if not entity_list:
        return 0.0
    lower = query.lower()
    matches = sum(1 for entity in entity_list if entity.lower() in lower)
    return matches / float(len(entity_list) or 1)


def _recency(updated_at: int) -> float:
    age_days = max(0.0, (now_ms() - int(updated_at or 0)) / 86_400_000.0)
    return 1.0 / (1.0 + age_days / 30.0)


def _stale_penalty(valid_to: int) -> float:
    if not valid_to:
        return 0.0
    days = (valid_to - now_ms()) / 86_400_000.0
    if days < 0:
        return 1.0
    if days < 3:
        return 0.3
    return 0.0


def _scope_risk(memory: MemoryItem, identity: Identity, config: Any) -> float:
    if identity.is_group and memory.visibility == "private":
        return 1.0
    if not identity.is_group and memory.scope.startswith("group"):
        return 0.7
    return 0.0

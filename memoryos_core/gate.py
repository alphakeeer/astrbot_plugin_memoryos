from __future__ import annotations

from typing import Any, List

from .models import RetrievalCandidate
from .prompts import MEMORY_GATE_PROMPT
from .providers import extract_json


class MemoryGate:
    def __init__(self, ai: Any, config: Any) -> None:
        self.ai = ai
        self.config = config

    async def select(
        self, event: Any, query: str, candidates: List[RetrievalCandidate]
    ) -> List[RetrievalCandidate]:
        mode = getattr(self.config, "memory_gate_mode", "heuristic")
        if mode == "off":
            return candidates[: int(getattr(self.config, "retrieval_top_k", 8))]
        heuristic = self._heuristic(query, candidates)
        if mode != "llm" or not heuristic:
            return heuristic
        llm_selected = await self._llm_select(event, query, heuristic)
        return llm_selected or heuristic

    def _heuristic(
        self, query: str, candidates: List[RetrievalCandidate]
    ) -> List[RetrievalCandidate]:
        selected = []
        for candidate in candidates:
            if candidate.scope_risk_penalty >= 0.7:
                continue
            if candidate.sensitivity_penalty >= 0.5 and candidate.final_score < 0.65:
                continue
            if candidate.final_score >= 0.12 or _has_token_overlap(
                query, candidate.memory.content
            ):
                selected.append(candidate)
        selected.sort(key=lambda item: item.final_score, reverse=True)
        return selected[: int(getattr(self.config, "retrieval_top_k", 8))]

    async def _llm_select(
        self, event: Any, query: str, candidates: List[RetrievalCandidate]
    ) -> List[RetrievalCandidate]:
        payload = [
            {
                "id": item.memory.memory_id,
                "type": item.memory.memory_type,
                "scope": item.memory.scope,
                "content": item.memory.content,
                "score": item.final_score,
            }
            for item in candidates
        ]
        prompt = MEMORY_GATE_PROMPT + "\nQuery:\n%s\nCandidates:\n%s" % (
            query,
            payload,
        )
        text = await self.ai.llm_generate(event, prompt)
        result = extract_json(text)
        if not isinstance(result, list):
            return []
        allowed = {str(item.get("id")) for item in result if item.get("use") is True}
        return [item for item in candidates if item.memory.memory_id in allowed]


def _has_token_overlap(query: str, content: str) -> bool:
    q = {token for token in query.lower().split() if len(token) >= 3}
    c = {token for token in content.lower().split() if len(token) >= 3}
    return bool(q & c)


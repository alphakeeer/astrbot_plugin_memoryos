from __future__ import annotations

from typing import Iterable, List, Protocol, Sequence, Tuple

from core.models import ScopeRule


class VectorStore(Protocol):
    async def upsert(self, memory_id: str, vector: Sequence[float]) -> None:
        ...

    async def delete(self, memory_id: str) -> None:
        ...

    async def search(
        self,
        query_vector: Sequence[float],
        allowed_scopes: Iterable[ScopeRule],
        top_k: int,
    ) -> List[Tuple[str, float]]:
        ...

    async def rebuild(self, embeddings: Iterable[Tuple[str, Sequence[float]]]) -> int:
        ...


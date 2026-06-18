from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from .models import Identity


@dataclass
class ExtractionTask:
    event: Any
    identity: Identity
    session_key: str


class MemoryTaskQueue:
    def __init__(
        self,
        store: Any,
        short_context: Any,
        extractor: Any,
        resolver: Any,
        ai: Any,
        config: Any,
    ) -> None:
        self.store = store
        self.short_context = short_context
        self.extractor = extractor
        self.resolver = resolver
        self.ai = ai
        self.config = config
        self._queue: asyncio.Queue[Optional[ExtractionTask]] = asyncio.Queue()
        self._worker: Optional[asyncio.Task[Any]] = None

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="memoryos-task-queue")

    async def stop(self) -> None:
        if self._worker is None:
            return
        await self._queue.put(None)
        await self._worker
        self._worker = None

    async def enqueue_extract(self, task: ExtractionTask) -> None:
        await self._queue.put(task)

    async def _run(self) -> None:
        while True:
            task = await self._queue.get()
            if task is None:
                return
            try:
                await self._process_extract(task)
            except Exception:
                # Keep the queue alive. AstrBot logger is used at the plugin boundary.
                pass

    async def _process_extract(self, task: ExtractionTask) -> None:
        turns = self.short_context.snapshot(
            task.session_key, int(getattr(self.config, "extraction_window_turns", 12))
        )
        candidates = await self.extractor.extract_from_turns(
            task.event, task.identity, turns
        )
        stored_ids = []
        for candidate in candidates:
            if not candidate.should_store or not candidate.content:
                continue
            if candidate.importance < getattr(self.config, "min_importance_to_store", 0.55):
                continue
            if candidate.confidence < getattr(self.config, "min_confidence_to_store", 0.6):
                continue
            memory = candidate.to_memory(task.identity)
            stored_id = await self.resolver.resolve_and_store(memory)
            stored_ids.append(stored_id)
            vector = await self.ai.embed(memory.embedding_text())
            if vector:
                stored = await self.store.get_memory(stored_id)
                if stored:
                    await self.store.upsert_vector(stored, vector)
        self.short_context.mark_extracted(task.session_key)


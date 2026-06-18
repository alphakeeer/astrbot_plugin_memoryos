from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

from .models import Identity, MemoryCandidate, RawMessage

try:
    from .tasks import BootstrapTask, ExtractionTask
except Exception:  # pragma: no cover - protects partially updated plugin installs.
    @dataclass
    class ExtractionTask:
        event: Any
        identity: Identity
        session_key: str

    @dataclass
    class BootstrapTask:
        event: Any
        identity: Identity
        job_id: str
        limit: int
        dry_run: bool = False
        source: str = "astrbot_conversation"
        scope_mode: str = "current_session"


class MemoryTaskQueue:
    def __init__(
        self,
        store: Any,
        short_context: Any,
        extractor: Any,
        resolver: Any,
        ai: Any,
        config: Any,
        history_source: Any = None,
    ) -> None:
        self.store = store
        self.short_context = short_context
        self.extractor = extractor
        self.resolver = resolver
        self.ai = ai
        self.config = config
        self.history_source = history_source
        self._queue: asyncio.Queue[
            Optional[Union[ExtractionTask, BootstrapTask]]
        ] = asyncio.Queue()
        self._worker: Optional[asyncio.Task[Any]] = None
        self._cancelled_jobs: set[str] = set()

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

    async def enqueue_bootstrap(self, task: BootstrapTask) -> None:
        self._cancelled_jobs.discard(task.job_id)
        await self._queue.put(task)

    def cancel_job(self, job_id: str) -> None:
        if job_id:
            self._cancelled_jobs.add(job_id)

    async def _run(self) -> None:
        while True:
            task = await self._queue.get()
            if task is None:
                return
            try:
                if isinstance(task, BootstrapTask):
                    await self._process_bootstrap(task)
                else:
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

    async def _process_bootstrap(self, task: BootstrapTask) -> None:
        result: Dict[str, Any] = {
            "source": task.source,
            "scope_mode": task.scope_mode,
            "dry_run": task.dry_run,
            "read_messages": 0,
            "chunks": 0,
            "candidate_count": 0,
            "stored_count": 0,
            "deduped_count": 0,
            "skipped_low_confidence": 0,
            "skipped_low_importance": 0,
            "skipped_invalid_scope": 0,
            "failed_chunks": 0,
            "parse_skipped": 0,
            "preview": [],
        }
        await self.store.update_job(task.job_id, "running", result)
        if task.job_id in self._cancelled_jobs:
            await self.store.update_job(task.job_id, "cancelled", result)
            return
        if self.history_source is None:
            result["errors"] = ["未配置 AstrBot 历史来源"]
            await self.store.update_job(task.job_id, "failed", result)
            return

        try:
            fetch = await self.history_source.fetch_current(
                task.event, task.identity, task.limit
            )
        except Exception as exc:
            result["errors"] = [str(exc)]
            await self.store.update_job(task.job_id, "failed", result)
            return

        messages: List[RawMessage] = list(fetch.messages)
        result["conversation_id"] = getattr(fetch, "conversation_id", "")
        result["read_messages"] = len(messages)
        result["parse_skipped"] = int(getattr(fetch, "skipped", 0) or 0)
        result["errors"] = list(getattr(fetch, "errors", []) or [])
        if not messages:
            result["message"] = "无可用 AstrBot 历史"
            await self.store.update_job(task.job_id, "done", result)
            return

        if getattr(self.config, "history_bootstrap_store_raw_snapshot", True):
            await self.store.append_raw_messages(messages)

        chunks = chunk_raw_messages(
            messages,
            int(getattr(self.config, "history_bootstrap_chunk_size", 30)),
            int(getattr(self.config, "history_bootstrap_chunk_overlap", 4)),
        )
        result["chunks"] = len(chunks)
        processed_ids: List[str] = []
        stored_ids: set[str] = set()

        for index, chunk in enumerate(chunks, 1):
            if task.job_id in self._cancelled_jobs:
                result["cancelled_at_chunk"] = index
                await self.store.update_job(task.job_id, "cancelled", result)
                return
            try:
                candidates = await self.extractor.extract_from_history_chunk(
                    task.event, task.identity, chunk
                )
            except Exception as exc:
                result["failed_chunks"] += 1
                result.setdefault("chunk_errors", []).append(str(exc))
                continue
            result["candidate_count"] += len(candidates)
            for candidate in candidates:
                keep_reason = _bootstrap_keep_reason(candidate, task.identity, self.config)
                if keep_reason:
                    result[keep_reason] += 1
                    continue
                if not candidate.source_message_ids:
                    candidate.source_message_ids = [m.message_id for m in chunk]
                preview = _candidate_preview(candidate)
                if len(result["preview"]) < int(
                    getattr(self.config, "history_bootstrap_dry_run_limit", 20)
                ):
                    result["preview"].append(preview)
                if task.dry_run:
                    continue
                memory = candidate.to_memory(task.identity)
                before_id = memory.memory_id
                stored_id = await self.resolver.resolve_and_store(memory)
                is_deduped = stored_id != before_id or stored_id in stored_ids
                if is_deduped:
                    result["deduped_count"] += 1
                stored_ids.add(stored_id)
                stored = await self.store.get_memory(stored_id)
                if stored:
                    vector = await self.ai.embed(stored.embedding_text())
                    if vector:
                        await self.store.upsert_vector(stored, vector)
                if not is_deduped:
                    result["stored_count"] += 1
            processed_ids.extend([m.message_id for m in chunk])
            await self.store.update_job(task.job_id, "running", result)

        if not task.dry_run:
            await self.store.mark_raw_processed(processed_ids)
        await self.store.update_job(task.job_id, "done", result)


def chunk_raw_messages(
    messages: Sequence[RawMessage], chunk_size: int, overlap: int
) -> List[List[RawMessage]]:
    if not messages:
        return []
    chunk_size = max(1, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 1))
    step = max(1, chunk_size - overlap)
    chunks = []
    index = 0
    while index < len(messages):
        chunk = list(messages[index : index + chunk_size])
        if chunk:
            chunks.append(chunk)
        index += step
    return chunks


def _bootstrap_keep_reason(
    candidate: MemoryCandidate, identity: Identity, config: Any
) -> str:
    if not candidate.should_store or not candidate.content:
        return "skipped_low_confidence"
    if candidate.importance < getattr(config, "history_bootstrap_min_importance", 0.65):
        return "skipped_low_importance"
    if candidate.confidence < getattr(config, "history_bootstrap_min_confidence", 0.7):
        return "skipped_low_confidence"
    if identity.is_group and candidate.scope == "user_private":
        return "skipped_invalid_scope"
    if not identity.is_group and candidate.scope in {"group_shared", "user_in_group"}:
        return "skipped_invalid_scope"
    return ""


def _candidate_preview(candidate: MemoryCandidate) -> Dict[str, Any]:
    return {
        "scope": candidate.scope,
        "memory_type": candidate.memory_type,
        "content": candidate.content,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "source_message_ids": candidate.source_message_ids,
        "reason": candidate.reason,
    }

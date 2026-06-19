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
            "empty_candidate_chunks": 0,
            "parse_skipped": 0,
            "preview": [],
            "llm_provider_id": str(getattr(self.config, "llm_provider_id", "") or ""),
            "embedding_provider_id": str(getattr(self.config, "embedding_provider_id", "") or ""),
            "thresholds": {
                "min_importance": getattr(self.config, "history_bootstrap_min_importance", 0.65),
                "min_confidence": getattr(self.config, "history_bootstrap_min_confidence", 0.7),
                "group_policy": getattr(self.config, "history_bootstrap_group_policy", "conservative"),
            },
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
            if not candidates:
                result["empty_candidate_chunks"] += 1
                if getattr(self.ai, "last_llm_error", ""):
                    result["last_llm_error"] = getattr(self.ai, "last_llm_error", "")
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
        result["diagnosis"] = _bootstrap_result_diagnosis(result)
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


def _bootstrap_result_diagnosis(result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("errors"):
        return {
            "level": "error",
            "message": "历史读取存在错误",
            "suggestion": "检查 unified_origin/session_id 是否属于同一会话，并查看 errors 字段。",
        }
    if result.get("read_messages", 0) <= 0:
        return {
            "level": "warning",
            "message": "未读取到可用 AstrBot 历史",
            "suggestion": "确认 AstrBot 已保存该会话历史，或换一个已知会话。",
        }
    if result.get("candidate_count", 0) <= 0:
        suggestion = "历史可读但没有候选。常见原因：历史多为系统任务/主动破冰/普通闲聊，或 LLM 返回空数组。请查看原始历史噪声标记。"
        if result.get("last_llm_error"):
            suggestion = "LLM 调用失败：%s。请检查 llm_provider_id 或当前会话模型。" % result.get("last_llm_error")
        return {
            "level": "warning",
            "message": "没有抽取到候选记忆",
            "suggestion": suggestion,
        }
    skipped = (
        int(result.get("skipped_low_confidence", 0) or 0)
        + int(result.get("skipped_low_importance", 0) or 0)
        + int(result.get("skipped_invalid_scope", 0) or 0)
    )
    if skipped and not result.get("preview"):
        return {
            "level": "warning",
            "message": "候选全部被过滤",
            "suggestion": "查看 skipped_* 计数；必要时降低阈值或调整群聊初始化策略。",
        }
    return {
        "level": "ok",
        "message": "初始化处理完成",
        "suggestion": "dry-run 合理后再确认写入；写入后可在记忆管理检查结果。",
    }

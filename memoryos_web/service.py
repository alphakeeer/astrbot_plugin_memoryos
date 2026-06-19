from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from memoryos_core.models import (
    ACTIVE_STATUS,
    Identity,
    MemoryItem,
    new_id,
    now_ms,
    visibility_for_scope,
)

try:
    from memoryos_core.tasks import BootstrapTask
except Exception:  # pragma: no cover - protects partially updated plugin installs.
    try:
        from memoryos_core.scheduler import BootstrapTask  # type: ignore[attr-defined]
    except Exception:
        @dataclass
        class BootstrapTask:
            event: Any
            identity: Any
            job_id: str
            limit: int
            dry_run: bool = False
            source: str = "astrbot_conversation"
            scope_mode: str = "current_session"


class APIError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class MemoryWebService:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    async def list_memories(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        memories = await self.plugin.store.list_memories(
            status=str(params.get("status") or ACTIVE_STATUS),
            limit=_as_int(params.get("limit"), 50),
            offset=_as_int(params.get("offset"), 0),
            memory_type=str(params.get("type") or ""),
            query=str(params.get("q") or ""),
        )
        return {"memories": [item.to_dict() for item in memories]}

    async def create_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        content = str(payload.get("content") or "").strip()
        if not content:
            raise APIError("记忆内容不能为空", 400, "empty_content")
        scope = str(payload.get("scope") or "global")
        ts = now_ms()
        memory = MemoryItem(
            memory_id=payload.get("memory_id") or new_id("mem"),
            scope=scope,
            owner_key=str(payload.get("owner_key") or "global"),
            visibility=str(payload.get("visibility") or visibility_for_scope(scope)),
            memory_type=str(payload.get("memory_type") or "fact"),
            content=content,
            canonical_text=str(payload.get("canonical_text") or content),
            tags=list(payload.get("tags") or []),
            entities=list(payload.get("entities") or []),
            confidence=_as_float(payload.get("confidence"), 0.9),
            importance=_as_float(payload.get("importance"), 0.7),
            created_at=ts,
            updated_at=ts,
        )
        await self.plugin.store.upsert_memory(memory)
        await self.plugin.embed_and_index(memory)
        return {"memory": memory.to_dict()}

    async def update_memory(self, memory_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        memory = await self.plugin.store.get_memory(memory_id)
        if not memory:
            raise APIError("没有找到这条记忆", 404, "not_found")
        content = str(payload.get("content", memory.content)).strip()
        canonical_text = str(payload.get("canonical_text", memory.canonical_text))
        tags = list(payload.get("tags", memory.tags) or [])
        importance = _as_float(payload.get("importance"), memory.importance)
        confidence = _as_float(payload.get("confidence"), memory.confidence)
        await self.plugin.store.update_memory_content(
            memory_id, content, canonical_text, tags, importance, confidence
        )
        updated = await self.plugin.store.get_memory(memory_id)
        if updated:
            await self.plugin.embed_and_index(updated)
        return {"memory": updated.to_dict() if updated else None}

    async def delete_memory(self, memory_id: str) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        ok = await self.plugin.store.soft_delete_memory(memory_id)
        await self.plugin.store.delete_vector(memory_id)
        return {"deleted": ok}

    async def expire_memory(self, memory_id: str) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        memory = await self.plugin.store.get_memory(memory_id)
        if not memory:
            raise APIError("没有找到这条记忆", 404, "not_found")
        memory.valid_to = now_ms()
        memory.updated_at = now_ms()
        await self.plugin.store.upsert_memory(memory)
        return {"expired": True}

    async def memory_logs(self, memory_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        logs = await self.plugin.store.access_logs(memory_id, _as_int(params.get("limit"), 100))
        return {"logs": logs}

    async def stats(self) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        stats = await self.plugin.store.stats()
        stats["enabled"] = self.plugin.enabled
        stats["embedding_available"] = getattr(self.plugin.ai, "embedding_available", False)
        web = getattr(self.plugin, "standalone_web", None)
        if web is not None:
            stats["standalone_web"] = web.status()
        return stats

    async def export_memories(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        include_raw = str(params.get("include_raw") or "false").lower() in {
            "1",
            "true",
            "yes",
        }
        return await self.plugin.store.export_json(include_raw)

    async def import_memories(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        counts = await self.plugin.store.import_json(payload)
        return {"imported": counts}

    async def rebuild_index(self) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        job_id = await self.plugin.store.create_job("rebuild_index", {})
        asyncio.create_task(self.plugin.rebuild_index(job_id))
        return {"job_id": job_id}

    async def jobs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        jobs = await self.plugin.store.list_jobs(
            limit=_as_int(params.get("limit"), 20),
            job_type=str(params.get("type") or ""),
        )
        return {"jobs": jobs}

    async def bootstrap_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._bootstrap_from_payload(payload, dry_run=False)

    async def bootstrap_dry_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._bootstrap_from_payload(payload, dry_run=True)

    async def bootstrap_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise APIError("缺少 job_id", 400, "missing_job_id")
        self.plugin.task_queue.cancel_job(job_id)
        await self.plugin.store.update_job(
            job_id, "cancel_requested", {"message": "已请求取消"}
        )
        return {"cancel_requested": True, "job_id": job_id}

    async def _bootstrap_from_payload(
        self, payload: Dict[str, Any], dry_run: bool
    ) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        if not self.plugin.config.history_bootstrap_enabled:
            raise APIError("历史初始化功能已在配置中关闭", 409, "bootstrap_disabled")
        if not hasattr(self.plugin.task_queue, "enqueue_bootstrap"):
            raise APIError(
                "历史初始化队列不可用，请确认插件文件已完整更新",
                503,
                "bootstrap_unavailable",
            )
        origin = str(payload.get("unified_origin") or "").strip()
        if not origin:
            raise APIError(
                "WebUI 启动历史初始化需要 unified_origin；聊天中可直接使用 /mem bootstrap dry-run",
                400,
                "missing_unified_origin",
            )
        identity = identity_from_payload(payload, origin)
        limit = _as_int(payload.get("limit"), self.plugin.config.history_bootstrap_max_messages)
        limit = max(1, min(limit, int(self.plugin.config.history_bootstrap_max_messages)))
        event = SyntheticEvent(origin)
        job_id = await self.plugin.store.create_job(
            "bootstrap_history",
            {
                "source": "astrbot_conversation",
                "scope_mode": "web_current_session",
                "session_id": identity.session_id,
                "unified_origin": identity.unified_origin,
                "group_id": identity.group_id,
                "user_id": identity.user_id,
                "limit": limit,
                "dry_run": dry_run,
            },
        )
        await self.plugin.task_queue.enqueue_bootstrap(
            BootstrapTask(
                event=event,
                identity=identity,
                job_id=job_id,
                limit=limit,
                dry_run=dry_run,
                scope_mode="web_current_session",
            )
        )
        return {"job_id": job_id, "dry_run": dry_run}


class SyntheticEvent:
    def __init__(self, unified_origin: str) -> None:
        self.unified_msg_origin = unified_origin


def identity_from_payload(payload: Dict[str, Any], origin: str) -> Identity:
    platform_id = str(payload.get("platform_id") or "unknown")
    user_id = str(payload.get("user_id") or "unknown_user")
    group_id = str(payload.get("group_id") or "")
    session_id = str(payload.get("session_id") or origin)
    persona_id = str(payload.get("persona_id") or "")
    return Identity(
        platform_id=platform_id,
        bot_id=str(payload.get("bot_id") or "bot"),
        user_id=user_id,
        group_id=group_id,
        session_id=session_id,
        persona_id=persona_id,
        unified_origin=origin,
        is_group=bool(group_id),
        timestamp=now_ms(),
    )


def success(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def failure(message: str, status: int = 400, code: str = "bad_request") -> Dict[str, Any]:
    return {"ok": False, "error": {"message": message, "code": code, "status": status}}


def unwrap_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        data = payload["data"]
        return data if isinstance(data, dict) else {"data": data}
    return payload


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)

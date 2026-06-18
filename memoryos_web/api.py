from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict

from memoryos_core.config import PLUGIN_NAME
from memoryos_core.models import ACTIVE_STATUS, Identity, MemoryItem, new_id, now_ms, visibility_for_scope
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

try:
    from astrbot.api.web import error_response, json_response, request
except Exception:  # pragma: no cover - local tests do not run AstrBot web stack.
    request = None

    def json_response(payload: Any) -> Any:
        return payload

    def error_response(message: str, status: int = 400) -> Dict[str, Any]:
        return {"error": message, "status": status}


class MemoryWebAPI:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def register(self, context: Any) -> None:
        if not hasattr(context, "register_web_api"):
            return
        routes = [
            ("memories", self.list_memories, ["GET"], "列出 MemoryOS 记忆"),
            ("memories", self.create_memory, ["POST"], "创建 MemoryOS 记忆"),
            ("memories/<memory_id>", self.update_memory, ["POST", "PUT"], "更新 MemoryOS 记忆"),
            ("memories/<memory_id>/delete", self.delete_memory, ["POST", "DELETE"], "删除 MemoryOS 记忆"),
            ("memories/<memory_id>/expire", self.expire_memory, ["POST"], "标记 MemoryOS 记忆过期"),
            ("memories/<memory_id>/logs", self.memory_logs, ["GET"], "查看 MemoryOS 召回日志"),
            ("stats", self.stats, ["GET"], "查看 MemoryOS 状态"),
            ("export", self.export_memories, ["GET"], "导出 MemoryOS 记忆"),
            ("import", self.import_memories, ["POST"], "导入 MemoryOS 记忆"),
            ("rebuild-index", self.rebuild_index, ["POST"], "重建 MemoryOS 索引"),
            ("jobs", self.jobs, ["GET"], "查看 MemoryOS 后台任务"),
            ("bootstrap/start", self.bootstrap_start, ["POST"], "启动 AstrBot 历史初始化"),
            ("bootstrap/dry-run", self.bootstrap_dry_run, ["POST"], "预览 AstrBot 历史初始化"),
            ("bootstrap/cancel", self.bootstrap_cancel, ["POST"], "取消 AstrBot 历史初始化任务"),
        ]
        for route, handler, methods, desc in routes:
            context.register_web_api(
                "/%s/%s" % (PLUGIN_NAME, route), handler, methods, desc
            )

    async def list_memories(self):
        await self.plugin.ensure_ready()
        query = _query_get("q", "")
        memory_type = _query_get("type", "")
        status = _query_get("status", ACTIVE_STATUS)
        limit = _query_get("limit", 50, int)
        offset = _query_get("offset", 0, int)
        memories = await self.plugin.store.list_memories(
            status=status,
            limit=limit,
            offset=offset,
            memory_type=memory_type,
            query=query,
        )
        return json_response({"memories": [item.to_dict() for item in memories]})

    async def create_memory(self):
        await self.plugin.ensure_ready()
        payload = await _json_body()
        content = str(payload.get("content") or "").strip()
        if not content:
            return error_response("记忆内容不能为空")
        scope = str(payload.get("scope") or "global")
        now = now_ms()
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
            confidence=float(payload.get("confidence") or 0.9),
            importance=float(payload.get("importance") or 0.7),
            created_at=now,
            updated_at=now,
        )
        await self.plugin.store.upsert_memory(memory)
        await self.plugin.embed_and_index(memory)
        return json_response({"memory": memory.to_dict()})

    async def update_memory(self, memory_id: str):
        await self.plugin.ensure_ready()
        payload = await _json_body()
        memory = await self.plugin.store.get_memory(memory_id)
        if not memory:
            return error_response("没有找到这条记忆", 404)
        content = str(payload.get("content", memory.content)).strip()
        canonical_text = str(payload.get("canonical_text", memory.canonical_text))
        tags = list(payload.get("tags", memory.tags) or [])
        importance = float(payload.get("importance", memory.importance))
        confidence = float(payload.get("confidence", memory.confidence))
        await self.plugin.store.update_memory_content(
            memory_id, content, canonical_text, tags, importance, confidence
        )
        updated = await self.plugin.store.get_memory(memory_id)
        if updated:
            await self.plugin.embed_and_index(updated)
        return json_response({"memory": updated.to_dict() if updated else None})

    async def delete_memory(self, memory_id: str):
        await self.plugin.ensure_ready()
        ok = await self.plugin.store.soft_delete_memory(memory_id)
        await self.plugin.store.delete_vector(memory_id)
        return json_response({"deleted": ok})

    async def expire_memory(self, memory_id: str):
        await self.plugin.ensure_ready()
        memory = await self.plugin.store.get_memory(memory_id)
        if not memory:
            return error_response("没有找到这条记忆", 404)
        memory.valid_to = now_ms()
        memory.updated_at = now_ms()
        await self.plugin.store.upsert_memory(memory)
        return json_response({"expired": True})

    async def memory_logs(self, memory_id: str):
        await self.plugin.ensure_ready()
        logs = await self.plugin.store.access_logs(memory_id, _query_get("limit", 100, int))
        return json_response({"logs": logs})

    async def stats(self):
        await self.plugin.ensure_ready()
        stats = await self.plugin.store.stats()
        stats["enabled"] = self.plugin.enabled
        stats["embedding_available"] = getattr(self.plugin.ai, "embedding_available", False)
        return json_response(stats)

    async def export_memories(self):
        await self.plugin.ensure_ready()
        include_raw = _query_get("include_raw", "false") in {"1", "true", "yes"}
        return json_response(await self.plugin.store.export_json(include_raw))

    async def import_memories(self):
        await self.plugin.ensure_ready()
        payload = await _json_body()
        counts = await self.plugin.store.import_json(payload)
        return json_response({"imported": counts})

    async def rebuild_index(self):
        await self.plugin.ensure_ready()
        job_id = await self.plugin.store.create_job("rebuild_index", {})
        asyncio.create_task(self.plugin.rebuild_index(job_id))
        return json_response({"job_id": job_id})

    async def jobs(self):
        await self.plugin.ensure_ready()
        job_type = _query_get("type", "")
        return json_response({"jobs": await self.plugin.store.list_jobs(job_type=job_type)})

    async def bootstrap_start(self):
        return await self._bootstrap_from_payload(dry_run=False)

    async def bootstrap_dry_run(self):
        return await self._bootstrap_from_payload(dry_run=True)

    async def bootstrap_cancel(self):
        await self.plugin.ensure_ready()
        payload = await _json_body()
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return error_response("缺少 job_id")
        self.plugin.task_queue.cancel_job(job_id)
        await self.plugin.store.update_job(job_id, "cancel_requested", {"message": "已请求取消"})
        return json_response({"cancel_requested": True, "job_id": job_id})

    async def _bootstrap_from_payload(self, dry_run: bool):
        await self.plugin.ensure_ready()
        if not self.plugin.config.history_bootstrap_enabled:
            return error_response("历史初始化功能已在配置中关闭")
        if not hasattr(self.plugin.task_queue, "enqueue_bootstrap"):
            return error_response("历史初始化队列不可用，请确认插件文件已完整更新", 503)
        payload = await _json_body()
        origin = str(payload.get("unified_origin") or "").strip()
        if not origin:
            return error_response("WebUI 启动历史初始化需要 unified_origin；聊天中可直接使用 /mem bootstrap current")
        identity = _identity_from_payload(payload, origin)
        limit = int(payload.get("limit") or self.plugin.config.history_bootstrap_max_messages)
        limit = max(1, min(limit, int(self.plugin.config.history_bootstrap_max_messages)))
        event = _SyntheticEvent(origin)
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
        return json_response({"job_id": job_id, "dry_run": dry_run})


class _SyntheticEvent:
    def __init__(self, unified_origin: str) -> None:
        self.unified_msg_origin = unified_origin


def _identity_from_payload(payload: Dict[str, Any], origin: str) -> Identity:
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


def _query_get(name: str, default: Any, caster: Any = str) -> Any:
    if request is None:
        return default
    try:
        return request.query.get(name, default, type=caster)
    except Exception:
        return default


async def _json_body() -> Dict[str, Any]:
    if request is None:
        return {}
    payload = await request.json(default={})
    return payload if isinstance(payload, dict) else {}

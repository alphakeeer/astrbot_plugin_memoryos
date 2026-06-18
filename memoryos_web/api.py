from __future__ import annotations

import asyncio
from typing import Any, Dict

from memoryos_core.config import PLUGIN_NAME
from memoryos_core.models import ACTIVE_STATUS, MemoryItem, new_id, now_ms, visibility_for_scope

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
            ("memories", self.list_memories, ["GET"], "List MemoryOS memories"),
            ("memories", self.create_memory, ["POST"], "Create MemoryOS memory"),
            ("memories/<memory_id>", self.update_memory, ["POST", "PUT"], "Update MemoryOS memory"),
            ("memories/<memory_id>/delete", self.delete_memory, ["POST", "DELETE"], "Delete MemoryOS memory"),
            ("memories/<memory_id>/expire", self.expire_memory, ["POST"], "Expire MemoryOS memory"),
            ("memories/<memory_id>/logs", self.memory_logs, ["GET"], "MemoryOS access logs"),
            ("stats", self.stats, ["GET"], "MemoryOS stats"),
            ("export", self.export_memories, ["GET"], "Export MemoryOS memories"),
            ("import", self.import_memories, ["POST"], "Import MemoryOS memories"),
            ("rebuild-index", self.rebuild_index, ["POST"], "Rebuild MemoryOS index"),
            ("jobs", self.jobs, ["GET"], "MemoryOS jobs"),
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
            return error_response("content is required")
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
            return error_response("memory not found", 404)
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
            return error_response("memory not found", 404)
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
        return json_response({"jobs": await self.plugin.store.list_jobs()})


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

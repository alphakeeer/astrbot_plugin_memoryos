from __future__ import annotations

from typing import Any, Dict

from memoryos_core.config import PLUGIN_NAME
from memoryos_core.models import ACTIVE_STATUS
from memoryos_web.service import APIError, MemoryWebService, unwrap_response

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
        self.service = MemoryWebService(plugin)

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
            ("contexts", self.contexts, ["GET"], "列出 MemoryOS 已知会话"),
            ("bootstrap/start", self.bootstrap_start, ["POST"], "启动 AstrBot 历史初始化"),
            ("bootstrap/dry-run", self.bootstrap_dry_run, ["POST"], "预览 AstrBot 历史初始化"),
            ("bootstrap/cancel", self.bootstrap_cancel, ["POST"], "取消 AstrBot 历史初始化任务"),
        ]
        for route, handler, methods, desc in routes:
            context.register_web_api(
                "/%s/%s" % (PLUGIN_NAME, route), handler, methods, desc
            )

    async def list_memories(self):
        return await self._respond(
            self.service.list_memories(
                {
                    "q": _query_get("q", ""),
                    "type": _query_get("type", ""),
                    "status": _query_get("status", ACTIVE_STATUS),
                    "limit": _query_get("limit", 50, int),
                    "offset": _query_get("offset", 0, int),
                }
            )
        )

    async def create_memory(self):
        return await self._respond(self.service.create_memory(await _json_body()))

    async def update_memory(self, memory_id: str):
        return await self._respond(
            self.service.update_memory(memory_id, await _json_body())
        )

    async def delete_memory(self, memory_id: str):
        return await self._respond(self.service.delete_memory(memory_id))

    async def expire_memory(self, memory_id: str):
        return await self._respond(self.service.expire_memory(memory_id))

    async def memory_logs(self, memory_id: str):
        return await self._respond(
            self.service.memory_logs(memory_id, {"limit": _query_get("limit", 100, int)})
        )

    async def stats(self):
        return await self._respond(self.service.stats())

    async def export_memories(self):
        return await self._respond(
            self.service.export_memories({"include_raw": _query_get("include_raw", "false")})
        )

    async def import_memories(self):
        return await self._respond(self.service.import_memories(await _json_body()))

    async def rebuild_index(self):
        return await self._respond(self.service.rebuild_index())

    async def jobs(self):
        return await self._respond(
            self.service.jobs(
                {
                    "type": _query_get("type", ""),
                    "limit": _query_get("limit", 20, int),
                }
            )
        )

    async def contexts(self):
        return await self._respond(
            self.service.contexts({"limit": _query_get("limit", 100, int)})
        )

    async def bootstrap_start(self):
        return await self._respond(self.service.bootstrap_start(await _json_body()))

    async def bootstrap_dry_run(self):
        return await self._respond(self.service.bootstrap_dry_run(await _json_body()))

    async def bootstrap_cancel(self):
        return await self._respond(self.service.bootstrap_cancel(await _json_body()))

    async def _respond(self, awaitable: Any):
        try:
            return json_response(unwrap_response(await awaitable))
        except APIError as exc:
            return error_response(exc.message, exc.status)


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

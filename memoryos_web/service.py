from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from memoryos_core.config import PLUGIN_NAME, PLUGIN_VERSION
from memoryos_core.models import (
    ACTIVE_STATUS,
    Identity,
    MemoryItem,
    RawMessage,
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
        return {"memories": [_decorate_memory(item.to_dict()) for item in memories]}

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
        await self.record_operation(
            "memory.create",
            "info",
            "已创建记忆",
            request=_safe_request(payload, ["content", "scope", "owner_key"]),
            response={"memory_id": memory.memory_id, "scope": memory.scope},
        )
        return {"memory": _decorate_memory(memory.to_dict())}

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
        await self.record_operation(
            "memory.update",
            "info",
            "已更新记忆",
            request={"memory_id": memory_id},
            response={"updated": bool(updated)},
        )
        return {"memory": _decorate_memory(updated.to_dict()) if updated else None}

    async def delete_memory(self, memory_id: str) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        ok = await self.plugin.store.soft_delete_memory(memory_id)
        await self.plugin.store.delete_vector(memory_id)
        await self.record_operation(
            "memory.delete",
            "warning" if ok else "error",
            "已删除记忆" if ok else "未找到要删除的记忆",
            request={"memory_id": memory_id},
            response={"deleted": ok},
            suggestion="" if ok else "刷新记忆列表，确认 memory_id 是否仍然存在。",
        )
        return {"deleted": ok}

    async def expire_memory(self, memory_id: str) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        memory = await self.plugin.store.get_memory(memory_id)
        if not memory:
            raise APIError("没有找到这条记忆", 404, "not_found")
        memory.valid_to = now_ms()
        memory.updated_at = now_ms()
        await self.plugin.store.upsert_memory(memory)
        await self.record_operation(
            "memory.expire",
            "info",
            "已标记记忆过期",
            request={"memory_id": memory_id},
            response={"expired": True},
        )
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
        stats["llm_provider_id"] = str(getattr(self.plugin.config, "llm_provider_id", "") or "")
        stats["embedding_provider_id"] = str(
            getattr(self.plugin.config, "embedding_provider_id", "") or ""
        )
        stats["last_llm_error"] = getattr(self.plugin.ai, "last_llm_error", "")
        stats["last_embedding_error"] = getattr(self.plugin.ai, "last_embedding_error", "")
        return stats

    async def runtime_meta(self) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        return {
            "plugin_name": PLUGIN_NAME,
            "plugin_version": PLUGIN_VERSION,
            "api_version": "2026-06-19.webui-diagnostics",
            "routes": _api_routes(),
            "config": _config_summary(self.plugin.config),
            "web": getattr(getattr(self.plugin, "standalone_web", None), "status", lambda: {})(),
        }

    async def diagnostics(self) -> Dict[str, Any]:
        stats = await self.stats()
        jobs = await self.plugin.store.list_jobs(limit=5)
        logs = await self.plugin.store.list_operation_logs(limit=8)
        checks = _health_checks(stats, self.plugin.config, self.plugin.ai)
        latest_bootstrap = next(
            (job for job in jobs if job.get("job_type") == "bootstrap_history"),
            None,
        )
        return {
            "stats": stats,
            "checks": checks,
            "latest_jobs": jobs,
            "latest_bootstrap_diagnosis": _diagnose_bootstrap_job(latest_bootstrap, self.plugin.config, self.plugin.ai)
            if latest_bootstrap
            else None,
            "recent_operation_logs": logs,
        }

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
        await self.record_operation(
            "memory.import",
            "info",
            "导入完成",
            request={"keys": sorted(payload.keys())[:12]},
            response=counts,
        )
        return {"imported": counts}

    async def rebuild_index(self) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        job_id = await self.plugin.store.create_job("rebuild_index", {})
        asyncio.create_task(self.plugin.rebuild_index(job_id))
        await self.record_operation(
            "index.rebuild",
            "info",
            "已提交索引重建任务",
            response={"job_id": job_id},
            job_id=job_id,
        )
        return {"job_id": job_id}

    async def jobs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        jobs = await self.plugin.store.list_jobs(
            limit=_as_int(params.get("limit"), 20),
            job_type=str(params.get("type") or ""),
        )
        return {"jobs": jobs}

    async def contexts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        limit = _as_int(params.get("limit"), 100)
        contexts = await self.plugin.store.list_contexts(limit=limit)
        contexts = _merge_contexts(contexts, _manager_contexts(self.plugin.context))
        return {"contexts": [_decorate_context(row) for row in contexts[:limit]]}

    async def create_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        context = context_from_payload(payload)
        await self.plugin.store.upsert_context(context)
        await self.record_operation(
            "context.save",
            "info",
            "已登记会话",
            request={"unified_origin": context["unified_origin"]},
            response=_decorate_context(context),
        )
        return {"context": _decorate_context(context)}

    async def raw_messages(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        messages = await self.plugin.store.list_raw_messages(
            session_id=str(params.get("session_id") or ""),
            user_id=str(params.get("user_id") or ""),
            group_id=str(params.get("group_id") or ""),
            platform_id=str(params.get("platform_id") or ""),
            limit=_as_int(params.get("limit"), 50),
            offset=_as_int(params.get("offset"), 0),
        )
        items = [_raw_message_preview(message) for message in messages]
        return {
            "messages": items,
            "summary": _raw_message_summary(items),
        }

    async def operation_logs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        logs = await self.plugin.store.list_operation_logs(
            limit=_as_int(params.get("limit"), 100),
            level=str(params.get("level") or ""),
            action=str(params.get("action") or ""),
        )
        return {"logs": logs}

    async def record_client_log(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        log_id = await self.record_operation(
            str(payload.get("action") or "ui.event"),
            str(payload.get("level") or "info"),
            str(payload.get("message") or "WebUI event"),
            suggestion=str(payload.get("suggestion") or ""),
            code=str(payload.get("code") or ""),
            job_id=str(payload.get("job_id") or ""),
            request=payload.get("request") if isinstance(payload.get("request"), dict) else {},
            response=payload.get("response") if isinstance(payload.get("response"), dict) else {},
        )
        return {"log_id": log_id}

    async def bootstrap_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._bootstrap_from_payload(payload, dry_run=False)

    async def bootstrap_dry_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._bootstrap_from_payload(payload, dry_run=True)

    async def bootstrap_probe(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        origin = _required_origin(payload)
        identity = identity_from_payload(payload, origin)
        limit = _as_int(payload.get("limit"), self.plugin.config.history_bootstrap_max_messages)
        limit = max(1, min(limit, int(self.plugin.config.history_bootstrap_max_messages)))
        if getattr(self.plugin, "history_source", None) is None:
            raise APIError("未配置 AstrBot 历史来源", 503, "history_source_unavailable")
        fetch = await self.plugin.history_source.fetch_current(
            SyntheticEvent(origin), identity, limit
        )
        errors = list(getattr(fetch, "errors", []) or [])
        read_messages = len(getattr(fetch, "messages", []) or [])
        return {
            "can_bootstrap": read_messages > 0 and not errors,
            "conversation_id": getattr(fetch, "conversation_id", ""),
            "read_messages": read_messages,
            "parse_skipped": int(getattr(fetch, "skipped", 0) or 0),
            "errors": errors,
            "messages": [_raw_message_preview(message) for message in list(getattr(fetch, "messages", []) or [])[:10]],
            "summary": _raw_message_summary(
                [_raw_message_preview(message) for message in list(getattr(fetch, "messages", []) or [])]
            ),
            "diagnosis": _probe_diagnosis(read_messages, errors),
        }

    async def bootstrap_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.plugin.ensure_ready()
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise APIError("缺少 job_id", 400, "missing_job_id")
        self.plugin.task_queue.cancel_job(job_id)
        await self.plugin.store.update_job(
            job_id, "cancel_requested", {"message": "已请求取消"}
        )
        await self.record_operation(
            "bootstrap.cancel",
            "warning",
            "已请求取消历史初始化任务",
            request={"job_id": job_id},
            response={"cancel_requested": True},
            job_id=job_id,
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
        origin = _required_origin(payload)
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
        await self.record_operation(
            "bootstrap.dry_run" if dry_run else "bootstrap.start",
            "info",
            "已提交历史初始化预览任务" if dry_run else "已提交历史初始化写入任务",
            request={
                "unified_origin": identity.unified_origin,
                "session_id": identity.session_id,
                "group_id": identity.group_id,
                "limit": limit,
                "dry_run": dry_run,
            },
            response={"job_id": job_id, "dry_run": dry_run},
            job_id=job_id,
        )
        return {"job_id": job_id, "dry_run": dry_run}

    async def record_operation(
        self,
        action: str,
        level: str,
        message: str,
        suggestion: str = "",
        code: str = "",
        job_id: str = "",
        request: Optional[Dict[str, Any]] = None,
        response: Optional[Dict[str, Any]] = None,
    ) -> str:
        return await self.plugin.store.record_operation(
            {
                "action": action,
                "level": level,
                "message": message,
                "suggestion": suggestion,
                "code": code,
                "job_id": job_id,
                "request": request or {},
                "response": response or {},
            }
        )


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


def context_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    origin = _required_origin(payload)
    platform_id = str(payload.get("platform_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not platform_id:
        raise APIError("缺少 platform_id：请选择已知会话，或填写平台适配器 ID，例如 aiocqhttp", 400, "missing_platform_id")
    if not session_id:
        raise APIError("缺少 session_id：请选择已知会话，或填写 AstrBot 会话 ID", 400, "missing_session_id")
    if not user_id:
        raise APIError("缺少 user_id：私聊填写用户 ID；群聊填写触发初始化的用户 ID", 400, "missing_user_id")
    group_id = str(payload.get("group_id") or "").strip()
    return {
        "unified_origin": origin,
        "platform_id": platform_id,
        "bot_id": str(payload.get("bot_id") or "bot").strip() or "bot",
        "user_id": user_id,
        "group_id": group_id,
        "session_id": session_id,
        "persona_id": str(payload.get("persona_id") or "").strip(),
        "sender_name": str(payload.get("sender_name") or "").strip(),
        "is_group": bool(group_id),
        "updated_at": now_ms(),
        "source": "web_manual",
    }


def _required_origin(payload: Dict[str, Any]) -> str:
    origin = str(payload.get("unified_origin") or "").strip()
    if origin:
        return origin
    raise APIError(
        "缺少 unified_origin：请先在 WebUI 选择已知会话，或使用“手动登记会话”填写 AstrBot 会话唯一标识。",
        400,
        "missing_unified_origin",
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


def _merge_contexts(
    stored: list[Dict[str, Any]], discovered: list[Dict[str, Any]]
) -> list[Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in stored + discovered:
        origin = str(item.get("unified_origin") or "").strip()
        if not origin:
            continue
        existing = result.get(origin, {})
        merged = dict(existing)
        merged.update({k: v for k, v in item.items() if v not in (None, "")})
        result[origin] = merged
    return sorted(
        result.values(),
        key=lambda row: int(row.get("updated_at") or 0),
        reverse=True,
    )


def _decorate_context(item: Dict[str, Any]) -> Dict[str, Any]:
    context = dict(item)
    missing = []
    for field in ("unified_origin", "platform_id", "session_id", "user_id"):
        if not str(context.get(field) or "").strip():
            missing.append(field)
    is_group = bool(context.get("is_group") or context.get("group_id"))
    platform = str(context.get("platform_id") or "unknown")
    if is_group:
        target = "群 %s" % (context.get("group_id") or "-")
    else:
        target = "用户 %s" % (context.get("user_id") or "-")
    context["is_group"] = is_group
    context["missing_fields"] = missing
    context["display_name"] = "%s · %s · %s" % (
        platform,
        target,
        context.get("unified_origin") or "",
    )
    context["field_help"] = _context_field_help()
    return context


def _decorate_memory(item: Dict[str, Any]) -> Dict[str, Any]:
    memory = dict(item)
    scope = str(memory.get("scope") or "")
    owner = str(memory.get("owner_key") or "")
    if scope == "global":
        label = "全局记忆"
    elif scope == "user_private":
        label = "用户私有：%s" % owner
    elif scope == "group_shared":
        label = "群共享：%s" % owner
    elif scope == "user_in_group":
        label = "群内用户：%s" % owner
    elif scope == "session":
        label = "会话：%s" % owner
    elif scope == "persona":
        label = "人设：%s" % owner
    else:
        label = "%s：%s" % (scope or "未知作用域", owner or "-")
    memory["scope_label"] = label
    memory["score_label"] = "重要性 %.2f / 置信度 %.2f" % (
        _as_float(memory.get("importance"), 0),
        _as_float(memory.get("confidence"), 0),
    )
    return memory


def _raw_message_preview(message: RawMessage) -> Dict[str, Any]:
    content = " ".join(str(message.content or "").split())
    noise = _raw_noise_reason(content)
    return {
        "message_id": message.message_id,
        "role": message.role,
        "platform_id": message.platform_id,
        "bot_id": message.bot_id,
        "user_id": message.user_id,
        "group_id": message.group_id,
        "session_id": message.session_id,
        "timestamp": message.timestamp,
        "processed_for_memory": bool(message.processed_for_memory),
        "content": content,
        "content_preview": content[:240],
        "noise_reason": noise,
        "usable_for_memory": not noise,
    }


def _raw_noise_reason(content: str) -> str:
    markers = [
        ("[系统任务：群聊主动破冰]", "系统主动破冰任务，不适合作为长期记忆"),
        ("CHAT_HISTORY_BEGIN", "包装后的群聊历史上下文，需只看其中真实用户发言"),
        ("think ", "模型思考/推理文本，不应写入记忆"),
        ("你被授权在群聊中发起一次", "主动消息任务提示，不是用户偏好"),
    ]
    for marker, reason in markers:
        if marker in content:
            return reason
    return ""


def _raw_message_summary(items: list[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(items)
    noisy = len([item for item in items if item.get("noise_reason")])
    user_count = len([item for item in items if item.get("role") == "user"])
    assistant_count = len([item for item in items if item.get("role") == "assistant"])
    return {
        "total": total,
        "usable": total - noisy,
        "noisy": noisy,
        "user_messages": user_count,
        "assistant_messages": assistant_count,
        "noise_ratio": round(noisy / total, 3) if total else 0,
    }


def _probe_diagnosis(read_messages: int, errors: list[str]) -> Dict[str, Any]:
    if errors:
        return {
            "level": "error",
            "message": "历史读取存在错误",
            "suggestion": "先检查会话字段是否来自同一个会话，尤其 unified_origin 和 session_id。",
        }
    if read_messages <= 0:
        return {
            "level": "warning",
            "message": "没有读取到 AstrBot 历史",
            "suggestion": "确认 AstrBot 已保存该会话历史；如果是新会话，需要先产生正常对话。",
        }
    return {
        "level": "ok",
        "message": "历史可读",
        "suggestion": "继续执行 dry-run，先查看候选再写入。",
    }


def _diagnose_bootstrap_job(
    job: Optional[Dict[str, Any]], config: Any, ai: Any
) -> Optional[Dict[str, Any]]:
    if not job:
        return None
    result = job.get("result") or {}
    errors = list(result.get("errors") or [])
    if job.get("status") == "failed" or errors:
        return {
            "level": "error",
            "message": "历史初始化失败或存在错误",
            "suggestion": "查看任务详情中的 errors/chunk_errors；优先确认会话字段和 LLM provider。",
        }
    if result.get("read_messages", 0) <= 0:
        return {
            "level": "warning",
            "message": "任务完成但没有读取到历史",
            "suggestion": "检查 AstrBot 是否保存了该会话历史，或换一个已知会话。",
        }
    if result.get("candidate_count", 0) <= 0:
        return {
            "level": "warning",
            "message": "历史可读，但没有抽取出候选记忆",
            "suggestion": _zero_candidate_suggestion(result, config, ai),
        }
    if result.get("stored_count", 0) <= 0 and not result.get("dry_run"):
        return {
            "level": "warning",
            "message": "有候选但没有写入记忆",
            "suggestion": "检查 skipped_low_confidence/skipped_low_importance/skipped_invalid_scope 计数和阈值。",
        }
    return {
        "level": "ok",
        "message": "初始化任务完成",
        "suggestion": "可在记忆管理中检查写入结果，必要时重建索引。",
    }


def _zero_candidate_suggestion(result: Dict[str, Any], config: Any, ai: Any) -> str:
    if getattr(ai, "last_llm_error", ""):
        return "LLM 调用最近有错误：%s。请检查 llm_provider_id 或当前会话模型。" % getattr(ai, "last_llm_error")
    if result.get("read_messages", 0) and result.get("chunks", 0):
        return (
            "当前历史可能多为系统提示、主动破冰或普通闲聊；也可能 LLM 返回空数组。"
            "请打开“原始历史”查看噪声标记，必要时降低历史初始化阈值或选择更有信息量的会话。"
        )
    return "请先确认历史读取正常，然后重新 dry-run。"


def _health_checks(stats: Dict[str, Any], config: Any, ai: Any) -> list[Dict[str, Any]]:
    checks = []
    checks.append(
        {
            "name": "插件启用",
            "status": "ok" if stats.get("enabled") else "error",
            "message": "MemoryOS 已启用" if stats.get("enabled") else "MemoryOS 已关闭",
            "suggestion": "" if stats.get("enabled") else "在插件配置中开启 enabled。",
        }
    )
    llm_id = str(getattr(config, "llm_provider_id", "") or "")
    checks.append(
        {
            "name": "LLM 抽取",
            "status": "ok" if llm_id or not getattr(ai, "last_llm_error", "") else "warning",
            "message": "LLM Provider：%s" % (llm_id or "跟随当前会话"),
            "suggestion": getattr(ai, "last_llm_error", "") or "",
        }
    )
    embedding_id = str(getattr(config, "embedding_provider_id", "") or "")
    checks.append(
        {
            "name": "Embedding",
            "status": "ok" if embedding_id else "warning",
            "message": "已配置向量检索" if embedding_id else "未配置 embedding，当前降级为关键词检索",
            "suggestion": "" if embedding_id else "如果需要语义检索，请在 AstrBot 配置 embedding_provider_id。",
        }
    )
    web = stats.get("standalone_web") or {}
    checks.append(
        {
            "name": "独立 Web",
            "status": "ok" if web.get("running") else "warning",
            "message": "运行中：%s" % web.get("url") if web.get("running") else "未运行或被端口占用",
            "suggestion": web.get("last_error") or "",
        }
    )
    return checks


def _config_summary(config: Any) -> Dict[str, Any]:
    return {
        "enabled": bool(getattr(config, "enabled", True)),
        "llm_provider_id": str(getattr(config, "llm_provider_id", "") or ""),
        "embedding_provider_id": str(getattr(config, "embedding_provider_id", "") or ""),
        "history_bootstrap_enabled": bool(getattr(config, "history_bootstrap_enabled", True)),
        "history_bootstrap_max_messages": int(getattr(config, "history_bootstrap_max_messages", 1000)),
        "history_bootstrap_min_importance": float(getattr(config, "history_bootstrap_min_importance", 0.65)),
        "history_bootstrap_min_confidence": float(getattr(config, "history_bootstrap_min_confidence", 0.7)),
        "history_bootstrap_group_policy": str(getattr(config, "history_bootstrap_group_policy", "conservative")),
        "standalone_web_host": str(getattr(config, "standalone_web_host", "")),
        "standalone_web_port": int(getattr(config, "standalone_web_port", 8765)),
    }


def _api_routes() -> list[str]:
    return [
        "GET stats",
        "GET runtime-meta",
        "GET diagnostics",
        "GET memories",
        "POST memories",
        "GET contexts",
        "POST contexts",
        "GET raw-messages",
        "GET jobs",
        "GET operation-logs",
        "POST operation-logs",
        "POST bootstrap/probe",
        "POST bootstrap/dry-run",
        "POST bootstrap/start",
        "POST bootstrap/cancel",
        "GET export",
        "POST import",
        "POST rebuild-index",
    ]


def _safe_request(payload: Dict[str, Any], keys: list[str]) -> Dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _context_field_help() -> Dict[str, str]:
    return {
        "unified_origin": "AstrBot 会话唯一标识，用于找到当前会话历史；优先从已知会话选择自动填充。",
        "platform_id": "平台适配器 ID，例如 aiocqhttp；用于生成记忆归属空间。",
        "session_id": "AstrBot 会话 ID；群聊通常接近群号，私聊通常接近用户或平台会话 ID。",
        "user_id": "当前用户 ID；私聊填写对话用户，群聊填写发起初始化的用户。",
        "group_id": "群号；私聊留空。",
        "bot_id": "机器人账号；不知道时可用默认 bot，已知会话会自动填充。",
    }


def _manager_contexts(context: Any) -> list[Dict[str, Any]]:
    manager = getattr(context, "conversation_manager", None)
    if manager is None:
        return []
    candidates = []
    for attr in ("conversations", "conversation_map", "_conversations"):
        value = getattr(manager, attr, None)
        if isinstance(value, dict):
            candidates.extend(_contexts_from_mapping(value))
    return candidates


def _contexts_from_mapping(value: Dict[Any, Any]) -> list[Dict[str, Any]]:
    contexts = []
    for key, item in value.items():
        if isinstance(key, tuple) and key:
            origin = str(key[0] or "")
        else:
            origin = str(getattr(item, "unified_msg_origin", "") or key or "")
        if not origin:
            continue
        contexts.append(
            {
                "unified_origin": origin,
                "session_id": str(getattr(item, "cid", "") or getattr(item, "session_id", "") or ""),
                "updated_at": now_ms(),
                "source": "astrbot_conversation_manager",
            }
        )
    return contexts

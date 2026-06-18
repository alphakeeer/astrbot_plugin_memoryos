from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from memoryos_commands.formatting import (
    format_memory_list,
    format_search_results,
    help_text,
    status_text,
)
from memoryos_core.config import PLUGIN_DISPLAY_NAME, PLUGIN_NAME, PLUGIN_VERSION, MemoryOSConfig
from memoryos_core.context_manager import ShortTermContextManager
from memoryos_core.extractor import MemoryExtractor
from memoryos_core.gate import MemoryGate
from memoryos_core.history import AstrBotHistorySource
from memoryos_core.identity import IdentityResolver, extract_message_text, is_command_text
from memoryos_core.injector import MemoryInjector
from memoryos_core.models import (
    ACTIVE_STATUS,
    SCOPE_GROUP_SHARED,
    Identity,
    MemoryCandidate,
    MemoryItem,
    allowed_scope_rules,
    new_id,
)
from memoryos_core.providers import AstrBotAI
from memoryos_core.resolver import MemoryResolver
from memoryos_core.retriever import MemoryRetriever
from memoryos_core.scheduler import MemoryTaskQueue
try:
    from memoryos_core.tasks import BootstrapTask, ExtractionTask
except Exception:  # pragma: no cover - protects partially updated plugin installs.
    try:
        from memoryos_core.scheduler import BootstrapTask, ExtractionTask  # type: ignore[attr-defined]
    except Exception:
        @dataclass
        class ExtractionTask:
            event: Any
            identity: Any
            session_key: str

        @dataclass
        class BootstrapTask:
            event: Any
            identity: Any
            job_id: str
            limit: int
            dry_run: bool = False
            source: str = "astrbot_conversation"
            scope_mode: str = "current_session"
from memoryos_storage.sqlite_store import SQLiteMemoryStore
from memoryos_web.api import MemoryWebAPI

try:
    from astrbot.api import logger
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.provider import LLMResponse, ProviderRequest
    from astrbot.api.star import Context, Star, register
    from astrbot.core.agent.message import TextPart
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - lets unit tests import the plugin locally.
    import logging

    logger = logging.getLogger(PLUGIN_NAME)

    class _Filter:
        class PermissionType:
            ADMIN = "admin"

        def command(self, *args: Any, **kwargs: Any) -> Any:
            return _identity_decorator

        def permission_type(self, *args: Any, **kwargs: Any) -> Any:
            return _identity_decorator

        def on_astrbot_loaded(self, *args: Any, **kwargs: Any) -> Any:
            return _identity_decorator

        def on_llm_request(self, *args: Any, **kwargs: Any) -> Any:
            return _identity_decorator

        def on_llm_response(self, *args: Any, **kwargs: Any) -> Any:
            return _identity_decorator

    def _identity_decorator(func: Any) -> Any:
        return func

    def register(*args: Any, **kwargs: Any) -> Any:
        return _identity_decorator

    class Star:
        def __init__(self, context: Any) -> None:
            self.context = context
            self.name = PLUGIN_NAME

    class Context:
        pass

    class AstrMessageEvent:
        pass

    class ProviderRequest:
        def __init__(self) -> None:
            self.extra_user_content_parts: List[Any] = []

    class LLMResponse:
        completion_text = ""

    class TextPart:
        def __init__(self, text: str) -> None:
            self.text = text

        def mark_as_temp(self) -> "TextPart":
            return self

    filter = _Filter()

    def get_astrbot_data_path() -> str:
        return str(Path.cwd() / ".data")


@register(
    "memoryos",
    "Sato",
    "Structured long-term memory system for AstrBot",
    PLUGIN_VERSION,
)
class MemoryOSPlugin(Star):
    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = MemoryOSConfig.from_mapping(config or {})
        self.enabled = bool(self.config.enabled)
        self.identity_resolver = IdentityResolver()
        self.data_dir = self._resolve_data_dir()
        self.store = SQLiteMemoryStore(self.data_dir / "memoryos.sqlite3")
        self.ai = AstrBotAI(context, self.config)
        self.short_context = ShortTermContextManager(
            max_turns=max(24, self.config.extraction_window_turns * 2),
            extract_every_n_pairs=self.config.auto_extract_every_n_pairs,
        )
        self.extractor = MemoryExtractor(self.ai, self.config)
        self.resolver = MemoryResolver(self.store, self.config)
        self.retriever = MemoryRetriever(self.store, self.ai, self.config)
        self.gate = MemoryGate(self.ai, self.config)
        self.injector = MemoryInjector(self.config)
        self.history_source = AstrBotHistorySource(context)
        try:
            self.task_queue = MemoryTaskQueue(
                self.store,
                self.short_context,
                self.extractor,
                self.resolver,
                self.ai,
                self.config,
                self.history_source,
            )
        except TypeError:
            self.task_queue = MemoryTaskQueue(
                self.store,
                self.short_context,
                self.extractor,
                self.resolver,
                self.ai,
                self.config,
            )
        self.web_api = MemoryWebAPI(self)
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._disabled_origins: set[str] = set()
        self._disabled_groups: set[str] = set()
        self._group_policies: Dict[str, str] = {}
        self.web_api.register(context)

    def _resolve_data_dir(self) -> Path:
        if self.config.data_dir:
            base = Path(self.config.data_dir)
        else:
            base = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            await self.store.init()
            await self.task_queue.start()
            self._ready = True
            logger.info("%s initialized at %s", PLUGIN_DISPLAY_NAME, self.data_dir)

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        await self.ensure_ready()

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        await self.ensure_ready()
        if not self.enabled:
            return
        identity = self.identity_resolver.resolve(event)
        if self._is_disabled(identity):
            return
        query = extract_message_text(event)
        if not query or is_command_text(query):
            return
        candidates = await self.retriever.retrieve(query, identity)
        selected = await self.gate.select(event, query, candidates)
        memory_context = self.injector.pack(selected)
        if not memory_context:
            return
        if getattr(req, "extra_user_content_parts", None) is None:
            req.extra_user_content_parts = []
        req.extra_user_content_parts.append(TextPart(text=memory_context).mark_as_temp())
        request_id = new_id("req")
        for candidate in selected:
            candidate.used_in_prompt = True
            await self.store.record_access(
                candidate.memory.memory_id,
                request_id,
                identity.session_id,
                True,
                candidate.final_score,
            )

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        await self.ensure_ready()
        if not self.enabled or not self.config.auto_memory_enabled:
            return
        identity = self.identity_resolver.resolve(event)
        if self._is_disabled(identity):
            return
        user_text = extract_message_text(event)
        if not user_text or is_command_text(user_text):
            return
        assistant_text = str(getattr(resp, "completion_text", "") or "")
        await self.store.append_raw_turn(identity, user_text, assistant_text)
        self.short_context.add_turn_pair(
            identity.session_key, user_text, assistant_text, identity.message_id
        )
        if self.short_context.should_extract(identity.session_key):
            await self.task_queue.enqueue_extract(
                ExtractionTask(event, identity, identity.session_key)
            )

    @filter.command("mem")
    async def mem(self, event: AstrMessageEvent):
        """MemoryOS command entrypoint."""
        await self.ensure_ready()
        identity = self.identity_resolver.resolve(event)
        text = extract_message_text(event)
        args = _strip_command(text, "mem")
        try:
            result = await self._handle_mem_command(event, identity, args)
        except Exception as exc:
            logger.exception("MemoryOS command failed")
            result = "MemoryOS 执行失败：%s" % exc
        yield event.plain_result(result)

    async def _handle_mem_command(
        self, event: AstrMessageEvent, identity: Identity, args: str
    ) -> str:
        if not args or args in {"help", "-h", "--help"}:
            return help_text()
        head, tail = _split_head(args)
        if head == "remember":
            return await self._cmd_remember(identity, tail, explicit=True)
        if head == "search":
            return await self._cmd_search(event, identity, tail)
        if head == "list":
            rules = allowed_scope_rules(identity, self.config)
            memories = await self.store.list_memories(rules, limit=30)
            return format_memory_list(memories)
        if head == "forget":
            return await self._cmd_forget(identity, tail)
        if head == "summarize":
            return await self._cmd_summarize(event, identity)
        if head == "bootstrap":
            return await self._cmd_bootstrap(event, identity, tail)
        if head == "status":
            text = status_text(await self.store.stats(), self.ai, self.enabled)
            jobs = await self.store.list_jobs(limit=1, job_type="bootstrap_history")
            bootstrap_line = "历史初始化：%s" % (
                "可用" if self.config.history_bootstrap_enabled else "已关闭"
            )
            if jobs:
                bootstrap_line += "，最近任务 %s [%s]" % (
                    jobs[0].get("job_id", ""),
                    jobs[0].get("status", ""),
                )
            return text + "\n" + bootstrap_line
        if head == "on":
            self._enable(identity)
            return "MemoryOS：已在当前会话启用记忆。"
        if head == "off":
            self._disable(identity)
            return "MemoryOS：已在当前会话停用记忆。"
        if head == "export":
            return await self._cmd_export()
        if head == "import":
            return await self._cmd_import(tail)
        if head == "rebuild-index":
            job_id = await self.store.create_job("rebuild_index", {})
            asyncio.create_task(self.rebuild_index(job_id))
            return "MemoryOS：已提交索引重建任务，任务 ID：%s" % job_id
        if head == "group":
            return await self._cmd_group(event, identity, tail)
        return help_text()

    async def _cmd_remember(
        self, identity: Identity, content: str, explicit: bool = True, scope: str = ""
    ) -> str:
        if not self.config.explicit_memory_enabled:
            return "MemoryOS：显式记忆命令已在配置中关闭。"
        content = content.strip()
        if not content:
            return "用法：/mem remember <内容>"
        candidate = MemoryCandidate(
            should_store=True,
            scope=scope or "",
            memory_type=_guess_type(content),
            content=content,
            canonical_text=content,
            confidence=0.98 if explicit else 0.85,
            importance=0.9 if explicit else 0.7,
            reason="explicit command",
        )
        memory = candidate.to_memory(identity)
        stored_id = await self.resolver.resolve_and_store(memory)
        stored = await self.store.get_memory(stored_id)
        if stored:
            await self.embed_and_index(stored)
        return "MemoryOS：已记住，记忆 ID：%s" % stored_id

    async def _cmd_search(
        self, event: AstrMessageEvent, identity: Identity, query: str
    ) -> str:
        if not query.strip():
            return "用法：/mem search <查询内容>"
        candidates = await self.retriever.retrieve(query, identity)
        return format_search_results(candidates[: self.config.retrieval_top_k])

    async def _cmd_forget(self, identity: Identity, tail: str) -> str:
        target = tail.strip()
        if not target:
            return "用法：/mem forget <memory_id> 或 /mem forget all"
        rules = allowed_scope_rules(identity, self.config)
        if target == "all":
            memories = await self.store.list_memories(rules, limit=10000)
            for memory in memories:
                await self.store.soft_delete_memory(memory.memory_id)
                await self.store.delete_vector(memory.memory_id)
            return "MemoryOS：已删除当前范围内 %d 条记忆。" % len(memories)
        memory = await self.store.get_memory(target)
        allowed = {(rule.scope, rule.owner_key) for rule in rules}
        if not memory or (memory.scope, memory.owner_key) not in allowed:
            return "MemoryOS：当前范围内没有找到这条记忆。"
        await self.store.soft_delete_memory(target)
        await self.store.delete_vector(target)
        return "MemoryOS：已删除记忆：%s" % target

    async def _cmd_summarize(self, event: AstrMessageEvent, identity: Identity) -> str:
        messages = await self.store.recent_raw_messages(
            identity.session_id, self.config.extraction_window_turns
        )
        if not messages:
            return "MemoryOS：当前会话还没有可总结的最近消息。"
        transcript = "\n".join("%s: %s" % (m.role, m.content) for m in messages)
        prompt = "请为 MemoryOS 记忆审计总结以下最近会话，不超过 180 个中文字符：\n%s" % transcript
        summary = await self.ai.llm_generate(event, prompt)
        return summary or transcript[-500:]

    async def _cmd_export(self) -> str:
        payload = await self.store.export_json(self.config.export_include_raw_messages)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(text) > 3500:
            export_path = self.data_dir / "memoryos_export.json"
            export_path.write_text(text, encoding="utf-8")
            return "MemoryOS：导出内容较长，已写入文件：%s" % export_path
        return text

    async def _cmd_import(self, raw_json: str) -> str:
        if not raw_json.strip():
            return "用法：/mem import <json>"
        payload = json.loads(raw_json)
        counts = await self.store.import_json(payload)
        return "MemoryOS：导入完成：%s" % counts

    async def _cmd_bootstrap(
        self, event: AstrMessageEvent, identity: Identity, tail: str
    ) -> str:
        if not self.config.history_bootstrap_enabled:
            return "MemoryOS：历史初始化功能已在配置中关闭。"
        head, rest = _split_head(tail)
        if head in {"", "current", "dry-run"}:
            if not hasattr(self.task_queue, "enqueue_bootstrap"):
                return "MemoryOS：历史初始化队列不可用，请确认插件文件已完整更新。"
            dry_run = head == "dry-run"
            limit = _parse_limit(rest, self.config.history_bootstrap_max_messages)
            payload = {
                "source": "astrbot_conversation",
                "scope_mode": "current_session",
                "session_id": identity.session_id,
                "unified_origin": identity.unified_origin,
                "group_id": identity.group_id,
                "user_id": identity.user_id,
                "limit": limit,
                "dry_run": dry_run,
            }
            job_id = await self.store.create_job("bootstrap_history", payload)
            await self.task_queue.enqueue_bootstrap(
                BootstrapTask(
                    event=event,
                    identity=identity,
                    job_id=job_id,
                    limit=limit,
                    dry_run=dry_run,
                )
            )
            action = "预览" if dry_run else "初始化"
            return "MemoryOS：已提交 AstrBot 历史记忆%s任务，任务 ID：%s" % (
                action,
                job_id,
            )
        if head == "status":
            jobs = await self.store.list_jobs(limit=10, job_type="bootstrap_history")
            return _format_bootstrap_jobs(jobs)
        if head == "cancel":
            job_id = rest.strip()
            if not job_id:
                return "用法：/mem bootstrap cancel <job_id>"
            self.task_queue.cancel_job(job_id)
            await self.store.update_job(job_id, "cancel_requested", {"message": "已请求取消"})
            return "MemoryOS：已请求取消历史初始化任务：%s" % job_id
        return "用法：/mem bootstrap current [数量]、/mem bootstrap dry-run [数量]、/mem bootstrap status、/mem bootstrap cancel <job_id>"

    async def _cmd_group(
        self, event: AstrMessageEvent, identity: Identity, tail: str
    ) -> str:
        if not identity.is_group:
            return "MemoryOS：群聊命令只能在群聊中使用。"
        head, rest = _split_head(tail)
        if head == "list":
            memories = await self.store.list_memories(
                allowed_scopes=[
                    _Rule(SCOPE_GROUP_SHARED, identity.group_space),
                    _Rule("user_in_group", identity.user_in_group_key),
                ],
                limit=30,
            )
            return format_memory_list(memories)
        admin_required = head in {"on", "off", "remember", "forget", "policy"}
        if head == "bootstrap":
            admin_required = bool(self.config.history_bootstrap_group_requires_admin)
        if admin_required and not _is_admin(event):
            return "MemoryOS：该群聊管理命令需要管理员权限。"
        if head == "on":
            self._disabled_groups.discard(identity.group_space)
            return "MemoryOS：已启用本群记忆。"
        if head == "off":
            self._disabled_groups.add(identity.group_space)
            return "MemoryOS：已停用本群记忆。"
        if head == "remember":
            return await self._cmd_remember(
                identity, rest, explicit=True, scope=SCOPE_GROUP_SHARED
            )
        if head == "forget":
            memory_id = rest.strip()
            if not memory_id:
                return "用法：/mem group forget <memory_id>"
            memory = await self.store.get_memory(memory_id)
            if not memory or memory.owner_key != identity.group_space:
                return "MemoryOS：没有找到这条本群共享记忆。"
            await self.store.soft_delete_memory(memory_id)
            await self.store.delete_vector(memory_id)
            return "MemoryOS：已删除本群共享记忆：%s" % memory_id
        if head == "policy":
            policy = rest.strip()
            if policy not in {"conservative", "normal", "aggressive"}:
                return "用法：/mem group policy conservative|normal|aggressive"
            self._group_policies[identity.group_space] = policy
            return "MemoryOS：本群记忆策略已设为 %s。" % policy
        if head == "bootstrap":
            if not hasattr(self.task_queue, "enqueue_bootstrap"):
                return "MemoryOS：历史初始化队列不可用，请确认插件文件已完整更新。"
            rest_text = rest.strip()
            mode, amount = _split_head(rest_text)
            if mode == "dry-run":
                dry_run = True
                limit_text = amount
            elif mode == "current":
                dry_run = False
                limit_text = amount
            elif not mode:
                dry_run = False
                limit_text = ""
            elif _is_int_text(mode):
                dry_run = False
                limit_text = rest_text
            else:
                return "用法：/mem group bootstrap [数量] 或 /mem group bootstrap dry-run [数量]"
            limit = _parse_limit(limit_text, self.config.history_bootstrap_max_messages)
            payload = {
                "source": "astrbot_conversation",
                "scope_mode": "current_group_session",
                "session_id": identity.session_id,
                "unified_origin": identity.unified_origin,
                "group_id": identity.group_id,
                "limit": limit,
                "dry_run": dry_run,
            }
            job_id = await self.store.create_job("bootstrap_history", payload)
            await self.task_queue.enqueue_bootstrap(
                BootstrapTask(
                    event=event,
                    identity=identity,
                    job_id=job_id,
                    limit=limit,
                    dry_run=dry_run,
                    scope_mode="current_group_session",
                )
            )
            action = "预览" if dry_run else "初始化"
            return "MemoryOS：已提交本群 AstrBot 历史记忆%s任务，任务 ID：%s" % (
                action,
                job_id,
            )
        return help_text()

    async def embed_and_index(self, memory: MemoryItem) -> None:
        vector = await self.ai.embed(memory.embedding_text())
        if vector:
            await self.store.upsert_vector(memory, vector)

    async def rebuild_index(self, job_id: str = "") -> Dict[str, Any]:
        if job_id:
            await self.store.update_job(job_id, "running", {})
        count = 0
        try:
            memories = await self.store.list_memories(status=ACTIVE_STATUS, limit=100000)
            for memory in memories:
                await self.embed_and_index(memory)
                count += 1
            result = {"indexed": count}
            if job_id:
                await self.store.update_job(job_id, "done", result)
            return result
        except Exception as exc:
            result = {"error": str(exc), "indexed": count}
            if job_id:
                await self.store.update_job(job_id, "failed", result)
            return result

    def _is_disabled(self, identity: Identity) -> bool:
        return (
            identity.unified_origin in self._disabled_origins
            or identity.group_space in self._disabled_groups
        )

    def _disable(self, identity: Identity) -> None:
        if identity.is_group:
            self._disabled_groups.add(identity.group_space)
        else:
            self._disabled_origins.add(identity.unified_origin)

    def _enable(self, identity: Identity) -> None:
        if identity.is_group:
            self._disabled_groups.discard(identity.group_space)
        else:
            self._disabled_origins.discard(identity.unified_origin)

    async def terminate(self) -> None:
        try:
            await self.task_queue.stop()
        finally:
            await self.store.close()


class _Rule:
    def __init__(self, scope: str, owner_key: str) -> None:
        self.scope = scope
        self.owner_key = owner_key


def _strip_command(text: str, command: str) -> str:
    stripped = (text or "").strip()
    prefixes = ["/" + command, "!" + command, command]
    for prefix in prefixes:
        if stripped == prefix:
            return ""
        if stripped.startswith(prefix + " "):
            return stripped[len(prefix) :].strip()
    return stripped


def _split_head(text: str) -> tuple[str, str]:
    stripped = (text or "").strip()
    if not stripped:
        return "", ""
    parts = stripped.split(None, 1)
    if len(parts) == 1:
        return parts[0].lower(), ""
    return parts[0].lower(), parts[1]


def _is_admin(event: Any) -> bool:
    checker = getattr(event, "is_admin", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    role = getattr(event, "role", "")
    return str(role).lower() == "admin"


def _parse_limit(text: str, default: int) -> int:
    try:
        value = int(str(text or "").strip().split()[0])
    except (IndexError, TypeError, ValueError):
        value = int(default)
    return max(1, min(value, int(default)))


def _is_int_text(text: str) -> bool:
    try:
        int(str(text or "").strip())
        return True
    except (TypeError, ValueError):
        return False


def _format_bootstrap_jobs(jobs: List[Dict[str, Any]]) -> str:
    if not jobs:
        return "MemoryOS：还没有历史初始化任务。"
    lines = ["MemoryOS 历史初始化任务："]
    for job in jobs:
        result = job.get("result") or {}
        lines.append(
            "%s [%s] 读取=%s 候选=%s 写入=%s dry-run=%s"
            % (
                job.get("job_id", ""),
                job.get("status", ""),
                result.get("read_messages", 0),
                result.get("candidate_count", 0),
                result.get("stored_count", 0),
                result.get("dry_run", False),
            )
        )
        message = result.get("message") or ""
        if message:
            lines.append("  %s" % message)
    return "\n".join(lines)


def _guess_type(content: str) -> str:
    lower = content.lower()
    if "叫我" in content or "call me" in lower:
        return "nickname"
    if "喜欢" in content or "偏好" in content or "prefer" in lower:
        return "preference"
    if "项目" in content or "project" in lower:
        return "project_state"
    if "不要" in content or "不是" in content or "no longer" in lower:
        return "correction"
    return "fact"

from __future__ import annotations

import re
from typing import Any, Iterable, List

from .models import (
    SCOPE_GROUP_SHARED,
    SCOPE_PERSONA,
    SCOPE_SESSION,
    SCOPE_USER_IN_GROUP,
    SCOPE_USER_PRIVATE,
    Identity,
    MemoryCandidate,
    RawMessage,
)
from .prompts import MEMORY_BOOTSTRAP_PROMPT, MEMORY_EXTRACTION_PROMPT
from .providers import extract_json


EXPLICIT_PATTERNS = [
    re.compile(r"^(?:请)?记住[:：]?\s*(.+)$", re.I),
    re.compile(r"^(?:帮我)?记一下[:：]?\s*(.+)$", re.I),
    re.compile(r"^remember(?: that)?[:：]?\s*(.+)$", re.I),
]


class MemoryExtractor:
    def __init__(self, ai: Any, config: Any) -> None:
        self.ai = ai
        self.config = config

    def parse_explicit(self, text: str, identity: Identity) -> List[MemoryCandidate]:
        body = ""
        for pattern in EXPLICIT_PATTERNS:
            match = pattern.search((text or "").strip())
            if match:
                body = match.group(1).strip()
                break
        if not body:
            return []
        scope = self._explicit_scope(body, identity)
        body = self._strip_scope_prefix(body)
        return [
            MemoryCandidate(
                should_store=True,
                scope=scope,
                memory_type=_guess_memory_type(body),
                content=body,
                canonical_text=body,
                confidence=0.98,
                importance=0.9,
                sensitivity="normal",
                reason="explicit remember command",
            )
        ]

    async def extract_from_turns(
        self, event: Any, identity: Identity, turns: Iterable[Any]
    ) -> List[MemoryCandidate]:
        transcript = _format_turns(turns)
        if not transcript.strip():
            return []
        prompt = (
            MEMORY_EXTRACTION_PROMPT
            + "\nConversation type: "
            + ("group" if identity.is_group else "private")
            + "\nCurrent identity:\n"
            + _identity_block(identity)
            + "\nConversation:\n"
            + transcript
        )
        text = await self.ai.llm_generate(event, prompt)
        candidates = self._parse_llm_candidates(text, identity)
        if candidates:
            return candidates
        return self._heuristic_extract(transcript, identity)

    async def extract_from_history_chunk(
        self, event: Any, identity: Identity, messages: Iterable[RawMessage]
    ) -> List[MemoryCandidate]:
        raw_messages = list(messages)
        transcript = _format_raw_messages(raw_messages)
        if not transcript.strip():
            return []
        prompt = (
            MEMORY_BOOTSTRAP_PROMPT
            + "\n会话类型："
            + ("群聊" if identity.is_group else "私聊")
            + "\n当前身份：\n"
            + _identity_block(identity)
            + "\n历史消息：\n"
            + transcript
        )
        text = await self.ai.llm_generate(event, prompt)
        candidates = self._parse_llm_candidates(text, identity, bootstrap=True)
        if candidates:
            message_ids = [m.message_id for m in raw_messages]
            for candidate in candidates:
                if not candidate.source_message_ids:
                    candidate.source_message_ids = message_ids
            return candidates
        return self._heuristic_extract(transcript, identity)

    def _parse_llm_candidates(
        self, text: str, identity: Identity, bootstrap: bool = False
    ) -> List[MemoryCandidate]:
        payload = extract_json(text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        candidates = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            candidate = MemoryCandidate.from_dict(item)
            if not candidate.content:
                continue
            if not bootstrap and not self._scope_allowed_for_extract(candidate.scope, identity, bootstrap):
                continue
            candidates.append(candidate)
        return candidates

    def _heuristic_extract(
        self, transcript: str, identity: Identity
    ) -> List[MemoryCandidate]:
        candidates: List[MemoryCandidate] = []
        for line in transcript.splitlines():
            explicit = self.parse_explicit(line.split(":", 1)[-1].strip(), identity)
            candidates.extend(explicit)
        if identity.is_group:
            return candidates
        if getattr(self.config, "private_auto_capture_level", "normal") in {
            "normal",
            "aggressive",
        }:
            project_hint = _project_hint(transcript)
            if project_hint:
                candidates.append(
                    MemoryCandidate(
                        should_store=True,
                        scope=SCOPE_USER_PRIVATE,
                        memory_type="project_state",
                        content=project_hint,
                        canonical_text=project_hint,
                        confidence=0.65,
                        importance=0.62,
                        reason="heuristic project-state extraction",
                    )
                )
        return candidates

    def _explicit_scope(self, body: str, identity: Identity) -> str:
        lower = body.lower()
        if identity.is_group:
            if lower.startswith(("group ", "群 ", "群聊 ", "本群")):
                return SCOPE_GROUP_SHARED
            return SCOPE_USER_IN_GROUP
        return SCOPE_USER_PRIVATE

    def _strip_scope_prefix(self, body: str) -> str:
        prefixes = ["group ", "me ", "群 ", "群聊 ", "本群", "我 "]
        stripped = body.strip()
        lower = stripped.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                return stripped[len(prefix) :].strip(" :：")
        return stripped

    def _scope_allowed_for_extract(
        self, scope: str, identity: Identity, bootstrap: bool = False
    ) -> bool:
        if identity.is_group:
            if scope == SCOPE_USER_PRIVATE:
                return False
            if scope == SCOPE_GROUP_SHARED:
                return getattr(self.config, "group_memory_enabled", True)
            if bootstrap and scope == SCOPE_USER_IN_GROUP:
                return bool(getattr(self.config, "history_bootstrap_allow_user_in_group", True))
            return scope in {SCOPE_USER_IN_GROUP, SCOPE_SESSION}
        if bootstrap:
            return scope in {SCOPE_USER_PRIVATE, SCOPE_SESSION, SCOPE_PERSONA}
        return scope not in {SCOPE_GROUP_SHARED, SCOPE_USER_IN_GROUP}


def _guess_memory_type(text: str) -> str:
    lower = text.lower()
    if "叫我" in text or "call me" in lower or "nickname" in lower:
        return "nickname"
    if "喜欢" in text or "偏好" in text or "prefer" in lower:
        return "preference"
    if "项目" in text or "project" in lower:
        return "project_state"
    if "不要" in text or "不是" in text or "correction" in lower:
        return "correction"
    return "fact"


def _format_turns(turns: Iterable[Any]) -> str:
    lines = []
    for turn in turns:
        role = getattr(turn, "role", "user")
        content = getattr(turn, "content", "")
        if content:
            lines.append("%s: %s" % (role, content))
    return "\n".join(lines)


def _format_raw_messages(messages: Iterable[RawMessage]) -> str:
    lines = []
    for message in messages:
        if not message.content:
            continue
        timestamp = str(message.timestamp or "")
        speaker = message.user_id if message.role == "user" else message.role
        lines.append(
            "[%s] %s %s: %s"
            % (message.message_id, timestamp, speaker, message.content)
        )
    return "\n".join(lines)


def _identity_block(identity: Identity) -> str:
    return (
        "platform=%s\nuser=%s\ngroup=%s\nsession=%s\npersona=%s"
        % (
            identity.platform_id,
            identity.user_id,
            identity.group_id,
            identity.session_id,
            identity.persona_id,
        )
    )


def _project_hint(transcript: str) -> str:
    markers = ["项目", "插件", "实现", "架构", "project", "plugin", "implement"]
    if not any(marker.lower() in transcript.lower() for marker in markers):
        return ""
    lines = [line for line in transcript.splitlines() if line.strip()]
    if not lines:
        return ""
    text = " ".join(line.split(":", 1)[-1].strip() for line in lines[-4:])
    if len(text) > 240:
        text = text[:237] + "..."
    return "用户近期正在推进的项目/任务上下文：" + text

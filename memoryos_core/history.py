from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import Identity, RawMessage, now_ms


@dataclass
class HistoryFetchResult:
    messages: List[RawMessage]
    conversation_id: str = ""
    source: str = "astrbot_conversation"
    skipped: int = 0
    errors: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class AstrBotHistorySource:
    """Read the current AstrBot conversation history and normalize it."""

    def __init__(self, context: Any) -> None:
        self.context = context

    async def fetch_current(
        self, event: Any, identity: Identity, limit: int
    ) -> HistoryFetchResult:
        manager = getattr(self.context, "conversation_manager", None)
        if manager is None:
            return HistoryFetchResult([], skipped=0, errors=["AstrBot 未提供 conversation_manager"])

        origin = getattr(event, "unified_msg_origin", "") or identity.unified_origin
        if not origin:
            return HistoryFetchResult([], skipped=0, errors=["当前事件缺少 unified_msg_origin"])

        cid = await _maybe_call(manager, "get_curr_conversation_id", origin)
        if not cid:
            return HistoryFetchResult([], skipped=0, errors=["当前 AstrBot 会话没有历史记录 ID"])

        conversation = await _get_conversation(manager, origin, cid)
        if conversation is None:
            return HistoryFetchResult(
                [], conversation_id=str(cid), errors=["未找到当前 AstrBot 会话历史"]
            )

        raw_history = getattr(conversation, "history", conversation)
        entries, skipped, errors = _decode_history(raw_history)
        if limit > 0:
            entries = entries[-int(limit) :]

        messages: List[RawMessage] = []
        for index, entry in enumerate(entries):
            message, ok = _entry_to_raw_message(entry, index, str(cid), identity)
            if ok and message and message.content:
                messages.append(message)
            else:
                skipped += 1
        return HistoryFetchResult(
            messages=messages,
            conversation_id=str(cid),
            skipped=skipped,
            errors=errors,
        )


async def _get_conversation(manager: Any, origin: str, cid: Any) -> Any:
    for args in ((origin, cid), (cid,), (origin,)):
        try:
            result = getattr(manager, "get_conversation")(*args)
            return await _maybe_await(result)
        except TypeError:
            continue
        except Exception:
            return None
    return None


async def _maybe_call(obj: Any, name: str, *args: Any) -> Any:
    func = getattr(obj, name, None)
    if not callable(func):
        return None
    try:
        return await _maybe_await(func(*args))
    except Exception:
        return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _decode_history(raw_history: Any) -> Tuple[List[Any], int, List[str]]:
    errors: List[str] = []
    skipped = 0
    value = raw_history
    if value in (None, ""):
        return [], 0, []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [], 0, ["AstrBot 会话历史不是有效 JSON"]
    if isinstance(value, dict):
        for key in ("history", "messages", "conversation"):
            nested = value.get(key)
            if isinstance(nested, list):
                value = nested
                break
    if not isinstance(value, list):
        return [], 1, ["AstrBot 会话历史格式无法识别"]
    entries = []
    for item in value:
        if item in (None, ""):
            skipped += 1
            continue
        entries.append(item)
    return entries, skipped, errors


def _entry_to_raw_message(
    entry: Any, index: int, conversation_id: str, identity: Identity
) -> Tuple[Optional[RawMessage], bool]:
    role, content, sender_id, timestamp = _extract_entry(entry)
    content = _normalize_content(content)
    if not content:
        return None, False
    role = _normalize_role(role)
    sender_id = _slug(sender_id or (identity.user_id if role == "user" else identity.bot_id))
    timestamp = _normalize_ts(timestamp, identity.timestamp or now_ms())
    message_id = "astrhist:%s:%d:%s" % (
        conversation_id,
        index,
        hashlib.sha1(("%s:%s:%s" % (role, sender_id, content)).encode("utf-8")).hexdigest()[
            :12
        ],
    )
    return (
        RawMessage(
            message_id=message_id,
            platform_id=identity.platform_id,
            bot_id=identity.bot_id,
            user_id=sender_id if role == "user" else identity.user_id,
            group_id=identity.group_id,
            session_id=identity.session_id,
            persona_id=identity.persona_id,
            role=role,
            content=content,
            message_type="astrbot_history",
            timestamp=timestamp,
        ),
        True,
    )


def _extract_entry(entry: Any) -> Tuple[str, Any, str, Any]:
    if isinstance(entry, str):
        return "user", entry, "", None
    if not isinstance(entry, dict):
        return "user", str(entry), "", None
    role = _first(entry, "role", "type", "sender_type")
    content = _first(entry, "content", "text", "message", "message_str")
    sender = _first(entry, "sender_id", "user_id", "uid", "sender")
    if isinstance(sender, dict):
        sender = _first(sender, "user_id", "id", "uid", "nickname", "name")
    timestamp = _first(entry, "timestamp", "time", "created_at")
    if content in (None, ""):
        content = _segments_to_text(
            _first(entry, "segments", "message_chain", "message", "content")
        )
    return str(role or "user"), content, str(sender or ""), timestamp


def _segments_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "")
        if "data" in value:
            return _segments_to_text(value.get("data"))
        return " ".join(_segments_to_text(v) for v in value.values()).strip()
    if isinstance(value, Iterable):
        parts = []
        for item in value:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                data = item.get("data", item)
                text = _segments_to_text(data)
                if text:
                    parts.append(text if item_type != "image" else "[图片]")
            else:
                text = _segments_to_text(item)
                if text:
                    parts.append(text)
        return " ".join(parts).strip()
    return ""


def _normalize_content(value: Any) -> str:
    if isinstance(value, (list, tuple, dict)):
        value = _segments_to_text(value)
    return " ".join(str(value or "").split()).strip()


def _normalize_role(role: str) -> str:
    lower = str(role or "").lower()
    if lower in {"assistant", "ai", "bot", "system"}:
        return "assistant" if lower != "system" else "system"
    return "user"


def _normalize_ts(value: Any, default: int) -> int:
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        ts = int(default or now_ms())
    if ts and ts < 10_000_000_000:
        ts *= 1000
    return ts or now_ms()


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return ""


def _slug(value: str) -> str:
    return str(value or "").replace(" ", "_").replace("/", "_").replace("\\", "_")

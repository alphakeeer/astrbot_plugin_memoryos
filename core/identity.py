from __future__ import annotations

from typing import Any

from .models import Identity, now_ms


class IdentityResolver:
    def resolve(self, event: Any) -> Identity:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)

        platform_id = _first_text(
            getattr(event, "platform_id", ""),
            _call(event, "get_platform_name"),
            getattr(message_obj, "platform_id", ""),
            getattr(message_obj, "type", ""),
            "unknown",
        )
        bot_id = _first_text(
            getattr(message_obj, "self_id", ""),
            getattr(event, "self_id", ""),
            "bot",
        )
        user_id = _first_text(
            getattr(sender, "user_id", ""),
            getattr(sender, "id", ""),
            getattr(sender, "uid", ""),
            getattr(event, "user_id", ""),
            "unknown_user",
        )
        group_id = _first_text(
            getattr(message_obj, "group_id", ""),
            getattr(event, "group_id", ""),
            "",
        )
        session_id = _first_text(
            getattr(message_obj, "session_id", ""),
            getattr(event, "session_id", ""),
            getattr(event, "unified_msg_origin", ""),
            "%s:%s:%s" % (platform_id, group_id or "private", user_id),
        )
        persona_id = _first_text(
            getattr(event, "persona_id", ""),
            getattr(message_obj, "persona_id", ""),
            "",
        )
        unified_origin = _first_text(
            getattr(event, "unified_msg_origin", ""),
            "%s:%s" % (platform_id, session_id),
        )
        message_id = _first_text(
            getattr(message_obj, "message_id", ""),
            getattr(event, "message_id", ""),
            "",
        )
        sender_name = _first_text(
            _call(event, "get_sender_name"),
            getattr(sender, "nickname", ""),
            getattr(sender, "name", ""),
            user_id,
        )
        timestamp = _safe_int(getattr(message_obj, "timestamp", 0), 0)
        if timestamp and timestamp < 10_000_000_000:
            timestamp *= 1000
        if not timestamp:
            timestamp = now_ms()

        return Identity(
            platform_id=_slug(platform_id),
            bot_id=_slug(bot_id),
            user_id=_slug(user_id),
            group_id=_slug(group_id),
            session_id=_slug(session_id),
            persona_id=_slug(persona_id),
            unified_origin=str(unified_origin or ""),
            message_id=str(message_id or ""),
            sender_name=str(sender_name or ""),
            is_group=bool(group_id),
            timestamp=timestamp,
        )


def extract_message_text(event: Any) -> str:
    message_obj = getattr(event, "message_obj", None)
    text = _first_text(
        getattr(event, "message_str", ""),
        getattr(message_obj, "message_str", ""),
        getattr(event, "plain_text", ""),
        "",
    )
    return str(text or "").strip()


def is_command_text(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("/") or stripped.startswith("!")


def _call(obj: Any, name: str) -> Any:
    func = getattr(obj, name, None)
    if not callable(func):
        return ""
    try:
        return func()
    except TypeError:
        return ""
    except Exception:
        return ""


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slug(value: str) -> str:
    text = str(value or "")
    return text.replace(" ", "_").replace("/", "_").replace("\\", "_")


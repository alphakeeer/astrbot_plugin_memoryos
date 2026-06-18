from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


ACTIVE_STATUS = "active"
DELETED_STATUS = "deleted"
SUPERSEDED_STATUS = "superseded"
UNCERTAIN_STATUS = "uncertain"

SCOPE_USER_PRIVATE = "user_private"
SCOPE_USER_IN_GROUP = "user_in_group"
SCOPE_GROUP_SHARED = "group_shared"
SCOPE_SESSION = "session"
SCOPE_PERSONA = "persona"
SCOPE_GLOBAL = "global"


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def loads_json(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return [] if default is None else default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return [] if default is None else default


@dataclass
class Identity:
    platform_id: str
    bot_id: str
    user_id: str
    group_id: str
    session_id: str
    persona_id: str
    unified_origin: str
    message_id: str = ""
    sender_name: str = ""
    is_group: bool = False
    timestamp: int = 0

    @property
    def actor_key(self) -> str:
        return "%s:%s" % (self.platform_id, self.user_id)

    @property
    def private_space(self) -> str:
        return "%s:private:%s" % (self.platform_id, self.user_id)

    @property
    def group_space(self) -> str:
        return "%s:group:%s" % (self.platform_id, self.group_id)

    @property
    def session_key(self) -> str:
        return "%s:session:%s" % (self.platform_id, self.session_id)

    @property
    def persona_key(self) -> str:
        return "persona:%s" % (self.persona_id or "default")

    @property
    def user_in_group_key(self) -> str:
        return "%s:user:%s" % (self.group_space, self.user_id)

    def owner_key_for_scope(self, scope: str) -> str:
        if scope == SCOPE_USER_PRIVATE:
            return self.private_space
        if scope == SCOPE_USER_IN_GROUP:
            return self.user_in_group_key
        if scope == SCOPE_GROUP_SHARED:
            return self.group_space
        if scope == SCOPE_SESSION:
            return self.session_key
        if scope == SCOPE_PERSONA:
            return self.persona_key
        return "global"


@dataclass
class ScopeRule:
    scope: str
    owner_key: str


@dataclass
class MemoryItem:
    memory_id: str
    scope: str
    owner_key: str
    visibility: str
    platform_id: str = ""
    bot_id: str = ""
    user_id: str = ""
    group_id: str = ""
    session_id: str = ""
    persona_id: str = ""
    memory_type: str = "fact"
    content: str = ""
    canonical_text: str = ""
    entities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    source_message_ids: List[str] = field(default_factory=list)
    source_summary: str = ""
    confidence: float = 0.8
    importance: float = 0.5
    sensitivity: str = "normal"
    status: str = ACTIVE_STATUS
    valid_from: Optional[int] = None
    valid_to: Optional[int] = None
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
    last_accessed_at: Optional[int] = None
    access_count: int = 0

    def embedding_text(self) -> str:
        return self.canonical_text or self.content

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "MemoryItem":
        data = dict(row)
        data["entities"] = loads_json(data.pop("entities_json", None), [])
        data["tags"] = loads_json(data.pop("tags_json", None), [])
        data["source_message_ids"] = loads_json(
            data.pop("source_message_ids_json", None), []
        )
        return cls(**data)


@dataclass
class RawMessage:
    message_id: str
    platform_id: str
    bot_id: str
    user_id: str
    group_id: str
    session_id: str
    persona_id: str
    role: str
    content: str
    message_type: str
    timestamp: int
    processed_for_memory: int = 0


@dataclass
class MemoryCandidate:
    should_store: bool
    scope: str
    memory_type: str
    content: str
    canonical_text: str = ""
    entities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.8
    importance: float = 0.5
    sensitivity: str = "normal"
    valid_from: Optional[int] = None
    valid_to: Optional[int] = None
    reason: str = ""
    source_message_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryCandidate":
        validity = data.get("validity") or {}
        return cls(
            should_store=bool(data.get("should_store", True)),
            scope=str(data.get("scope") or SCOPE_USER_PRIVATE),
            memory_type=str(data.get("memory_type") or "fact"),
            content=str(data.get("content") or "").strip(),
            canonical_text=str(data.get("canonical_text") or "").strip(),
            entities=_as_str_list(data.get("entities")),
            tags=_as_str_list(data.get("tags")),
            confidence=_safe_float(data.get("confidence"), 0.8),
            importance=_safe_float(data.get("importance"), 0.5),
            sensitivity=str(data.get("sensitivity") or "normal"),
            valid_from=_safe_optional_int(validity.get("valid_from")),
            valid_to=_safe_optional_int(validity.get("valid_to")),
            reason=str(data.get("reason") or ""),
            source_message_ids=_as_str_list(data.get("source_message_ids")),
        )

    def to_memory(self, identity: Identity) -> MemoryItem:
        scope = normalize_scope(self.scope, identity)
        now = now_ms()
        visibility = visibility_for_scope(scope)
        return MemoryItem(
            memory_id=new_id("mem"),
            scope=scope,
            owner_key=identity.owner_key_for_scope(scope),
            visibility=visibility,
            platform_id=identity.platform_id,
            bot_id=identity.bot_id,
            user_id=identity.user_id,
            group_id=identity.group_id,
            session_id=identity.session_id,
            persona_id=identity.persona_id,
            memory_type=self.memory_type,
            content=self.content,
            canonical_text=self.canonical_text,
            entities=self.entities,
            tags=self.tags,
            source_message_ids=self.source_message_ids,
            source_summary=self.reason,
            confidence=max(0.0, min(1.0, self.confidence)),
            importance=max(0.0, min(1.0, self.importance)),
            sensitivity=self.sensitivity,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            created_at=now,
            updated_at=now,
        )


@dataclass
class RetrievalCandidate:
    memory: MemoryItem
    vector_similarity: float = 0.0
    keyword_score: float = 0.0
    entity_overlap: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    access_score: float = 0.0
    stale_penalty: float = 0.0
    sensitivity_penalty: float = 0.0
    scope_risk_penalty: float = 0.0
    final_score: float = 0.0
    used_in_prompt: bool = False


def normalize_scope(scope: str, identity: Identity) -> str:
    scope = (scope or "").strip()
    valid = {
        SCOPE_USER_PRIVATE,
        SCOPE_USER_IN_GROUP,
        SCOPE_GROUP_SHARED,
        SCOPE_SESSION,
        SCOPE_PERSONA,
        SCOPE_GLOBAL,
    }
    if scope not in valid:
        return SCOPE_USER_IN_GROUP if identity.is_group else SCOPE_USER_PRIVATE
    if identity.is_group and scope == SCOPE_USER_PRIVATE:
        return SCOPE_USER_IN_GROUP
    if not identity.is_group and scope in {SCOPE_USER_IN_GROUP, SCOPE_GROUP_SHARED}:
        return SCOPE_USER_PRIVATE
    return scope


def visibility_for_scope(scope: str) -> str:
    if scope == SCOPE_USER_PRIVATE:
        return "private"
    if scope == SCOPE_USER_IN_GROUP:
        return "group_member_context"
    if scope == SCOPE_GROUP_SHARED:
        return "group"
    if scope == SCOPE_SESSION:
        return "session"
    if scope == SCOPE_PERSONA:
        return "persona"
    return "global"


def allowed_scope_rules(identity: Identity, config: Any) -> List[ScopeRule]:
    rules = [
        ScopeRule(SCOPE_SESSION, identity.session_key),
    ]
    if identity.persona_id and getattr(config, "use_persona_scope_filter", True):
        rules.append(ScopeRule(SCOPE_PERSONA, identity.persona_key))
    if identity.is_group:
        if getattr(config, "group_memory_enabled", True):
            rules.append(ScopeRule(SCOPE_GROUP_SHARED, identity.group_space))
            rules.append(ScopeRule(SCOPE_USER_IN_GROUP, identity.user_in_group_key))
        if getattr(config, "allow_private_memory_in_group", False):
            rules.append(ScopeRule(SCOPE_USER_PRIVATE, identity.private_space))
    else:
        if getattr(config, "private_memory_enabled", True):
            rules.append(ScopeRule(SCOPE_USER_PRIVATE, identity.private_space))
        if getattr(config, "allow_group_memory_in_private", False) and identity.group_id:
            rules.append(ScopeRule(SCOPE_GROUP_SHARED, identity.group_space))
    return rules


def scope_rule_tuples(rules: Iterable[ScopeRule]) -> List[Tuple[str, str]]:
    return [(rule.scope, rule.owner_key) for rule in rules]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


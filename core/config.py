from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


PLUGIN_NAME = "astrbot_plugin_memoryos"
PLUGIN_DISPLAY_NAME = "MemoryOS"
PLUGIN_VERSION = "1.0.0"


@dataclass
class MemoryOSConfig:
    enabled: bool = True
    llm_provider_id: str = ""
    embedding_provider_id: str = ""
    vector_backend: str = "sqlite"
    db_backend: str = "sqlite"

    auto_memory_enabled: bool = True
    explicit_memory_enabled: bool = True
    group_memory_enabled: bool = True
    private_memory_enabled: bool = True

    auto_extract_every_n_pairs: int = 5
    extraction_window_turns: int = 12
    min_importance_to_store: float = 0.55
    min_confidence_to_store: float = 0.6
    retrieval_top_k: int = 8
    retrieval_candidate_multiplier: int = 3
    injection_token_budget: int = 1200
    max_memory_chars: int = 260

    private_auto_capture_level: str = "normal"
    group_auto_capture_level: str = "conservative"
    default_group_policy: str = "conservative"

    use_user_scope_filter: bool = True
    use_group_scope_filter: bool = True
    use_persona_scope_filter: bool = True
    allow_private_memory_in_group: bool = False
    allow_group_memory_in_private: bool = False

    enable_conflict_resolution: bool = True
    enable_time_decay: bool = True
    memory_gate_mode: str = "heuristic"

    memory_ttl_days_for_tasks: int = 30
    memory_ttl_days_for_group_topics: int = 14
    raw_message_retention_days: int = 180
    export_include_raw_messages: bool = False

    command_prefix: str = "/mem"
    data_dir: str = ""

    @classmethod
    def from_mapping(cls, raw: Any) -> "MemoryOSConfig":
        data: Dict[str, Any] = {}
        if isinstance(raw, dict):
            data = raw

        kwargs = {}
        for field_name, field_def in cls.__dataclass_fields__.items():  # type: ignore[attr-defined]
            value = data.get(field_name, field_def.default)
            kwargs[field_name] = _coerce_value(value, field_def.default)
        cfg = cls(**kwargs)
        cfg.auto_extract_every_n_pairs = max(1, int(cfg.auto_extract_every_n_pairs))
        cfg.extraction_window_turns = max(2, int(cfg.extraction_window_turns))
        cfg.retrieval_top_k = max(1, int(cfg.retrieval_top_k))
        cfg.retrieval_candidate_multiplier = max(
            1, int(cfg.retrieval_candidate_multiplier)
        )
        cfg.injection_token_budget = max(100, int(cfg.injection_token_budget))
        cfg.min_importance_to_store = _clamp01(cfg.min_importance_to_store)
        cfg.min_confidence_to_store = _clamp01(cfg.min_confidence_to_store)
        if cfg.memory_gate_mode not in {"off", "heuristic", "llm"}:
            cfg.memory_gate_mode = "heuristic"
        if cfg.private_auto_capture_level not in {"conservative", "normal", "aggressive"}:
            cfg.private_auto_capture_level = "normal"
        if cfg.group_auto_capture_level not in {"conservative", "normal", "aggressive"}:
            cfg.group_auto_capture_level = "conservative"
        if cfg.default_group_policy not in {"conservative", "normal", "aggressive"}:
            cfg.default_group_policy = "conservative"
        return cfg


def _coerce_value(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return "" if value is None else str(value)
    return value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


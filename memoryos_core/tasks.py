from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Identity


@dataclass
class ExtractionTask:
    event: Any
    identity: Identity
    session_key: str


@dataclass
class BootstrapTask:
    event: Any
    identity: Identity
    job_id: str
    limit: int
    dry_run: bool = False
    source: str = "astrbot_conversation"
    scope_mode: str = "current_session"

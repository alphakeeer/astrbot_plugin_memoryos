from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List

from .models import now_ms


@dataclass
class ContextTurn:
    role: str
    content: str
    timestamp: int
    message_id: str = ""


class ShortTermContextManager:
    def __init__(self, max_turns: int = 24, extract_every_n_pairs: int = 5) -> None:
        self.max_turns = max(4, max_turns)
        self.extract_every_n_pairs = max(1, extract_every_n_pairs)
        self._turns: Dict[str, Deque[ContextTurn]] = defaultdict(
            lambda: deque(maxlen=self.max_turns)
        )
        self._pair_counts: Dict[str, int] = defaultdict(int)

    def add_turn_pair(
        self,
        session_key: str,
        user_text: str,
        assistant_text: str,
        user_message_id: str = "",
    ) -> None:
        if user_text:
            self._turns[session_key].append(
                ContextTurn("user", user_text, now_ms(), user_message_id)
            )
        if assistant_text:
            self._turns[session_key].append(
                ContextTurn("assistant", assistant_text, now_ms(), "")
            )
        self._pair_counts[session_key] += 1

    def snapshot(self, session_key: str, limit: int) -> List[ContextTurn]:
        turns = list(self._turns.get(session_key, []))
        return turns[-limit:]

    def should_extract(self, session_key: str) -> bool:
        return self._pair_counts.get(session_key, 0) >= self.extract_every_n_pairs

    def mark_extracted(self, session_key: str) -> None:
        self._pair_counts[session_key] = 0


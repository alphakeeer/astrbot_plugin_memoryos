from core.config import MemoryOSConfig
from core.gate import MemoryGate
from core.injector import MemoryInjector
from core.models import MemoryItem, RetrievalCandidate
from core.providers import DeterministicTestAI


def test_injector_packs_memory_context_with_budget():
    cfg = MemoryOSConfig(injection_token_budget=100, max_memory_chars=40)
    memory = MemoryItem(
        memory_id="mem_1",
        scope="user_private",
        owner_key="p:private:u",
        visibility="private",
        memory_type="preference",
        content="用户喜欢中文解释，并希望术语中英文对照。" * 10,
        confidence=0.9,
    )
    text = MemoryInjector(cfg).pack([RetrievalCandidate(memory=memory, final_score=1)])

    assert text.startswith("<memory_context>")
    assert 'id="mem_1"' in text
    assert len(text) < 700


def test_gate_blocks_scope_risk():
    cfg = MemoryOSConfig()
    gate = MemoryGate(DeterministicTestAI(), cfg)
    private = MemoryItem(
        memory_id="mem_private",
        scope="user_private",
        owner_key="p:private:u",
        visibility="private",
        content="secret",
    )
    candidate = RetrievalCandidate(
        memory=private,
        final_score=0.9,
        scope_risk_penalty=1.0,
    )

    selected = gate._heuristic("secret", [candidate])

    assert selected == []


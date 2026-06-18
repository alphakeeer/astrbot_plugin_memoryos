import asyncio
import tempfile
from pathlib import Path

from memoryos_core.models import MemoryItem, ScopeRule
from memoryos_storage.sqlite_store import SQLiteMemoryStore


def test_store_migration_keyword_vector_and_export_import():
    asyncio.run(_run_store_test())


async def _run_store_test():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
        await store.init()
        memory = MemoryItem(
            memory_id="mem_1",
            scope="user_private",
            owner_key="aiocqhttp:private:u1",
            visibility="private",
            memory_type="project_state",
            content="用户正在实现 AstrBot MemoryOS 插件。",
            canonical_text="AstrBot MemoryOS plugin implementation",
            importance=0.9,
        )
        await store.upsert_memory(memory)
        await store.upsert_vector(memory, [1.0, 0.0, 0.0])

        rules = [ScopeRule("user_private", "aiocqhttp:private:u1")]
        keyword = await store.keyword_search("AstrBot", rules, 5)
        vector = await store.vector_search([1.0, 0.0, 0.0], rules, 5)
        exported = await store.export_json()

        assert keyword[0][0] == "mem_1"
        assert vector[0][0] == "mem_1"
        assert exported["memories"][0]["memory_id"] == "mem_1"

        store2 = SQLiteMemoryStore(Path(tmp) / "memory2.sqlite3")
        await store2.init()
        counts = await store2.import_json(exported)
        imported = await store2.get_memory("mem_1")

        assert counts["memories"] == 1
        assert imported is not None
        assert imported.content.startswith("用户正在实现")

        await store.close()
        await store2.close()


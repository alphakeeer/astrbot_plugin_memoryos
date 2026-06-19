import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from main import MemoryOSPlugin
from memoryos_core.history import AstrBotHistorySource
from memoryos_core.identity import IdentityResolver
from memoryos_core.scheduler import chunk_raw_messages
from memoryos_web.service import APIError, MemoryWebService


class FakeConversationManager:
    def __init__(self, history):
        self.history = history

    async def get_curr_conversation_id(self, unified_origin):
        return "conv-1" if unified_origin else ""

    async def get_conversation(self, unified_origin, conversation_id):
        return SimpleNamespace(history=self.history)


class FakeContext:
    def __init__(self, history, llm_payload=None):
        self.conversation_manager = FakeConversationManager(history)
        self.llm_payload = llm_payload or []

    async def get_current_chat_provider_id(self, unified_origin):
        return "fake-provider"

    async def llm_generate(self, **kwargs):
        return SimpleNamespace(completion_text=json.dumps(self.llm_payload, ensure_ascii=False))


class FakeEvent:
    def __init__(self, text="/mem bootstrap current 20", user_id="u1", group_id=""):
        sender = SimpleNamespace(user_id=user_id, nickname=user_id)
        self.message_obj = SimpleNamespace(
            self_id="bot",
            session_id="s-" + (group_id or user_id),
            message_id="msg1",
            group_id=group_id,
            sender=sender,
            message_str=text,
            timestamp=1_700_000_000,
        )
        self.message_str = text
        self.unified_msg_origin = "umo-" + (group_id or user_id)
        self.role = "admin"

    def get_sender_name(self):
        return "User"

    def get_platform_name(self):
        return "test"

    def is_admin(self):
        return self.role == "admin"


def test_astrbot_history_source_parses_json_and_segments():
    asyncio.run(_run_history_parse())


def test_bootstrap_dry_run_does_not_write_memories():
    asyncio.run(_run_bootstrap_dry_run())


def test_group_bootstrap_blocks_private_scope():
    asyncio.run(_run_group_scope_filter())


def test_web_context_registration_and_probe():
    asyncio.run(_run_web_context_registration_and_probe())


def test_web_bootstrap_missing_origin_is_web_friendly():
    asyncio.run(_run_web_bootstrap_missing_origin())


def test_chunk_raw_messages_overlap():
    messages = [SimpleNamespace(message_id=str(i)) for i in range(7)]
    chunks = chunk_raw_messages(messages, 3, 1)
    assert [[m.message_id for m in chunk] for chunk in chunks] == [
        ["0", "1", "2"],
        ["2", "3", "4"],
        ["4", "5", "6"],
        ["6"],
    ]


async def _run_history_parse():
    history = json.dumps(
        [
            {"role": "user", "content": [{"type": "text", "data": {"text": "记住我喜欢中文"}}], "timestamp": 1},
            {"role": "assistant", "content": "好的", "timestamp": 2},
            {"role": "user", "content": "", "timestamp": 3},
        ],
        ensure_ascii=False,
    )
    event = FakeEvent("hi")
    identity = IdentityResolver().resolve(event)
    result = await AstrBotHistorySource(FakeContext(history)).fetch_current(
        event, identity, 10
    )
    assert result.conversation_id == "conv-1"
    assert len(result.messages) == 2
    assert result.skipped == 1
    assert result.messages[0].message_id.startswith("astrhist:conv-1:")
    assert result.messages[0].content == "记住我喜欢中文"


async def _run_bootstrap_dry_run():
    history = [
        {"role": "user", "content": "我开发 MemoryOS 插件时偏好中文说明", "timestamp": 1}
    ]
    llm_payload = [
        {
            "should_store": True,
            "scope": "user_private",
            "memory_type": "preference",
            "content": "用户开发 MemoryOS 插件时偏好中文说明。",
            "canonical_text": "用户偏好中文说明。",
            "confidence": 0.95,
            "importance": 0.9,
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            FakeContext(history, llm_payload),
            {
                "data_dir": str(Path(tmp)),
                "embedding_provider_id": "",
                "standalone_web_enabled": False,
            },
        )
        await plugin.ensure_ready()
        event = FakeEvent("/mem bootstrap dry-run 20")
        reply = await plugin._cmd_bootstrap(
            event, plugin.identity_resolver.resolve(event), "dry-run 20"
        )
        assert "任务 ID" in reply
        job = await _wait_job(plugin)
        assert job["status"] == "done"
        assert job["result"]["candidate_count"] == 1
        assert job["result"]["stored_count"] == 0
        assert job["result"]["preview"][0]["content"].startswith("用户开发")
        memories = await plugin.store.list_memories(limit=10)
        assert memories == []
        await plugin.terminate()


async def _run_group_scope_filter():
    history = [{"role": "user", "content": "本群长期讨论 MemoryOS 插件", "timestamp": 1}]
    llm_payload = [
        {
            "should_store": True,
            "scope": "user_private",
            "memory_type": "preference",
            "content": "不应该保存到私聊。",
            "confidence": 0.99,
            "importance": 0.99,
        },
        {
            "should_store": True,
            "scope": "group_shared",
            "memory_type": "group_fact",
            "content": "本群长期讨论 MemoryOS 插件。",
            "confidence": 0.95,
            "importance": 0.9,
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            FakeContext(history, llm_payload),
            {
                "data_dir": str(Path(tmp)),
                "embedding_provider_id": "",
                "standalone_web_enabled": False,
            },
        )
        await plugin.ensure_ready()
        event = FakeEvent("/mem group bootstrap 20", group_id="g1")
        reply = await plugin._cmd_group(
            event, plugin.identity_resolver.resolve(event), "bootstrap 20"
        )
        assert "任务 ID" in reply
        job = await _wait_job(plugin)
        assert job["status"] == "done"
        assert job["result"]["skipped_invalid_scope"] == 1
        memories = await plugin.store.list_memories(limit=10)
        assert len(memories) == 1
        assert memories[0].scope == "group_shared"
        await plugin.terminate()


async def _run_web_context_registration_and_probe():
    history = [{"role": "user", "content": "我希望 MemoryOS 优先通过 WebUI 管理", "timestamp": 1}]
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            FakeContext(history, []),
            {
                "data_dir": str(Path(tmp)),
                "embedding_provider_id": "",
                "standalone_web_enabled": False,
            },
        )
        await plugin.ensure_ready()
        service = MemoryWebService(plugin)
        payload = {
            "unified_origin": "umo-web",
            "platform_id": "aiocqhttp",
            "bot_id": "bot-1",
            "session_id": "session-web",
            "user_id": "user-1",
            "group_id": "",
            "limit": 20,
        }
        created = await service.create_context(payload)
        assert created["context"]["display_name"].startswith("aiocqhttp")
        contexts = await service.contexts({"limit": 10})
        assert contexts["contexts"][0]["unified_origin"] == "umo-web"
        probe = await service.bootstrap_probe(payload)
        assert probe["can_bootstrap"] is True
        assert probe["read_messages"] == 1
        assert probe["conversation_id"] == "conv-1"
        await plugin.terminate()


async def _run_web_bootstrap_missing_origin():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            FakeContext([], []),
            {
                "data_dir": str(Path(tmp)),
                "embedding_provider_id": "",
                "standalone_web_enabled": False,
            },
        )
        await plugin.ensure_ready()
        service = MemoryWebService(plugin)
        try:
            await service.bootstrap_dry_run({"limit": 20})
        except APIError as exc:
            assert exc.code == "missing_unified_origin"
            assert "WebUI" in exc.message
            assert "/mem bootstrap" not in exc.message
        else:
            raise AssertionError("missing unified_origin should fail")
        await plugin.terminate()


async def _wait_job(plugin):
    for _ in range(60):
        jobs = await plugin.store.list_jobs(limit=1, job_type="bootstrap_history")
        if jobs and jobs[0]["status"] in {"done", "failed", "cancelled"}:
            return jobs[0]
        await asyncio.sleep(0.05)
    raise AssertionError("bootstrap job did not finish")

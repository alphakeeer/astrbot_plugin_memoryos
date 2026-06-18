import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from main import MemoryOSPlugin, ProviderRequest


class FakeEvent:
    def __init__(self, text, user_id="u1", group_id=""):
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

    def plain_result(self, text):
        return text


def test_private_memory_not_injected_into_group_by_default():
    asyncio.run(_run_integration())


def test_group_plain_remember_is_user_in_group_not_group_shared():
    asyncio.run(_run_group_remember_scope())


async def _run_integration():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            SimpleNamespace(),
            {
                "data_dir": str(Path(tmp)),
                "auto_memory_enabled": False,
                "embedding_provider_id": "",
            },
        )
        await plugin.ensure_ready()

        private_identity = plugin.identity_resolver.resolve(FakeEvent("hi", "u1"))
        await plugin._cmd_remember(private_identity, "用户的私聊秘密项目是 X")

        group_event = FakeEvent("秘密项目是什么？", "u1", "g1")
        req = ProviderRequest()
        await plugin.on_llm_request(group_event, req)

        assert getattr(req, "extra_user_content_parts", []) == []
        await plugin.terminate()


async def _run_group_remember_scope():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            SimpleNamespace(),
            {
                "data_dir": str(Path(tmp)),
                "auto_memory_enabled": False,
                "embedding_provider_id": "",
            },
        )
        await plugin.ensure_ready()
        identity = plugin.identity_resolver.resolve(FakeEvent("hi", "u1", "g1"))
        memory_id = (await plugin._cmd_remember(identity, "在这个群里叫我小王")).split()[-1]
        memory = await plugin.store.get_memory(memory_id)

        assert memory is not None
        assert memory.scope == "user_in_group"
        assert memory.owner_key == "test:group:g1:user:u1"
        await plugin.terminate()

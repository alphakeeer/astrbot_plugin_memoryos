from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from main import MemoryOSPlugin
from memoryos_web.service import MemoryWebService


class FakeConversationManager:
    def __init__(self, history):
        self.history = history

    async def get_curr_conversation_id(self, unified_origin):
        return "conv-smoke" if unified_origin else ""

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


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


async def wait_job(service):
    for _ in range(80):
        jobs = (await service.jobs({"limit": 5}))["jobs"]
        if jobs and jobs[0]["status"] in {"done", "failed", "cancelled"}:
            return jobs[0]
        await asyncio.sleep(0.05)
    raise AssertionError("bootstrap job did not finish")


async def smoke_api():
    history = [
        {"role": "user", "content": "请记住本群长期讨论 MemoryOS WebUI 改造", "timestamp": 1}
    ]
    llm_payload = [
        {
            "should_store": True,
            "scope": "group_shared",
            "memory_type": "group_fact",
            "content": "本群长期讨论 MemoryOS WebUI 改造。",
            "canonical_text": "本群长期讨论 MemoryOS WebUI 改造。",
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
        service = MemoryWebService(plugin)
        payload = {
            "unified_origin": "umo-smoke",
            "platform_id": "aiocqhttp",
            "bot_id": "bot",
            "user_id": "user-1",
            "group_id": "group-1",
            "session_id": "session-1",
            "limit": 20,
        }

        meta = await service.runtime_meta()
        assert_true("diagnostics" in " ".join(meta["routes"]), "runtime-meta routes missing diagnostics")

        created = await service.create_context(payload)
        assert_true(created["context"]["unified_origin"] == "umo-smoke", "context create failed")

        probe = await service.bootstrap_probe(payload)
        assert_true(probe["read_messages"] == 1, "probe did not read history")
        assert_true(probe["diagnosis"]["level"] == "ok", "probe diagnosis should be ok")

        dry = await service.bootstrap_dry_run(payload)
        assert_true(dry["job_id"], "dry-run did not create job")
        job = await wait_job(service)
        assert_true(job["status"] == "done", "dry-run job failed")
        assert_true(job["result"]["candidate_count"] == 1, "dry-run candidate count wrong")
        assert_true(job["result"]["stored_count"] == 0, "dry-run wrote memory")

        start = await service.bootstrap_start(payload)
        assert_true(start["job_id"], "start did not create job")
        job = await wait_job(service)
        assert_true(job["status"] == "done", "start job failed")
        memories = (await service.list_memories({"limit": 20}))["memories"]
        assert_true(len(memories) == 1, "start did not write memory")

        raw = await service.raw_messages({"session_id": "session-1", "limit": 20})
        assert_true(raw["summary"]["total"] >= 1, "raw messages missing")

        logs = await service.operation_logs({"limit": 20})
        assert_true(len(logs["logs"]) >= 3, "operation logs missing")

        diagnostics = await service.diagnostics()
        assert_true(len(diagnostics["checks"]) >= 3, "diagnostics checks missing")

        await plugin.terminate()


def smoke_static_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "pages" / "memoryos" / "index.html").read_text(encoding="utf-8")
    js = (root / "pages" / "memoryos" / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    refs = set(re.findall(r'byId\("([^"]+)"\)', js))
    missing = sorted(refs - ids)
    assert_true(not missing, "JS references missing HTML ids: %s" % missing)
    required_routes = [
        "runtime-meta",
        "diagnostics",
        "operation-logs",
        "raw-messages",
        "bootstrap/probe",
        "bootstrap/dry-run",
        "bootstrap/start",
    ]
    for route in required_routes:
        assert_true(route in js, "route missing from app.js: %s" % route)


async def main():
    smoke_static_contract()
    await smoke_api()
    print("MemoryOS smoke checks passed")


if __name__ == "__main__":
    asyncio.run(main())

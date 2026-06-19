import asyncio
import json
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from main import MemoryOSPlugin
from memoryos_web.standalone import StandaloneWebServer


def test_standalone_server_serves_index_and_stats():
    asyncio.run(_run_standalone_smoke())


def test_standalone_rejects_path_traversal():
    asyncio.run(_run_path_traversal())


def test_non_loopback_requires_token():
    plugin = SimpleNamespace(
        config=SimpleNamespace(
            standalone_web_enabled=True,
            standalone_web_host="0.0.0.0",
            standalone_web_port=8765,
            standalone_web_auth_token="",
        )
    )
    server = StandaloneWebServer(plugin, Path.cwd() / "pages" / "memoryos")
    server.start(asyncio.new_event_loop())
    assert not server.status()["running"]
    assert "必须配置" in server.status()["last_error"]


async def _run_standalone_smoke():
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            SimpleNamespace(),
            {
                "data_dir": str(Path(tmp)),
                "auto_memory_enabled": False,
                "embedding_provider_id": "",
                "standalone_web_enabled": True,
                "standalone_web_port": port,
            },
        )
        await plugin.ensure_ready()
        try:
            index = await asyncio.to_thread(_get, "http://127.0.0.1:%d/" % port)
            assert "MemoryOS 控制台" in index
            stats = json.loads(
                await asyncio.to_thread(_get, "http://127.0.0.1:%d/api/stats" % port)
            )
            assert stats["ok"] is True
            assert "active_memories" in stats["data"]
            contexts = json.loads(
                await asyncio.to_thread(_get, "http://127.0.0.1:%d/api/contexts" % port)
            )
            assert contexts["ok"] is True
            assert "contexts" in contexts["data"]
        finally:
            await plugin.terminate()


async def _run_path_traversal():
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        plugin = MemoryOSPlugin(
            SimpleNamespace(),
            {
                "data_dir": str(Path(tmp)),
                "auto_memory_enabled": False,
                "embedding_provider_id": "",
                "standalone_web_enabled": True,
                "standalone_web_port": port,
            },
        )
        await plugin.ensure_ready()
        try:
            try:
                _get("http://127.0.0.1:%d/../README.md" % port)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("path traversal was not rejected")
        finally:
            await plugin.terminate()


def _get(url):
    deadline = time.time() + 3
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError("request failed: %s" % last_error)


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

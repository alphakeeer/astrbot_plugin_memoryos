import subprocess
import sys
from pathlib import Path


def test_plugin_loads_with_old_scheduler_without_bootstrap_task():
    root = Path(__file__).resolve().parents[1]
    script = r"""
import importlib.util
import sys
import types
from pathlib import Path

root = Path.cwd().resolve()
for key in list(sys.modules):
    if key.startswith("memoryos_core") or key.startswith("memoryos_plugin_main"):
        sys.modules.pop(key, None)

old_scheduler = types.ModuleType("memoryos_core.scheduler")

class MemoryTaskQueue:
    def __init__(self, store, short_context, extractor, resolver, ai, config):
        self.store = store
    async def start(self):
        pass
    async def stop(self):
        pass
    async def enqueue_extract(self, task):
        pass

old_scheduler.MemoryTaskQueue = MemoryTaskQueue
sys.modules["memoryos_core.scheduler"] = old_scheduler

spec = importlib.util.spec_from_file_location(
    "memoryos_plugin_main_old_scheduler", root / "main.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
plugin = mod.MemoryOSPlugin(types.SimpleNamespace(), {"data_dir": str(root / ".tmp-import-compat")})
assert not hasattr(plugin.task_queue, "enqueue_bootstrap")
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
    )
    tmp = root / ".tmp-import-compat"
    if tmp.exists():
        for child in tmp.iterdir():
            child.unlink()
        tmp.rmdir()
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout

from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from memoryos_web.service import APIError, MemoryWebService, failure, success


class StandaloneWebServer:
    def __init__(self, plugin: Any, pages_dir: Path, logger: Any = None) -> None:
        self.plugin = plugin
        self.pages_dir = Path(pages_dir).resolve()
        self.logger = logger
        self.service = MemoryWebService(plugin)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_error = ""
        self._url = ""

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        config = self.plugin.config
        if not getattr(config, "standalone_web_enabled", True):
            self._last_error = ""
            return
        host = str(getattr(config, "standalone_web_host", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(config, "standalone_web_port", 8765) or 8765)
        token = str(getattr(config, "standalone_web_auth_token", "") or "")
        if not _is_loopback(host) and not token:
            self._last_error = "非本机监听必须配置 standalone_web_auth_token"
            self._log_warning(self._last_error)
            return
        self._loop = loop
        handler = self._make_handler()
        try:
            self._server = ThreadingHTTPServer((host, port), handler)
            self._server.daemon_threads = True
        except OSError as exc:
            self._last_error = "独立 Web 服务启动失败：%s" % exc
            self._log_warning(self._last_error)
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="memoryos-standalone-web",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        self._last_error = ""
        self._url = "http://%s:%s" % (host, port)
        self._log_info("MemoryOS 独立 Web 管理台已启动：%s", self._url)

    def stop(self) -> None:
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
        self._running = False

    def status(self) -> Dict[str, Any]:
        config = self.plugin.config
        return {
            "enabled": bool(getattr(config, "standalone_web_enabled", True)),
            "running": self._running,
            "url": self._url if self._running else "",
            "host": str(getattr(config, "standalone_web_host", "127.0.0.1") or ""),
            "port": int(getattr(config, "standalone_web_port", 8765) or 8765),
            "auth_required": bool(getattr(config, "standalone_web_auth_token", "")),
            "last_error": self._last_error,
        }

    def _make_handler(self) -> Any:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MemoryOSHTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                outer._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                outer._handle(self, "POST")

            def do_PUT(self) -> None:  # noqa: N802
                outer._handle(self, "PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                outer._handle(self, "DELETE")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                outer._security_headers(self)
                self.end_headers()

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        return Handler

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlparse(handler.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(handler, method, parsed)
            return
        if method != "GET":
            self._send_json(handler, 405, failure("方法不允许", 405, "method_not_allowed"))
            return
        self._serve_static(handler, parsed.path)

    def _handle_api(self, handler: BaseHTTPRequestHandler, method: str, parsed: Any) -> None:
        if not self._authorized(handler, parsed):
            self._send_json(handler, 401, failure("未授权", 401, "unauthorized"))
            return
        params = _query_params(parsed.query)
        body = self._read_json(handler)
        try:
            data = self._run_api(method, parsed.path[len("/api/") :].strip("/"), params, body)
            self._send_json(handler, 200, success(data))
        except APIError as exc:
            self._send_json(handler, exc.status, failure(exc.message, exc.status, exc.code))
        except Exception as exc:
            self._send_json(handler, 500, failure(str(exc), 500, "internal_error"))

    def _run_api(
        self, method: str, route: str, params: Dict[str, Any], body: Dict[str, Any]
    ) -> Dict[str, Any]:
        service = self.service
        parts = [part for part in route.split("/") if part]
        if route == "stats" and method == "GET":
            return self._await(service.stats())
        if route == "runtime-meta" and method == "GET":
            return self._await(service.runtime_meta())
        if route == "diagnostics" and method == "GET":
            return self._await(service.diagnostics())
        if route == "jobs" and method == "GET":
            return self._await(service.jobs(params))
        if route == "contexts" and method == "GET":
            return self._await(service.contexts(params))
        if route == "contexts" and method == "POST":
            return self._await(service.create_context(body))
        if route == "raw-messages" and method == "GET":
            return self._await(service.raw_messages(params))
        if route == "operation-logs" and method == "GET":
            return self._await(service.operation_logs(params))
        if route == "operation-logs" and method == "POST":
            return self._await(service.record_client_log(body))
        if route == "export" and method == "GET":
            return self._await(service.export_memories(params))
        if route == "import" and method == "POST":
            return self._await(service.import_memories(body))
        if route == "rebuild-index" and method == "POST":
            return self._await(service.rebuild_index())
        if route == "memories" and method == "GET":
            return self._await(service.list_memories(params))
        if route == "memories" and method == "POST":
            return self._await(service.create_memory(body))
        if len(parts) >= 2 and parts[0] == "memories":
            memory_id = unquote(parts[1])
            if len(parts) == 2 and method in {"POST", "PUT"}:
                return self._await(service.update_memory(memory_id, body))
            if len(parts) == 3 and parts[2] == "delete" and method in {"POST", "DELETE"}:
                return self._await(service.delete_memory(memory_id))
            if len(parts) == 3 and parts[2] == "expire" and method == "POST":
                return self._await(service.expire_memory(memory_id))
            if len(parts) == 3 and parts[2] == "logs" and method == "GET":
                return self._await(service.memory_logs(memory_id, params))
        if route == "bootstrap/start" and method == "POST":
            return self._await(service.bootstrap_start(body))
        if route == "bootstrap/dry-run" and method == "POST":
            return self._await(service.bootstrap_dry_run(body))
        if route == "bootstrap/probe" and method == "POST":
            return self._await(service.bootstrap_probe(body))
        if route == "bootstrap/cancel" and method == "POST":
            return self._await(service.bootstrap_cancel(body))
        if route == "openapi" and method == "GET":
            if not getattr(self.plugin.config, "standalone_web_openapi_enabled", False):
                raise APIError("OpenAPI 调试接口未启用", 404, "not_found")
            return {"routes": _openapi_routes()}
        raise APIError("没有找到接口：%s" % route, 404, "not_found")

    def _await(self, coro: Any) -> Dict[str, Any]:
        if self._loop is None:
            raise RuntimeError("独立 Web 服务没有绑定事件循环")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        result = future.result(timeout=60)
        return result if isinstance(result, dict) else {"data": result}

    def _serve_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        target = (self.pages_dir / rel).resolve()
        if not _is_within(target, self.pages_dir) or not target.is_file():
            self._send_bytes(handler, 404, b"Not found", "text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send_bytes(handler, 200, target.read_bytes(), content_type)

    def _read_json(self, handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
        length = int(handler.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise APIError("请求 JSON 格式无效", 400, "invalid_json")
        return payload if isinstance(payload, dict) else {}

    def _authorized(self, handler: BaseHTTPRequestHandler, parsed: Any) -> bool:
        token = str(getattr(self.plugin.config, "standalone_web_auth_token", "") or "")
        if not token:
            return True
        auth = handler.headers.get("Authorization", "")
        if auth == "Bearer %s" % token:
            return True
        query = parse_qs(parsed.query)
        return (query.get("token") or [""])[0] == token

    def _send_json(
        self, handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(handler, status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self, handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str
    ) -> None:
        handler.send_response(status)
        self._security_headers(handler)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _security_headers(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def _log_info(self, msg: str, *args: Any) -> None:
        if self.logger is not None and hasattr(self.logger, "info"):
            self.logger.info(msg, *args)

    def _log_warning(self, msg: str, *args: Any) -> None:
        if self.logger is not None and hasattr(self.logger, "warning"):
            self.logger.warning(msg, *args)


def _query_params(query: str) -> Dict[str, Any]:
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _openapi_routes() -> Dict[str, Tuple[str, ...]]:
    return {
        "GET /api/stats": (),
        "GET /api/runtime-meta": (),
        "GET /api/diagnostics": (),
        "GET /api/memories": ("q", "status", "type", "limit", "offset"),
        "POST /api/memories": (),
        "POST /api/memories/{memory_id}": (),
        "POST /api/memories/{memory_id}/delete": (),
        "POST /api/memories/{memory_id}/expire": (),
        "GET /api/memories/{memory_id}/logs": ("limit",),
        "GET /api/jobs": ("type", "limit"),
        "GET /api/contexts": ("limit",),
        "POST /api/contexts": (),
        "GET /api/raw-messages": ("session_id", "user_id", "group_id", "platform_id", "limit", "offset"),
        "GET /api/operation-logs": ("level", "action", "limit"),
        "POST /api/operation-logs": (),
        "GET /api/export": ("include_raw",),
        "POST /api/import": (),
        "POST /api/rebuild-index": (),
        "POST /api/bootstrap/probe": (),
        "POST /api/bootstrap/start": (),
        "POST /api/bootstrap/dry-run": (),
        "POST /api/bootstrap/cancel": (),
    }

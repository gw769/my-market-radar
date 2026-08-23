from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from pathlib import Path


EXTENSION_ID = "mfaihjehhndpmiakeolkpmkjnpllheho"
EXTENSION_VERSION = "1.0.7"
PROJECT_ROOT = Path(__file__).parents[4]
EXTENSION_CRX = PROJECT_ROOT / "chrome-extension.crx"


def extension_update_manifest(base_url: str) -> bytes:
    base_url = base_url.rstrip("/")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">'
        f'<app appid="{EXTENSION_ID}"><updatecheck '
        f'codebase="{base_url}/chrome-extension.crx" version="{EXTENSION_VERSION}" />'
        '</app></gupdate>'
    ).encode("utf-8")


def is_trusted_extension_request(headers: Any) -> bool:
    # Extension service-worker fetches are "none". A normal website trying to
    # reach localhost is cross-site, so reject it before it can dequeue work.
    return str(headers.get("Sec-Fetch-Site", "")).lower() == "none"


class ExtensionBridgeError(RuntimeError):
    pass


@dataclass
class _PendingCommand:
    payload: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str | None = None


class ExtensionBridge:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: deque[str] = deque()
        self._pending: dict[str, _PendingCommand] = {}
        self._last_seen = 0.0

    def connected(self, max_age: float = 75.0) -> bool:
        with self._condition:
            return self._last_seen > 0 and time.monotonic() - self._last_seen <= max_age

    def heartbeat(self) -> None:
        with self._condition:
            self._last_seen = time.monotonic()
            self._condition.notify_all()

    def request(self, action: str, timeout: float = 50.0, **params: Any) -> Any:
        command_id = uuid.uuid4().hex
        pending = _PendingCommand(
            payload={"id": command_id, "action": action, "params": params}
        )
        with self._condition:
            self._pending[command_id] = pending
            self._queue.append(command_id)
            self._condition.notify_all()
        if not pending.event.wait(timeout=max(1.0, timeout)):
            with self._condition:
                self._pending.pop(command_id, None)
                try:
                    self._queue.remove(command_id)
                except ValueError:
                    pass
            raise ExtensionBridgeError(f"主 Chrome 扩展响应超时：{action}")
        if pending.error:
            raise ExtensionBridgeError(pending.error)
        return pending.result

    def next_command(self, wait_seconds: float = 20.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.0, min(wait_seconds, 25.0))
        with self._condition:
            self._last_seen = time.monotonic()
            while not self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
                self._last_seen = time.monotonic()
            while self._queue:
                command_id = self._queue.popleft()
                pending = self._pending.get(command_id)
                if pending:
                    return pending.payload
            return None

    def complete(self, command_id: str, result: Any = None, error: str | None = None) -> bool:
        with self._condition:
            self._last_seen = time.monotonic()
            pending = self._pending.pop(command_id, None)
        if not pending:
            return False
        pending.result = result
        pending.error = error
        pending.event.set()
        return True


bridge = ExtensionBridge()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "MYMarketRadarChromeBridge/1.0"

    def _trusted_extension_request(self) -> bool:
        return is_trusted_extension_request(self.headers)

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "connected": bridge.connected()})
            return
        if parsed.path == "/updates.xml":
            xml = extension_update_manifest("http://127.0.0.1:9232")
            self._bytes(200, xml, "application/xml; charset=utf-8")
            return
        if parsed.path == "/chrome-extension.crx":
            if not EXTENSION_CRX.is_file():
                self._json(404, {"error": "extension package missing"})
                return
            self._bytes(200, EXTENSION_CRX.read_bytes(), "application/x-chrome-extension")
            return
        if parsed.path == "/command":
            if not self._trusted_extension_request():
                self._json(403, {"error": "extension request required"})
                return
            query = parse_qs(parsed.query)
            try:
                wait_seconds = float((query.get("wait") or ["20"])[0])
            except ValueError:
                wait_seconds = 20.0
            command = bridge.next_command(wait_seconds)
            self._json(200, {"command": command})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/result":
            self._json(404, {"error": "not found"})
            return
        if not self._trusted_extension_request():
            self._json(403, {"error": "extension request required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8 * 1024 * 1024:
                raise ValueError("invalid content length")
            payload = json.loads(self.rfile.read(length))
            command_id = str(payload.get("id") or "")
            accepted = bridge.complete(
                command_id,
                result=payload.get("result"),
                error=str(payload.get("error")) if payload.get("error") else None,
            )
            self._json(200, {"ok": accepted})
        except Exception as exc:
            self._json(400, {"error": str(exc)[:300]})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def start_extension_bridge(host: str = "127.0.0.1", port: int = 9232) -> None:
    global _server, _server_thread
    if _server is not None:
        return
    _server = ThreadingHTTPServer((host, port), _BridgeHandler)
    _server.daemon_threads = True
    _server_thread = threading.Thread(
        target=_server.serve_forever,
        name="chrome-extension-bridge",
        daemon=True,
    )
    _server_thread.start()


def stop_extension_bridge() -> None:
    global _server, _server_thread
    server, thread = _server, _server_thread
    _server = None
    _server_thread = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join(timeout=2)


def extension_ready() -> bool:
    return bridge.connected()


def extension_request(action: str, timeout: float = 50.0, **params: Any) -> Any:
    # A Manifest V3 service worker may be asleep and therefore have no recent heartbeat.
    # Queue first and let the extension's alarm wake within the caller's timeout instead of
    # rejecting a healthy, signed-in Chrome as disconnected.
    return bridge.request(action, timeout=timeout, **params)

"""Serve the vendored OpenCowork GUI with a Talos chat bridge.

The renderer is the actual OpenCowork React interface (MIT, Copyright (c)
2026 OpenCoworkAI), built into ``talos_gui/dist``.  This module only serves
those files on loopback and proxies chat turns to the Talos room, so the room
token never reaches the browser.  Text only: no audio is synthesized or played.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_PORT = 8766
ROOM = "http://127.0.0.1:8765"
MAX_BODY = 64 * 1024
MAX_TEXT = 8_000


class GuiBridgeError(RuntimeError):
    pass


def dist(state=None) -> Path:
    return Path(__file__).resolve().parents[2] / "talos_gui" / "dist"


def available() -> bool:
    return (dist() / "index.html").is_file()


def _token(state) -> str:
    path = Path(state) / "room.token"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise GuiBridgeError(f"No room token at {path}.") from error
    if not text:
        raise GuiBridgeError("The room token is empty.")
    return text


def _room_turn(text: str, state, *, timeout: float = 240) -> dict:
    body = json.dumps({"text": text, "source": "text"}).encode()
    request = urllib.request.Request(
        ROOM + "/v1/turn", data=body,
        headers={"Authorization": "Bearer " + _token(state),
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise GuiBridgeError(f"Talos room returned HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GuiBridgeError(f"Talos room is unavailable: {error}") from error
    except ValueError as error:
        raise GuiBridgeError("Talos room returned invalid JSON.") from error


class Handler(BaseHTTPRequestHandler):
    server_version = "TalosGUI/0.1"
    root = Path()
    state = Path("state")

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json")
                         else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self._json(200, {"ok": True, "ui": available()})
            return
        if path == "/api/config":
            self._json(200, {"model": os.environ.get("TALOS_GUI_MODEL", "qwen2.5:1.5b")})
            return
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (self.root / relative).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            self._json(404, {"error": "Not found"})
            return
        if not target.is_file():
            target = self.root / "index.html"
        if not target.is_file():
            self._json(503, {"error": "The GUI bundle is missing. Run the build step."})
            return
        try:
            body = target.read_bytes()
        except OSError as error:
            self._json(500, {"error": str(error)})
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, body, kind)

    def do_POST(self):
        if urlparse(self.path).path != "/api/turn":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "Invalid request size."})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, OSError):
            self._json(400, {"error": "Invalid JSON."})
            return
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            self._json(400, {"error": "Text is required."})
            return
        if len(text) > MAX_TEXT:
            self._json(400, {"error": "Text is too long."})
            return
        try:
            result = _room_turn(text.strip(), self.state)
        except GuiBridgeError as error:
            self._json(502, {"error": str(error)})
            return
        self._json(200, {
            "reply": result.get("reply", ""),
            "action": result.get("action"),
            "tool": result.get("tool"),
        })


def build_server(state, *, port: int = 0, root=None,
                 server_class=ThreadingHTTPServer):
    root = Path(root) if root is not None else dist(state)
    handler = type("TalosGuiHandler", (Handler,), {"root": root, "state": Path(state)})
    try:
        return server_class(("127.0.0.1", int(port)), handler)
    except OSError as error:
        raise GuiBridgeError(f"Could not bind the GUI on 127.0.0.1:{port}: {error}") from error


def serve(state, *, port: int = DEFAULT_PORT, open_browser: bool = True,
          server_class=ThreadingHTTPServer):
    root = dist(state)
    if not (root / "index.html").is_file():
        raise GuiBridgeError(
            "The GUI bundle is missing. Run: cd talos_gui && npm install && npm run build"
        )
    server = build_server(state, port=port, root=root, server_class=server_class)
    url = f"http://127.0.0.1:{server.server_port}/"
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url

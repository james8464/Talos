"""Minimal Agent Client Protocol (JSON-RPC over stdio) client."""

from __future__ import annotations

import json
import subprocess


class ACPError(RuntimeError):
    pass


class ACPSession:
    def __init__(self, command: list[str], *, popen=subprocess.Popen):
        if not command or not all(isinstance(part, str) for part in command):
            raise ValueError("ACP command must be an argv list.")
        self.process = popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        if self.process.stdin is None or self.process.stdout is None:
            raise ACPError("ACP process pipes are unavailable.")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise ACPError("ACP agent closed the connection.")
        reply = json.loads(line)
        if "error" in reply:
            raise ACPError(str(reply["error"].get("message", "ACP error")))
        return reply.get("result", {})

    def initialize(self, *, protocol: str = "2024-11-05") -> dict:
        return self.call("initialize", {"protocolVersion": protocol,
                                        "clientCapabilities": {},
                                        "clientInfo": {"name": "talos", "version": "0.2.0"}})

    def prompt(self, text: str, *, cwd: str = ".") -> dict:
        session = self.call("session/new", {"cwd": cwd, "mcpServers": []})
        ident = session.get("sessionId") or session.get("session_id")
        if not ident:
            raise ACPError("ACP session id missing.")
        return self.call("session/prompt", {"sessionId": ident,
                                            "prompt": [{"type": "text", "text": text[:4000]}]})

    def close(self) -> None:
        try:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            self.process.kill()

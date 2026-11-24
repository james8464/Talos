"""Minimal local MCP stdio server for Talos.

Exposes read-only capabilities, health and task decomposition to any MCP
client (Codex, Claude, Cursor, opencode) without third-party dependencies.
JSON-RPC 2.0 over newline-delimited stdin/stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

VERSION = "0.2.0"


def _tools() -> list[dict]:
    return [
        {
            "name": "talos_capabilities",
            "description": "Inventory Talos tools, test coverage and upstream gaps.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "talos_health",
            "description": "Return the last Talos self-check report.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "talos_decompose",
            "description": "Decompose a task into model-sized leaves.",
            "inputSchema": {
                "type": "object",
                "required": ["task"],
                "properties": {"task": {"type": "string"}},
            },
        },
        {
            "name": "talos_graph_query",
            "description": "Search the local Graphify source graph.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        },
    ]


def call_tool(name: str, arguments: dict, root: Path, state: Path) -> dict:
    if name == "talos_capabilities":
        from talos_core.self_improvement import capability_audit
        return capability_audit.report(root, state)
    if name == "talos_health":
        path = state / "health.json"
        return json.loads(path.read_text()) if path.is_file() else {"ok": False, "error": "no report"}
    if name == "talos_decompose":
        from talos_core.self_improvement import decomposition
        task = str(arguments.get("task", "")).strip()
        if not task:
            raise ValueError("task is required")
        return decomposition.plan_task(state, task)
    if name == "talos_graph_query":
        from talos_core.knowledge_graph import graph
        return {"matches": graph.search(str(arguments.get("query", "")))}
    raise ValueError(f"unknown tool {name}")


def handle(message: dict, root: Path, state: Path) -> dict | None:
    method = message.get("method")
    ident = message.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": ident, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "talos", "version": VERSION},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": _tools()}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            value = call_tool(params.get("name", ""), params.get("arguments") or {}, root, state)
        except (OSError, ValueError, RuntimeError) as error:
            return {"jsonrpc": "2.0", "id": ident,
                    "error": {"code": -32000, "message": str(error)}}
        return {"jsonrpc": "2.0", "id": ident,
                "result": {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                           "isError": False}}
    return {"jsonrpc": "2.0", "id": ident,
            "error": {"code": -32601, "message": "method not found"}}


def serve(stdin=None, stdout=None, root: Path | None = None, state: Path | None = None) -> int:
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    root = Path(root or Path.cwd())
    state = Path(state or root / "state")
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            sink.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "parse error"}}) + "\n")
            sink.flush()
            continue
        response = handle(message, root, state)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    return 0

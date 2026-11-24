"""Generic MCP stdio client built on the official Python SDK.

Configuration lives in ``state/mcp-servers.json``. Talos does not implement the
MCP wire protocol itself; this module only launches existing MCP servers and
calls their tools.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
import time
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # optional extra
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None

FILE = "mcp-servers.json"
MAX_TIMEOUT = 180
DISCOVERY_TTL = 60.0
MAX_PLANNER_TOOLS = 24

# Read/write hints are only used when a server omits MCP tool annotations.
# Unknown names fail closed as writes so the planner never writes silently.
_READ_HINTS = frozenset((
    "analyze", "audit", "browse", "calculate", "capabilities", "check", "compute",
    "count", "describe", "diff", "echo", "estimate", "explain", "fetch", "find",
    "graph", "health", "help", "info", "inspect", "list", "lookup", "memory",
    "ping", "predict", "preview", "query", "read", "recall", "report", "resolve",
    "retrieve", "review", "search", "see", "show", "stats", "status", "summarize",
    "summary", "time", "view", "whoami",
))
_WRITE_HINTS = frozenset((
    "add", "apply", "archive", "cancel", "change", "clear", "close", "commit",
    "create", "delete", "deploy", "destroy", "disable", "download", "drop", "edit",
    "enable", "erase", "execute", "export", "forget", "import", "install", "kill",
    "launch", "merge", "mkdir", "move", "open", "patch", "post", "publish", "push",
    "put", "reboot", "remove", "rename", "replace", "reset", "restart", "resume",
    "run", "save", "send", "set", "start", "stop", "submit", "sync", "terminate",
    "touch", "trigger", "unarchive", "uninstall", "update", "upload", "write",
))

_DISCOVERY_CACHE: dict[tuple[str, tuple], tuple[float, list[dict]]] = {}


class MCPClientError(RuntimeError):
    pass


def available() -> bool:
    return ClientSession is not None and StdioServerParameters is not None and stdio_client is not None


def _path(state: Path | str) -> Path:
    return Path(state) / FILE


def servers(state: Path | str) -> dict:
    try:
        data = json.loads(_path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def configure(state: Path | str, name: str, command: str | None = None, args=None, *,
              url=None, headers=None, transport="stdio", allowed_tools=None,
              env=None, cwd=None) -> dict:
    name = " ".join(str(name).split())
    if not name or len(name) > 60:
        raise ValueError("MCP server name must be 1–60 characters.")
    if bool(command) == bool(url):
        raise ValueError("Provide exactly one MCP command or URL.")
    if url is not None:
        if not isinstance(url, str) or not re.match(r"^https?://", url.strip(), re.I):
            raise ValueError("MCP URL must start with http:// or https://.")
        if transport not in ("http", "sse"):
            raise ValueError("URL MCP transport must be http or sse.")
    else:
        if not isinstance(command, str) or not command:
            raise ValueError("MCP command must be a non-empty executable path.")
        if transport != "stdio":
            raise ValueError("Command MCP transport must be stdio.")
    if args is not None and (not isinstance(args, list) or not all(isinstance(v, str) for v in args)):
        raise ValueError("MCP args must be a list of strings.")
    if allowed_tools is not None and (
        not isinstance(allowed_tools, list) or not all(isinstance(v, str) for v in allowed_tools)
    ):
        raise ValueError("allowed_tools must be a list of strings.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = servers(state)
    entry = {"transport": transport}
    if url is not None:
        entry["url"] = url.strip()
        if headers:
            entry["headers"] = {str(k): str(v) for k, v in headers.items()}
    else:
        entry.update({"command": command, "args": list(args or [])})
        if env is not None:
            entry["env"] = {str(k): str(v) for k, v in env.items()}
        if cwd is not None:
            entry["cwd"] = str(cwd)
    if allowed_tools is not None:
        entry["allowed_tools"] = sorted(set(allowed_tools))
    data[name] = entry
    fd, temporary = tempfile.mkstemp(prefix=".mcp-servers-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, _path(state))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"name": name, "configured": True}

def _server(state: Path | str, name: str) -> dict:
    if not available():
        raise MCPClientError("Install the optional 'mcp' extra to use MCP servers.")
    server = servers(state).get(name)
    if not isinstance(server, dict):
        raise MCPClientError(f"No MCP server named {name!r}.")
    if not server.get("url") and not server.get("command"):
        raise MCPClientError(f"MCP server {name!r} has no command or URL.")
    return server


async def _session_action(read, write, action):
    async with ClientSession(read, write) as session:
        await session.initialize()
        return await action(session)


async def _with_session(server: dict, action):
    if server.get("url"):
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        headers = server.get("headers") or None
        if server.get("transport") == "sse":
            async with sse_client(server["url"], headers=headers, timeout=10) as (read, write):
                return await _session_action(read, write, action)
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(server["url"], http_client=http_client) as (read, write):
                return await _session_action(read, write, action)
    parameters = StdioServerParameters(
        command=server["command"],
        args=[str(v) for v in server.get("args", [])],
        env={str(k): str(v) for k, v in (server.get("env") or {}).items()} or None,
        cwd=server.get("cwd"),
    )
    async with stdio_client(parameters) as (read, write):
        return await _session_action(read, write, action)

def _run(state, name, action, timeout):
    server = _server(state, name)
    if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError("MCP timeout must be between 1 and 180 seconds.")

    async def runner():
        return await asyncio.wait_for(_with_session(server, action), timeout)

    try:
        return asyncio.run(runner())
    except TimeoutError as error:
        raise MCPClientError(f"MCP server {name!r} timed out.") from error
    except BaseExceptionGroup as error:
        details = " ".join(_exception_text(error))[:400]
        raise MCPClientError(f"MCP server {name!r} connection failed: {details}") from error
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, MCPClientError):
            raise
        raise MCPClientError(str(error)[:300]) from error


def _exception_text(error) -> list[str]:
    parts = []
    if isinstance(error, BaseExceptionGroup):
        for item in error.exceptions:
            parts.extend(_exception_text(item))
    else:
        text = " ".join(str(error).split())
        if text:
            parts.append(text)
    return parts


def tools(state, name, timeout=30):
    def collect(session):
        async def inner():
            result = await session.list_tools()
            tools = []
            for tool in result.tools:
                data = tool.model_dump() if hasattr(tool, "model_dump") else vars(tool)
                tools.append({
                    "name": data.get("name", ""),
                    "description": data.get("description", "") or "",
                    "inputSchema": data.get("input_schema") or data.get("inputSchema") or {},
                    "annotations": _annotations(data.get("annotations")),
                })
            return tools
        return inner()

    return _run(state, name, collect, timeout)


def call(state, name, tool, arguments=None, timeout=90):
    server = _server(state, name)
    allowed = server.get("allowed_tools")
    if isinstance(allowed, list) and tool not in allowed:
        raise MCPClientError(f"Tool {tool!r} is not allowed on MCP server {name!r}.")
    arguments = {} if arguments is None else arguments
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object.")

    def invoke(session):
        async def inner():
            result = await session.call_tool(tool, arguments)
            structured = getattr(result, "structuredContent", None)
            if structured is None:
                structured = getattr(result, "structured_content", None)
            if structured is not None:
                return structured
            texts = [getattr(item, "text", None) for item in (getattr(result, "content", None) or [])]
            texts = [text for text in texts if isinstance(text, str)]
            if len(texts) == 1:
                try:
                    return json.loads(texts[0])
                except ValueError:
                    return texts[0]
            return texts
        return inner()

    return _run(state, name, invoke, timeout)


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(word.title() for word in rest)


def _annotations(raw) -> dict:
    """Normalize SDK annotations to snake_case booleans."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"):
        value = raw.get(key, raw.get(_camel(key)))
        if isinstance(value, bool):
            result[key] = value
    return result


def is_read_only(tool_name: str, annotations: dict | None = None) -> bool:
    """Classify a tool without ever assuming an unknown tool is safe to write."""
    annotations = annotations or {}
    hint = annotations.get("read_only_hint")
    if hint is True:
        return True
    if hint is False or annotations.get("destructive_hint") is True:
        return False
    words = re.findall(r"[a-z0-9]+", str(tool_name).casefold())
    if any(word in _WRITE_HINTS for word in words):
        return False
    return any(word in _READ_HINTS for word in words)


def _split_qualified(name: str) -> tuple[str, str]:
    if not isinstance(name, str) or "." not in name:
        raise MCPClientError("MCP tool names must be namespaced as server.tool.")
    server, tool = name.rsplit(".", 1)
    if not server or not tool:
        raise MCPClientError("MCP tool names must be namespaced as server.tool.")
    return server, tool


def _allowed(server: dict, tool: str) -> bool:
    allowed = server.get("allowed_tools")
    return not isinstance(allowed, list) or tool in allowed


def _explicitly_allowed(server: dict, tool: str) -> bool:
    allowed = server.get("allowed_tools")
    return isinstance(allowed, list) and tool in allowed


def _fingerprint(state: Path | str) -> tuple[str, int, int] | None:
    path = _path(state)
    try:
        stat = path.stat()
    except OSError:
        return None
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


def clear_cache() -> None:
    """Drop cached discovery results. Tests and configuration changes may need this."""
    _DISCOVERY_CACHE.clear()


def _discover_server(state, name: str, server: dict, timeout: float) -> tuple[list[dict], str]:
    try:
        found = tools(state, name, timeout=timeout)
    except (AttributeError, KeyError, MCPClientError, OSError, RuntimeError, TypeError, ValueError) as error:
        return [], f"{name}: {' '.join(str(error).split())[:200]}"
    items = []
    for raw in found or ():
        tool = str(raw.get("name") or "").strip()
        if not tool or not _allowed(server, tool):
            continue
        annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
        read_only = is_read_only(tool, annotations)
        item = {
            "name": f"{name}.{tool}",
            "server": name,
            "tool": tool,
            "description": str(raw.get("description") or ""),
            "input_schema": raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {},
            "read_only": read_only,
            "write_allowed": (not read_only) and _explicitly_allowed(server, tool),
            "annotations": annotations,
        }
        items.append(item)
    return items, ""


def discover(state: Path | str, *, timeout: int = 5, max_age: float = DISCOVERY_TTL) -> list[dict]:
    """List allowed tools from every configured server, briefly cached and fail-soft.

    A configuration with no servers returns immediately without spawning anything.
    """
    configured = servers(state)
    if not configured or not available():
        return []
    fingerprint = _fingerprint(state)
    key = (str(Path(state).resolve()), fingerprint or ("missing", 0, 0))
    cached = _DISCOVERY_CACHE.get(key)
    if cached and 0 <= time.monotonic() - cached[0] < max_age:
        return copy.deepcopy(cached[1])
    items: list[dict] = []
    for name in sorted(configured):
        server = configured.get(name)
        if not isinstance(server, dict):
            continue
        found, _error = _discover_server(state, name, server, timeout)
        items.extend(found)
    items = items[:MAX_PLANNER_TOOLS]
    for stale in [entry for entry in _DISCOVERY_CACHE if entry[0] == key[0] and entry != key]:
        del _DISCOVERY_CACHE[stale]
    _DISCOVERY_CACHE[key] = (time.monotonic(), items)
    return copy.deepcopy(items)


def planner_tools(state: Path | str, *, timeout: int = 5, max_age: float = DISCOVERY_TTL) -> list[dict]:
    """Discovery for the planner: never raise, return an empty list when unavailable."""
    try:
        return discover(state, timeout=timeout, max_age=max_age)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return []


def _cached_metadata(state: Path | str, server_name: str, tool: str) -> dict | None:
    configured = servers(state)
    if not configured:
        return None
    fingerprint = _fingerprint(state)
    key = (str(Path(state).resolve()), fingerprint or ("missing", 0, 0))
    cached = _DISCOVERY_CACHE.get(key)
    if not cached:
        return None
    for item in cached[1]:
        if item["server"] == server_name and item["tool"] == tool:
            return item
    return None


def read_only_tool(state: Path | str, name: str) -> bool:
    """Whether a namespaced tool is read-only, using cached annotations if present."""
    server_name, tool = _split_qualified(name)
    meta = _cached_metadata(state, server_name, tool)
    if meta is not None:
        return bool(meta["read_only"])
    return is_read_only(tool)


def call_tool(
    state: Path | str, name: str, arguments: dict | None = None, *,
    confirm: bool = False, trusted_owner: bool = False, timeout: int = 90,
):
    """Call a namespaced planner tool with allowlist and confirmation checks."""
    server_name, tool = _split_qualified(name)
    configured = servers(state)
    server = configured.get(server_name)
    if not isinstance(server, dict):
        raise MCPClientError(f"MCP server {server_name!r} is not configured.")
    if not _allowed(server, tool):
        raise MCPClientError(f"Tool {tool!r} is not allowed on MCP server {server_name!r}.")
    if not read_only_tool(state, name):
        if not _explicitly_allowed(server, tool):
            raise MCPClientError(
                f"MCP tool {name!r} changes state; add {tool!r} to the server's allowed_tools first."
            )
        if not (trusted_owner and confirm):
            raise MCPClientError(
                f"MCP tool {name!r} changes state; it requires an owner-confirmed request."
            )
    return call(state, server_name, tool, arguments, timeout)


def call_read_only(state: Path | str, name: str, arguments: dict | None = None, *, timeout: int = 90):
    """Call a namespaced tool only when it is classified read-only."""
    if not read_only_tool(state, name):
        raise MCPClientError(f"MCP tool {name!r} is not read-only; it cannot run as a lookup.")
    return call_tool(state, name, arguments, timeout=timeout)


def command(text, state):
    """Handle explicit MCP server/tool requests from the room."""
    words = text.strip()
    if re.fullmatch(r"(?:list|show) (?:my )?mcp servers[?.! ]*", words, re.I):
        names = sorted(servers(state))
        return "MCP servers: " + ", ".join(names) if names else "No MCP servers are configured."
    listing = re.fullmatch(r"(?:list|show) mcp tools (?:for|on) (.+?)[?.!]*", words, re.I)
    if listing:
        name = listing.group(1).strip()
        try:
            found = tools(state, name)
        except MCPClientError as error:
            return f"MCP error: {error}"
        names = [item["name"] for item in found]
        return f"MCP tools on {name}: " + ", ".join(names) if names else f"No tools on {name}."
    requested = re.fullmatch(
        r"call mcp tool (.+?) on (.+?)(?: with (.+))?[.!]?", words, re.I,
    )
    if requested:
        tool, name, raw = requested.group(1).strip(), requested.group(2).strip(), requested.group(3)
        raw = (raw or "{}").strip().rstrip(" .!?")
        try:
            arguments = json.loads(raw)
        except ValueError:
            return "MCP arguments must be a JSON object."
        try:
            result = call(state, name, tool, arguments)
        except (MCPClientError, ValueError) as error:
            return f"MCP error: {error}"
        return f"MCP {name}/{tool}: {json.dumps(result, ensure_ascii=False)[:500]}"
    return None

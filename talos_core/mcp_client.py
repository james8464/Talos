"""Generic MCP stdio client built on the official Python SDK.

Configuration lives in ``state/mcp-servers.json``. Talos does not implement the
MCP wire protocol itself; this module only launches existing MCP servers and
calls their tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
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
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, MCPClientError):
            raise
        raise MCPClientError(str(error)[:300]) from error


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

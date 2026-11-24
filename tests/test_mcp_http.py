"""End-to-end Streamable-HTTP MCP test using the official SDK."""

import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from talos_core import mcp_client

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(mcp_client.available(), "install the optional 'mcp' extra")
class MCPHTTPTests(unittest.TestCase):
    def test_streamable_http_tools_and_call(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        port = free_port()
        script = folder / "fixture_server.py"
        script.write_text(
            "from mcp.server.mcpserver import MCPServer\n"
            "server = MCPServer('fixture')\n"
            "@server.tool()\n"
            "def ping() -> str:\n"
            "    return 'pong'\n"
            f"server.run(transport='streamable-http', host='127.0.0.1', port={port}, stateless_http=True)\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [sys.executable, str(script)], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                self.fail("HTTP MCP fixture did not start.")
            state = folder / "state"
            mcp_client.configure(
                state, "fixture", url=f"http://127.0.0.1:{port}/mcp", transport="http",
            )
            tools = mcp_client.tools(state, "fixture", timeout=20)
            self.assertIn("ping", [tool["name"] for tool in tools])
            result = mcp_client.call(state, "fixture", "ping", {}, timeout=20)
            self.assertIn("pong", str(result))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def test_sse_tools_and_call(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        port = free_port()
        script = folder / "sse_fixture_server.py"
        script.write_text(
            "from mcp.server.mcpserver import MCPServer\n"
            "server = MCPServer('fixture')\n"
            "@server.tool()\n"
            "def ping() -> str:\n"
            "    return 'pong'\n"
            f"server.run(transport='sse', host='127.0.0.1', port={port})\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [sys.executable, str(script)], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                self.fail("SSE MCP fixture did not start.")
            state = folder / "state"
            mcp_client.configure(
                state, "fixture", url=f"http://127.0.0.1:{port}/sse", transport="sse",
            )
            tools = mcp_client.tools(state, "fixture", timeout=20)
            self.assertIn("ping", [tool["name"] for tool in tools])
            result = mcp_client.call(state, "fixture", "ping", {}, timeout=20)
            self.assertIn("pong", str(result))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    unittest.main()

"""Integration test for the official-SDK MCP client using Talos's own server."""

import sys
import tempfile
import unittest
from pathlib import Path

from talos_core import mcp_client

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(mcp_client.available(), "install the optional 'mcp' extra")
class MCPClientTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        mcp_client.configure(
            self.state,
            "talos",
            sys.executable,
            ["-m", "talos_core.cli", "mcp", "--root", str(ROOT), "--state", str(self.state)],
            cwd=str(ROOT),
        )

    def test_tools_and_tool_call(self):
        tools = mcp_client.tools(self.state, "talos")
        names = {tool["name"] for tool in tools}
        self.assertIn("talos_graph_query", names)
        result = mcp_client.call(
            self.state, "talos", "talos_graph_query", {"query": "Room"},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("matches", result)

    def test_missing_server_is_an_error(self):
        with self.assertRaises(mcp_client.MCPClientError):
            mcp_client.tools(self.state, "missing")


if __name__ == "__main__":
    unittest.main()

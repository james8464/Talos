"""Integration test for the official-SDK MCP client using Talos's own server."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from talos_core import cli, mcp_client

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

    def test_cli_add_and_tools_for_existing_server(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main([
                "mcp-client", "add", "talos",
                "--command", sys.executable,
                "--server-args", json.dumps([
                    "-m", "talos_core.cli", "mcp", "--root", str(ROOT),
                    "--state", str(self.state),
                ]),
                "--cwd", str(ROOT), "--state", str(self.state),
            ])
        self.assertEqual(code, 0)
        self.assertIn("configured", output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["mcp-client", "tools", "talos", "--state", str(self.state)])
        self.assertEqual(code, 0)
        self.assertIn("talos_graph_query", output.getvalue())

    def test_missing_server_is_an_error(self):
        with self.assertRaises(mcp_client.MCPClientError):
            mcp_client.tools(self.state, "missing")


if __name__ == "__main__":
    unittest.main()

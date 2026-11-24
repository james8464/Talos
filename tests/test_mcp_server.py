import io
import json
import tempfile
import unittest
from pathlib import Path

from talos_core import mcp_server


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.state.parent

    def test_initialize_and_list_tools(self):
        source = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n")
        sink = io.StringIO()
        self.assertEqual(mcp_server.serve(source, sink, self.root, self.state), 0)
        replies = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "talos")
        names = {tool["name"] for tool in replies[1]["result"]["tools"]}
        self.assertIn("talos_capabilities", names)
        self.assertIn("talos_decompose", names)

    def test_decompose_tool_returns_leaves(self):
        request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "talos_decompose", "arguments": {"task": "Fix a typo"}}}
        response = mcp_server.handle(request, self.root, self.state)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(payload["leaves"]), 1)

    def test_unknown_method_and_tool_are_errors(self):
        self.assertEqual(mcp_server.handle({"id": 1, "method": "nope"}, self.root, self.state)["error"]["code"], -32601)
        response = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                      "params": {"name": "nope", "arguments": {}}}, self.root, self.state)
        self.assertIn("unknown tool", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()

"""Planner integration for configured MCP servers, using a local stdio fixture."""

import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agent import DECISION_SCHEMA, plan, process_event
from talos_core import mcp_client
from talos_core.memory.agent_memory import open_memory

ROOT = Path(__file__).resolve().parents[1]
EVENT = {"kind": "synthetic", "summary": "Echo a short greeting."}
LOOKUP = {
    "disposition": "look_up",
    "tool": "fixture.echo_text",
    "goal": "Read the fixture value",
    "reason": "The configured MCP server can answer this.",
    "detail": '{"text": "hello"}',
}
REPLY = {
    "disposition": "reply",
    "tool": "none",
    "goal": "Answer the request",
    "reason": "The MCP result is enough.",
    "detail": "hello",
}
WRITE = {
    "disposition": "act",
    "tool": "fixture.delete_note",
    "goal": "Remove the fixture note",
    "reason": "The owner asked for a write tool.",
    "detail": '{"note_id": "7"}',
}

FIXTURE = '''
import os
from mcp.server.mcpserver import MCPServer

server = MCPServer("fixture")
log = os.environ.get("MCP_FIXTURE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write("start\\n")


@server.tool()
def echo_text(text: str) -> str:
    """Return the supplied text."""
    return text


@server.tool()
def delete_note(note_id: str) -> dict:
    """Delete a note by id."""
    return {"deleted": note_id}


server.run(transport="stdio")
'''


@unittest.skipUnless(mcp_client.available(), "install the optional 'mcp' extra")
class PlannerMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(cls.enterClassContext(tempfile.TemporaryDirectory())).resolve()
        cls.script = cls.root / "fixture_server.py"
        cls.script.write_text(FIXTURE, encoding="utf-8")

    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.log = self.root / f"fixture-{uuid4().hex}.log"
        mcp_client.clear_cache()
        self.path = self.state / "talos.sqlite3"
        self.database = self.enterContext(closing(open_memory(self.path)))

    def configure(self, name="fixture", *, allowed_tools=None, command=None, args=None):
        mcp_client.configure(
            self.state, name,
            command or sys.executable,
            args if args is not None else [str(self.script)],
            cwd=str(ROOT), env={"MCP_FIXTURE_LOG": str(self.log)},
            allowed_tools=allowed_tools,
        )

    def test_discovers_namespaced_tools_and_exposes_schemas_to_planner(self):
        self.configure(allowed_tools=["echo_text", "delete_note"])
        found = {item["name"]: item for item in mcp_client.planner_tools(self.state)}

        self.assertEqual(set(found), {"fixture.echo_text", "fixture.delete_note"})
        self.assertTrue(found["fixture.echo_text"]["read_only"])
        self.assertFalse(found["fixture.delete_note"]["read_only"])
        self.assertTrue(found["fixture.delete_note"]["write_allowed"])
        self.assertEqual(found["fixture.echo_text"]["input_schema"]["properties"]["text"]["type"],
                         "string")

        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", side_effect=[json.dumps(LOOKUP), json.dumps(REPLY)]) as model,
        ):
            result = plan(self.database, EVENT, max_lookups=1, model="test-local:7b")

        self.assertEqual(result["tools_used"], ["fixture.echo_text"])
        first_schema = model.call_args_list[0].kwargs["schema"]["properties"]["tool"]["enum"]
        final_schema = model.call_args_list[1].kwargs["schema"]["properties"]["tool"]["enum"]
        self.assertIn("fixture.echo_text", first_schema)
        self.assertIn("fixture.delete_note", first_schema)
        self.assertNotIn("fixture.echo_text", final_schema)
        self.assertIn("fixture.delete_note", final_schema)

        context = json.loads(model.call_args_list[1].args[0][-1]["content"])
        exposed = {item["name"]: item for item in context["mcp_tools"]}
        self.assertTrue(exposed["fixture.echo_text"]["read_only"])
        self.assertEqual(exposed["fixture.delete_note"]["arguments"]["properties"]["note_id"]["type"],
                         "string")
        self.assertEqual(
            context["lookup_results"][0]["result"], {"result": "hello"},
        )

    def test_allowlist_and_missing_server_fail_with_clear_errors(self):
        self.configure(allowed_tools=["echo_text"])
        names = {item["name"] for item in mcp_client.planner_tools(self.state)}
        self.assertEqual(names, {"fixture.echo_text"})

        with self.assertRaisesRegex(mcp_client.MCPClientError, "not allowed"):
            mcp_client.call_tool(self.state, "fixture.delete_note", {}, confirm=True,
                                 trusted_owner=True)
        with self.assertRaisesRegex(mcp_client.MCPClientError, "not configured"):
            mcp_client.call_tool(self.state, "missing.echo_text", {})
        with self.assertRaisesRegex(mcp_client.MCPClientError, "server.tool"):
            mcp_client.call_tool(self.state, "notnamespaced", {})
        with self.assertRaisesRegex(mcp_client.MCPClientError, "not read-only"):
            mcp_client.call_read_only(self.state, "fixture.delete_note", {})

    def test_writes_need_an_explicit_allowlist_and_owner_confirmation(self):
        self.configure(allowed_tools=["echo_text", "delete_note"])
        with self.assertRaisesRegex(mcp_client.MCPClientError, "owner-confirmed"):
            mcp_client.call_tool(self.state, "fixture.delete_note", {"note_id": "1"})
        with self.assertRaisesRegex(mcp_client.MCPClientError, "owner-confirmed"):
            mcp_client.call_tool(self.state, "fixture.delete_note", {"note_id": "1"},
                                 confirm=False, trusted_owner=True)
        result = mcp_client.call_tool(
            self.state, "fixture.delete_note", {"note_id": "1"},
            confirm=True, trusted_owner=True,
        )
        self.assertEqual(result, {"deleted": "1"})

        self.configure("open", allowed_tools=None)
        found = {item["name"]: item for item in mcp_client.planner_tools(self.state, max_age=0)}
        self.assertFalse(found["open.delete_note"]["write_allowed"])
        self.assertTrue(found["open.echo_text"]["read_only"])
        with self.assertRaisesRegex(mcp_client.MCPClientError, "allowed_tools"):
            mcp_client.call_tool(self.state, "open.delete_note", {"note_id": "1"},
                                 confirm=True, trusted_owner=True)

    def test_process_event_blocks_unconfirmed_write_then_applies_confirmed_call(self):
        self.configure(allowed_tools=["echo_text", "delete_note"])
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value=json.dumps(WRITE)),
        ):
            blocked = process_event(
                self.database, EVENT, apply=True, trusted_owner=True, model="test-local:7b",
            )
        self.assertEqual(blocked["action"], "blocked")
        self.assertIn("owner-confirmed", blocked["error"])
        self.assertNotIn("mcp", blocked)

        confirmed = {**WRITE, "detail": 'confirm {"note_id": "7"}'}
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value=json.dumps(confirmed)),
        ):
            applied = process_event(
                self.database, EVENT, apply=True, trusted_owner=True, model="test-local:7b",
            )
        self.assertEqual(applied["action"], "applied")
        self.assertEqual(applied["mcp"], {"deleted": "7"})

    def test_no_servers_never_connect_and_planner_keeps_working(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with patch("talos_core.mcp_client.tools", side_effect=AssertionError("must not connect")):
            self.assertEqual(mcp_client.planner_tools(self.state), [])
            with (
                patch("agent.check_ollama_model"),
                patch("agent.model_text", return_value=json.dumps(REPLY)) as model,
            ):
                result = plan(self.database, EVENT, model="test-local:7b")

        self.assertEqual(result["disposition"], "reply")
        self.assertEqual(model.call_args.kwargs["schema"], DECISION_SCHEMA)
        context = json.loads(model.call_args.args[0][-1]["content"])
        self.assertNotIn("mcp_tools", context)
        self.assertNotIn(".", model.call_args.kwargs["schema"]["properties"]["tool"]["enum"])

    def test_discovery_is_briefly_cached_and_fails_soft(self):
        self.configure(allowed_tools=["echo_text", "delete_note"])
        self.assertEqual(len(mcp_client.planner_tools(self.state)), 2)
        self.assertEqual(len(mcp_client.planner_tools(self.state)), 2)
        self.assertEqual(self.log.read_text(encoding="utf-8").count("start"), 1)

        self.assertEqual(len(mcp_client.planner_tools(self.state, max_age=0)), 2)
        self.assertEqual(self.log.read_text(encoding="utf-8").count("start"), 2)

        self.configure("broken", allowed_tools=None, command="/nonexistent/mcp-server")
        names = {item["name"] for item in mcp_client.planner_tools(self.state, max_age=0)}
        self.assertIn("fixture.echo_text", names)
        self.assertFalse(any(name.startswith("broken.") for name in names))
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value=json.dumps(REPLY)),
        ):
            self.assertEqual(plan(self.database, EVENT, model="test-local:7b")["disposition"],
                             "reply")


if __name__ == "__main__":
    unittest.main()

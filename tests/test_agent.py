import io
import json
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agent import OLLAMA_MODEL, listen, parse_decision
from talos_core.memory.agent_memory import decisions, open_memory, recent_context, record_decision, review_decision

EVENT = {"kind": "synthetic", "summary": "The window is still open and rain has started."}
PROPOSAL = {
    "disposition": "act",
    "tool": "log_alert",
    "goal": "Keep rain from getting into the room.",
    "reason": "The open window could let in rain.",
    "detail": "Rain has started and the window is open.",
}


def chat_response(proposal: dict) -> dict:
    return {"done": True, "done_reason": "stop", "message": {"content": json.dumps(proposal)}}


class ShadowAgentTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "state.sqlite3"
        self.database = self.enterContext(closing(open_memory(self.path)))

    def test_records_new_model_selected_goal_in_shadow_mode(self):
        source = io.StringIO(json.dumps(EVENT) + "\n")
        output, errors = io.StringIO(), io.StringIO()

        with (
            patch("agent.check_ollama_model") as check_local,
            patch("agent.request_ollama_json", return_value=chat_response(PROPOSAL)) as chat,
        ):
            self.assertEqual(listen(self.database, source, output, errors), 0)
            check_local.assert_called_once_with(OLLAMA_MODEL)

        row = json.loads(output.getvalue())
        self.assertEqual(row, {"id": 1, "mode": "shadow", **PROPOSAL})
        self.assertEqual(decisions(self.database)[0]["proposal"], PROPOSAL)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(chat.call_args.args, ("POST", "/api/chat"))
        payload = chat.call_args.kwargs["payload"]
        self.assertFalse(payload["stream"])
        self.assertNotIn("tools", payload)
        self.assertEqual(json.loads(payload["messages"][-1]["content"])["new_event"], EVENT)

    def test_owner_feedback_survives_restart_and_reaches_next_decision(self):
        decision_id = record_decision(self.database, EVENT, PROPOSAL)
        review_decision(self.database, decision_id, "Keep weather alerts quiet while I am asleep.")
        next_event = {"kind": "synthetic", "summary": "A thunderstorm is approaching at night."}
        response = chat_response({**PROPOSAL, "disposition": "ignore", "tool": "none"})
        with (
            closing(open_memory(self.path)) as restarted,
            patch("agent.check_ollama_model"),
            patch("agent.request_ollama_json", return_value=response) as chat,
        ):
            self.assertEqual(
                listen(restarted, io.StringIO(json.dumps(next_event)), io.StringIO(), io.StringIO()), 0
            )
        context = json.loads(chat.call_args.kwargs["payload"]["messages"][-1]["content"])
        self.assertEqual(context["new_event"], next_event)
        self.assertEqual(
            context["owner_feedback"][0]["feedback"], "Keep weather alerts quiet while I am asleep."
        )

    def test_aged_event_leaves_recent_context_but_review_remains(self):
        decision_id = record_decision(self.database, EVENT, PROPOSAL)
        with self.database:
            self.database.execute(
                "UPDATE decisions SET recorded_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), decision_id),
            )
        review_decision(self.database, decision_id, "A nearby storm is worth logging.")
        context = recent_context(self.database)
        self.assertEqual(context["recent_observations"], [])
        self.assertEqual(len(context["owner_feedback"]), 1)

    def test_bad_event_or_unapproved_tool_does_not_block_next_event(self):
        source = io.StringIO("malformed\n" + (json.dumps(EVENT) + "\n") * 2)
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("agent.check_ollama_model"),
            patch("agent.request_ollama_json", side_effect=[
                chat_response({**PROPOSAL, "tool": "send_video"}), chat_response(PROPOSAL),
            ]),
        ):
            self.assertEqual(listen(self.database, source, output, errors), 1)

        self.assertEqual(len(decisions(self.database)), 1)
        self.assertEqual(json.loads(output.getvalue())["id"], 1)
        self.assertIn("event 1", errors.getvalue())
        self.assertIn("unsupported disposition or tool", errors.getvalue())

    def test_cross_validates_disposition_and_tool(self):
        for variant in (
            {**PROPOSAL, "disposition": "wait"},
            {**PROPOSAL, "tool": "none"},
            {**PROPOSAL, "goal": ""},
            {**PROPOSAL, "reason": None},
        ):
            with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                parse_decision(json.dumps(variant))

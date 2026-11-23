import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core.memory.agent_memory import decisions, open_memory
from talos_core.room.room_service import Handler, Room

GREETING = {
    "disposition": "act", "tool": "speak", "goal": "Welcome the arrival",
    "reason": "The room was just occupied.", "detail": "Hello. Please admire the restraint.",
}


class RoomServiceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "room.sqlite3"
        self.room = Room(self.path, "mock-local")

    def test_presence_alone_never_generates_speech_or_uses_model(self):
        with patch("talos_core.room.room_service.plan") as planner:
            for occupied in (True, False, True):
                status, arrival = self.room.route("POST", "/v1/presence", {"occupied": occupied})
                self.assertEqual(status, 202)
                self.assertTrue(self.room.step())
                self.assertIsNone(self.room.route("GET", "/v1/speech", {})[1]["item"])
                receipt = self.room.route("GET", f"/v1/events/{arrival['event_id']}", {})[1]
                self.assertEqual(receipt["status"], "done")
            planner.assert_not_called()

    def test_stale_presence_event_does_not_run_after_departure(self):
        event_id = self.room.route("POST", "/v1/presence", {"occupied": True})[1]["event_id"]
        self.room.route("POST", "/v1/presence", {"occupied": False})
        self.assertTrue(self.room.step())
        self.assertEqual(self.room.route("GET", f"/v1/events/{event_id}", {})[1]["status"], "stale")

    def test_wait_schedules_one_durable_follow_up(self):
        self.room.route("POST", "/v1/events", {"kind": "task_update", "summary": "A task is delayed."})
        wait = {**GREETING, "disposition": "wait", "tool": "none", "detail": "Check whether they stayed."}
        with patch("talos_core.room.room_service.plan", return_value=wait):
            self.assertTrue(self.room.step())
        with closing(open_memory(self.path)) as db, db:
            row = db.execute("SELECT * FROM room_events WHERE status = 'pending'").fetchone()
            self.assertEqual(row["depth"], 1)
            self.assertGreater(row["run_at"], "2000")
            db.execute("UPDATE room_events SET run_at = '2000' WHERE id = ?", (row["id"],))
        restarted = Room(self.path, "mock-local")
        with patch("talos_core.room.room_service.plan", return_value=wait):
            self.assertTrue(restarted.step())
            self.assertFalse(restarted.step())

    def test_backend_failure_retries_without_creating_actions(self):
        _, payload = self.room.route("POST", "/v1/events", {"kind": "test", "summary": "A new event."})
        with patch("talos_core.room.room_service.plan", side_effect=RuntimeError("backend down")):
            self.assertTrue(self.room.step())
            self.assertFalse(self.room.step())
        result = self.room.route("GET", f"/v1/events/{payload['event_id']}", {})[1]
        self.assertEqual((result["attempts"], result["status"], result["decision_id"]), (1, "pending", None))
        self.assertIsNone(self.room.route("GET", "/v1/speech", {})[1]["item"])

    def test_voice_turn_uses_real_local_actions_and_respects_retention(self):
        voice_text = "Remind me in fifteen minutes."
        reminder = {
            **GREETING, "tool": "set_reminder", "detail": "Bring in the laundry.",
            "remind_in_minutes": 15,
        }
        with patch("talos_core.room.room_service.plan", return_value=reminder) as planner:
            _, receipt = self.room.route(
                "POST", "/v1/turn", {"text": voice_text, "source": "voice", "remember": False},
            )
        self.assertIs(planner.call_args.kwargs["trusted_owner"], False)
        self.assertIs(planner.call_args.kwargs["private_context"], False)
        self.assertEqual(receipt["action"], "applied")
        self.assertEqual(self.room.route("GET", "/v1/inbox", {})[1]["items"][0]["kind"], "reminder")
        with closing(open_memory(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 0)
            self.assertNotIn(voice_text, json.dumps(decisions(db)))

    def test_voice_opt_in_archives_both_sides_of_the_turn(self):
        reply = {**GREETING, "disposition": "reply", "tool": "none", "detail": "A reply."}
        with patch("talos_core.room.room_service.plan", return_value=reply):
            self.room.route("POST", "/v1/turn", {"text": "Hello Talos", "source": "voice", "remember": True})
        with closing(open_memory(self.path)) as db:
            self.assertEqual(
                [tuple(row) for row in db.execute("SELECT role, text FROM transcripts ORDER BY id")],
                [("user", "Hello Talos"), ("assistant", "A reply.")],
            )

    def test_http_handler_requires_token_and_rejects_oversize_payloads(self):
        handler = object.__new__(Handler)
        handler.path = "/v1/presence"
        handler.server = SimpleNamespace(token="test-key", room=self.room)
        with patch.object(handler, "reply") as response:
            handler.headers = {"Content-Length": "9000"}
            handler.handle_request("POST")
            self.assertEqual(response.call_args.args[0], 401)
            handler.headers["Authorization"] = "Bearer test-key"
            handler.handle_request("POST")
            self.assertEqual(response.call_args.args[0], 400)
            body = b'{"occupied": true}'
            handler.headers["Content-Length"] = str(len(body))
            handler.rfile = io.BytesIO(body)
            handler.handle_request("POST")
            self.assertEqual(response.call_args.args[0], 202)

    def test_spoken_follow_up_uses_ephemeral_context_only(self):
        reply = {**GREETING, "disposition": "reply", "tool": "none", "detail": "A reply."}
        with patch("talos_core.room.room_service.plan", return_value=reply) as planner:
            self.room.route("POST", "/v1/turn", {"text": "The key is blue.", "source": "voice"})
            self.room.route("POST", "/v1/turn", {"text": "What color is it?", "source": "voice"})
        dialogue = planner.call_args.kwargs["room_context"]["recent_dialogue"]
        self.assertEqual(dialogue[0]["content"], "The key is blue.")
        with closing(open_memory(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 0)
            self.assertNotIn("The key is blue.", json.dumps(decisions(db)))

    def test_actionable_event_can_still_speak_and_be_acknowledged(self):
        self.room.route("POST", "/v1/events", {"kind": "deadline", "summary": "A timer is due."})
        with patch("talos_core.room.room_service.plan", return_value={**GREETING, "detail": "Your timer is due."}):
            self.assertTrue(self.room.step())
        item = self.room.route("GET", "/v1/speech", {})[1]["item"]
        self.assertEqual(item["text"], "Your timer is due.")
        for _ in range(2):
            self.assertEqual(self.room.route("POST", "/v1/speech/ack", {"id": item["id"]})[0], 200)
        self.assertIsNone(self.room.route("GET", "/v1/speech", {})[1]["item"])

    def test_voice_turn_uses_smaller_planning_budget_and_reports_duration(self):
        reply = {**GREETING, "disposition": "reply", "tool": "none", "detail": "A reply."}
        with patch("talos_core.room.room_service.plan", return_value=reply) as planner:
            _, result = self.room.route("POST", "/v1/turn", {"text": "Hello Talos", "source": "voice"})
        self.assertEqual(planner.call_args.kwargs["max_lookups"], 1)
        self.assertEqual(planner.call_args.kwargs["max_tokens"], 256)
        self.assertGreaterEqual(result["queue_ms"], 0)
        self.assertGreaterEqual(result["planning_ms"], 0)

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_memory import open_memory
from improve_talos import draft_patch
from room_dialogue import EMPTY, HEARD, IDENTITY, TYPED, UNKNOWN, VISIBLE, decision
from room_research import FREE_MODEL, public_answer
from room_service import Room


class RoomDialogueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.room = Room(self.root / "memory.sqlite", "local")

    def turn(self, text, source="voice", **extra):
        return self.room.route("POST", "/v1/turn", {"text": text, "source": source, **extra})[1]

    def test_screenshot_turns_and_camera_snapshot_skip_inference(self):
        self.room.route("POST", "/v1/presence", {"occupied": False})
        with patch("room_service.plan", side_effect=AssertionError("unnecessary model call")):
            self.assertEqual(self.turn("Can you hear me tell us?")["reply"], HEARD)
            self.assertEqual(self.turn("Can you hear me?", source="text")["reply"], TYPED)
            self.assertEqual(self.turn("Hello.")["reply"], "")
            self.assertEqual(self.turn("Who am I?")["reply"], IDENTITY)
            for value, expected in ((True, VISIBLE), (False, EMPTY), (None, UNKNOWN)):
                self.assertEqual(self.turn("Talos, can you see me?", occupied=value)["reply"], expected)
            self.assertEqual(self.turn("Can you see me?")["reply"], EMPTY)

    def test_current_sensor_questions_use_the_input_channel_and_latest_observation(self):
        with patch("room_service.plan", side_effect=AssertionError("unexpected planner call")):
            for occupied, reply in ((True, VISIBLE), (False, EMPTY), (None, UNKNOWN)):
                self.room.route("POST", "/v1/presence", {"occupied": occupied})
                self.assertEqual(self.turn("Talos, can you see me right now?")["reply"], reply)
                self.assertEqual(self.turn("Is there anyone in the room now?")["reply"], reply)
            self.assertEqual(self.turn("Can you hear me now, Talos?")["reply"], HEARD)
            self.assertEqual(self.turn("Can you hear me now?", source="text")["reply"], TYPED)
        with patch("room_service.plan", return_value=decision("reply", "Which photo?")) as planner:
            self.assertEqual(self.turn("Talos, can you see anyone in the photo right now?")["reply"],
                             "Which photo?")
            planner.assert_called_once()

    def test_unaddressed_speech_has_no_spoken_reply_or_dialogue_history(self):
        for proposal in (decision("ignore", ""), decision("reply", "Hello.")):
            with patch("room_service.plan", return_value=proposal):
                self.assertEqual(self.turn("I left the door open.")["reply"], "")
        self.assertEqual(self.room.voice_window, [])

    def test_compact_response_can_apply_reminder_without_generating_planning_prose(self):
        short = {"action": "set_reminder", "minutes": 10, "text": "Check the oven."}
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=json.dumps(short)) as model:
            self.assertEqual(self.turn("Remind me in ten minutes to check the oven.")["action"], "applied")
            self.assertEqual(model.call_args.kwargs["schema"]["required"], ["action", "text"])
        with closing(open_memory(self.room.db_path)) as db:
            self.assertEqual(db.execute("SELECT kind FROM inbox").fetchone()[0], "reminder")

    def test_invalid_short_tool_output_cannot_apply(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"set_reminder","text":"bad","minutes":false}'), self.assertRaises(RuntimeError):
            self.turn("Remind me to call.")

    def test_explicit_lookup_only_sends_query_and_surfaces_sources(self):
        sources = [{"title": "Example", "url": "https://example.org", "excerpt": "A statement."}]
        with patch("room_service.search_web", return_value=sources) as search, patch(
            "room_service.model_text", return_value="A statement."
        ):
            result = self.turn("Talos, search the web for lunar eclipses.")
            search.assert_called_once_with("lunar eclipses.")
            self.assertEqual(result["sources"], sources)
        self.room.online = False
        with patch("room_service.public_answer") as remote:
            self.assertIn("off", self.turn("Ask Respan what is ice?")["reply"])
            remote.assert_not_called()

    def test_planner_can_select_search_for_typed_question(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"search_web","text":"public facts"}'), patch(
                    "room_service.search_web", return_value=[]) as search:
            result = self.turn("Which comet is visible tonight?", source="text")
            search.assert_called_once_with("Which comet is visible tonight?")
            self.assertIn("find", result["reply"])

    def test_respan_has_one_named_free_route_and_uses_no_conversation_history(self):
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"choices":[{"message":{"content":"Four."}}]}'
        with patch.dict(os.environ, RESPAN_API_KEY="test"), patch(
            "room_research.http.client.HTTPSConnection", return_value=connection
        ):
            self.assertEqual(public_answer("Two plus two?"), "Four.")
            payload = json.loads(connection.request.call_args.kwargs["body"])
            self.assertTrue(FREE_MODEL.endswith(":free"))
            self.assertEqual(payload["model"], FREE_MODEL)
            self.assertEqual(payload["messages"][-1]["content"], "Two plus two?")
            response.status = 429
            with self.assertRaisesRegex(RuntimeError, "429"):
                public_answer("Again?")
            self.assertEqual(connection.request.call_count, 2)

    def test_code_proposal_checks_patch_against_copy_and_preserves_live_source(self):
        (self.root / "example.py").write_text("value = 1\n")
        (self.root / "state").mkdir()
        (self.root / "state" / "respan.key").write_text("fictional")
        real_run = subprocess.run
        def run(command, **kwargs):
            if command[0] == "mock-codex":
                snapshot = kwargs["cwd"]
                self.assertFalse((snapshot / "state").exists())
                self.assertIn("read-only", command)
                self.assertIn("--oss", command)
                (snapshot / "proposal.diff").write_text(
                    "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n")
                return
            return real_run(command, **kwargs)
        with patch("improve_talos.shutil.which", return_value="mock-codex"), patch(
            "improve_talos.subprocess.run", side_effect=run
        ):
            patch_file = draft_patch("Fix the example.", root=self.root)
        self.assertTrue(patch_file.exists())
        self.assertEqual((self.root / "example.py").read_text(), "value = 1\n")

    def test_slow_stages_drive_code_issue_selection_without_transcripts(self):
        from improve_talos import observed_issue
        from room_service import record_slow_stage

        with closing(open_memory(self.room.db_path)) as db, db:
            record_slow_stage(db, "voice decision", 2100)
        with self.assertRaisesRegex(RuntimeError, "No repeated"):
            observed_issue(self.room.db_path)
        with closing(open_memory(self.room.db_path)) as db, db:
            record_slow_stage(db, "voice decision", 3500)
        issue = observed_issue(self.room.db_path)
        self.assertIn("2 times", issue)
        self.assertIn("3500 ms", issue)

    def test_upgrade_existing_v2_memory_preserves_presence(self):
        with closing(open_memory(self.room.db_path)) as db, db:
            db.execute("DROP TABLE room_slow_stages")
            db.execute("PRAGMA user_version = 2")
            db.execute("INSERT INTO room_presence VALUES (1, 1, '2000-01-01')")
        with closing(open_memory(self.room.db_path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT occupied FROM room_presence").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM room_slow_stages").fetchone()[0], 0)

import base64
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

    def test_voice_identity_questions_require_speaker_evidence(self):
        profile = "00000000-0000-4000-8000-000000000001"
        self.room.route("POST", "/v1/visitors/enroll", {
            "id": profile, "agreed": True, "name": "Example",
            "template": base64.b64encode(bytes(128)).decode(),
            "introduction": "My name is Example.",
        })
        with patch("room_service.plan", side_effect=AssertionError("unexpected planner")), patch(
            "room_service.check_ollama_model", side_effect=AssertionError("unexpected inference")
        ):
            self.assertEqual(self.turn("Talos, whose voice is this?")["reply"], IDENTITY)
            self.assertEqual(self.turn("Can you identify my voice?", source="text")["reply"], TYPED)
            for extra in ({}, {"visitor": profile}):
                _, result = self.room.route("POST", "/v1/voice/facing", {
                    "text": "Do you recognize my voice?", "occupied": True, **extra,
                })
                self.assertEqual(result["reply"], IDENTITY)
        with patch("room_service.plan", return_value=decision("reply", "At the conference?")) as planner:
            self.turn("Who is speaking at the conference?")
            planner.assert_called_once()

    def test_unaddressed_speech_has_no_spoken_reply_or_dialogue_history(self):
        for proposal in (decision("ignore", ""), decision("reply", "Hello.")):
            with patch("room_service.plan", return_value=proposal):
                self.assertEqual(self.turn("I left the door open.")["reply"], "")
        self.assertEqual(self.room.voice_window, [])

    def test_compact_response_can_apply_reminder_without_generating_planning_prose(self):
        short = {"action": "set_reminder", "minutes": 10, "text": "Check the oven."}
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=json.dumps(short)) as model:
            self.assertEqual(self.turn("In ten minutes, could you remind me about the oven?")["action"], "applied")
            self.assertEqual(model.call_args.kwargs["schema"]["required"], ["action", "text"])
        with closing(open_memory(self.room.db_path)) as db:
            self.assertEqual(db.execute("SELECT kind FROM inbox").fetchone()[0], "reminder")

    def test_follow_up_preserves_roles_and_ends_with_current_request(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"reply","text":"Three."}') as model:
            self.turn("How many wheels are on a tricycle?")
            self.turn("And on two of those combined?")
        messages = model.call_args.args[0]
        self.assertEqual([message["role"] for message in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "How many wheels are on a tricycle?")
        self.assertEqual(messages[2]["content"], "Three.")
        self.assertTrue(messages[-1]["content"].endswith("And on two of those combined?"))

    def test_spoken_calculation_uses_local_arithmetic(self):
        for expr, expected in (("3 * 2", "3 times 2 is 6."), ("4", "4."),
                               ("1 / 0", "I couldn't calculate that expression."),
                               ("3 and some code", "I couldn't calculate that expression.")):
            with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                    json.dumps({"action": "calculate", "text": expr})):
                self.assertEqual(self.turn("What does that come to?")["reply"], expected)

    def test_invalid_short_tool_output_cannot_apply(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"set_reminder","text":"bad","minutes":false}'), self.assertRaises(RuntimeError):
            self.turn("Set a reminder for the call.")

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
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertEqual(db.execute("SELECT occupied FROM room_presence").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM room_slow_stages").fetchone()[0], 0)

    def test_local_reminder_duration_and_boundaries(self):
        from room_dialogue import explicit_reminder
        for duration, minutes in [("twenty-five minutes", 25), ("an hour", 60), ("30 days", 43200)]:
            result = explicit_reminder(f"Hey Talos, please remind me in {duration} to check the oven.")
            self.assertEqual((result["remind_in_minutes"], result["detail"]), (minutes, "check the oven"))
        for duration in ("zero minutes", "31 days", "one hour and ten minutes", "two seconds"):
            self.assertIsNone(explicit_reminder(f"Remind me in {duration} to check the oven."))
        self.assertIsNone(explicit_reminder("She said remind me in 10 minutes to eat."))
        self.assertIsNone(explicit_reminder("Don't remind me in 10 minutes to eat."))

    def test_exact_reminder_in_shadow_mode_leaves_inbox_untouched(self):
        self.room = Room(self.room.db_path, "local", apply=False)
        receipt = self.turn("Remind me in 2 hours to stretch.")
        self.assertEqual(receipt["action"], "shadow")
        self.assertEqual(self.room.route("GET", "/v1/inbox", {})[1]["items"], [])

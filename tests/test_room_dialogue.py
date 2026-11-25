import json
import os
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from talos_core.memory.agent_memory import open_memory
from talos_core.room.room_dialogue import EMPTY, HEARD, IDENTITY, TYPED, UNKNOWN, VISIBLE, decision
from talos_core.room.room_research import FREE_MODEL, FREE_MODELS, public_answer
from talos_core.room.room_service import Room
from talos_core.self_improvement.improve_talos import draft_patch


class RoomDialogueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.room = Room(self.root / "memory.sqlite", "local")

    def turn(self, text, source="voice", **extra):
        return self.room.route("POST", "/v1/turn", {"text": text, "source": source, **extra})[1]

    def test_screenshot_turns_and_camera_snapshot_skip_inference(self):
        self.room.route("POST", "/v1/presence", {"occupied": False})
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("unnecessary model call")):
            self.assertEqual(self.turn("Can you hear me tell us?")["reply"], HEARD)
            self.assertEqual(self.turn("Can you hear me?", source="text")["reply"], TYPED)
            self.assertEqual(self.turn("Hello.")["reply"], "")
            self.assertEqual(self.turn("Who am I?")["reply"], IDENTITY)
            for value, expected in ((True, VISIBLE), (False, EMPTY), (None, UNKNOWN)):
                self.assertEqual(self.turn("Talos, can you see me?", occupied=value)["reply"], expected)
            self.assertEqual(self.turn("Can you see me?")["reply"], EMPTY)

    def test_current_sensor_questions_use_the_input_channel_and_latest_observation(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("unexpected planner call")):
            for occupied, reply in ((True, VISIBLE), (False, EMPTY), (None, UNKNOWN)):
                self.room.route("POST", "/v1/presence", {"occupied": occupied})
                self.assertEqual(self.turn("Talos, can you see me right now?")["reply"], reply)
                self.assertEqual(self.turn("Is there anyone in the room now?")["reply"], reply)
            self.assertEqual(self.turn("Can you hear me now, Talos?")["reply"], HEARD)
            self.assertEqual(self.turn("Can you hear me now?", source="text")["reply"], TYPED)
        with patch("talos_core.room.room_service.plan", return_value=decision("reply", "Which photo?")) as planner:
            self.assertEqual(self.turn("Talos, can you see anyone in the photo right now?")["reply"],
                             "Which photo?")
            planner.assert_called_once()

    def test_conversational_audio_checks_stay_grounded_in_the_input_channel(self):
        from talos_core.room.room_dialogue import IDENTITY, sensor_answer

        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("unexpected planner call")):
            for words in ("Talos, am I coming through?", "Can you hear my voice?",
                          "Is my voice coming through right now?"):
                for source, expected in (("text", TYPED), ("voice", HEARD)):
                    with self.subTest(words=words, source=source):
                        self.assertEqual(self.turn(words, source=source)["reply"], expected)
            self.assertEqual(self.turn("Can you tell who I am?")["reply"], IDENTITY)
        self.assertIsNone(sensor_answer("Am I coming through the right door?", "voice", True))

    def test_typed_coding_job_queues_runs_and_reports_unchanged(self):
        state = self.root / "state"
        state.mkdir()
        (self.root / "talos_core").mkdir()
        target = self.root / "talos_core" / "example.py"
        target.write_text("VALUE = 1\n")
        room = Room(state / "memory.sqlite", "local")
        with patch("talos_core.room.room_coding.local_infer",
                   return_value=lambda prompt: "{}"):
            reply = room.route("POST", "/v1/turn", {
                "text": "coding job talos_core/example.py: Add a note.",
                "source": "text"})[1]["reply"]
        self.assertIn("declined", reply)
        self.assertIn("Target unchanged: yes", reply)
        self.assertEqual(target.read_text(), "VALUE = 1\n")

    def test_typed_coding_plan_runs_verify_only(self):
        state = self.root / "state"
        state.mkdir()
        (self.root / "talos_core").mkdir()
        target = self.root / "talos_core" / "example.py"
        target.write_text("VALUE = 1\n")
        (state / "plan.json").write_text(json.dumps({"steps": [
            {"id": "one", "target": "talos_core/example.py", "request": "Add a note."}]}))
        room = Room(state / "memory.sqlite", "local")
        with patch("talos_core.room.room_coding.local_infer",
                   return_value=lambda prompt: "{}"):
            reply = room.route("POST", "/v1/turn", {
                "text": "coding plan state/plan.json", "source": "text"})[1]["reply"]
        self.assertIn("declined", reply)
        self.assertIn("Targets unchanged: yes", reply)
        self.assertEqual(target.read_text(), "VALUE = 1\n")

    def test_free_form_plan_is_generated_and_run_verify_only(self):
        state = self.root / "state"
        state.mkdir()
        (self.root / "talos_core").mkdir()
        target = self.root / "talos_core" / "example.py"
        target.write_text("VALUE = 1\n")
        room = Room(state / "memory.sqlite", "local")
        generated = {"steps": [{"id": "one", "target": "talos_core/example.py",
                                "request": "Add a note."}]}
        with patch("talos_core.room.room_coding.generate_plan",
                   return_value=generated), \
                patch("talos_core.room.room_coding.repair_plan",
                      side_effect=ValueError("no safe revision")), \
                patch("talos_core.room.room_coding.local_infer",
                      return_value=lambda prompt: "{}"):
            reply = room.route("POST", "/v1/turn", {
                "text": "coding plan: make the example clearer",
                "source": "text"})[1]["reply"]
        self.assertIn("Coding plan", reply)
        self.assertIn("declined", reply)
        self.assertIn("Targets unchanged: yes", reply)
        self.assertEqual(target.read_text(), "VALUE = 1\n")

    def test_free_form_plan_generation_failure_is_reported(self):
        state = self.root / "state"
        state.mkdir()
        room = Room(state / "memory.sqlite", "local")
        with patch("talos_core.room.room_coding.generate_plan",
                   side_effect=ValueError("no safe plan")):
            reply = room.route("POST", "/v1/turn", {
                "text": "coding plan: do something vague",
                "source": "text"})[1]["reply"]
        self.assertIn("Plan rejected: no safe plan", reply)

    def test_capability_questions_are_answered_from_room_state(self):
        from talos_core.room.room_dialogue import capability_answer

        muted = capability_answer("Can you play music?", audio=False)
        self.assertIn("muted", muted.casefold())
        self.assertIn("can't play", muted.casefold())
        self.room.speak = False
        with patch("talos_core.room.room_service.plan",
                   side_effect=AssertionError("unexpected planner call")):
            reply = self.turn("What can you run in the background?", source="text")["reply"]
        self.assertIn("muted", reply.casefold())
        self.assertNotIn("audio playback", reply.casefold())
        self.assertIn("speak and play sound",
                      capability_answer("What can you do?", audio=True))
        self.assertIn("muted", capability_answer(
            "In one short sentence, what can you run in the background?", audio=False).casefold())

    def test_self_reported_name_is_not_biometric_identity(self):
        with patch("talos_core.room.room_service.plan",
                   side_effect=AssertionError("unexpected planner call")):
            self.assertIn("Nice to meet you, Ada",
                          self.turn("My name is Ada.", source="text")["reply"])
            reply = self.turn("Do you recognize me?", source="text")["reply"]
        self.assertIn("introduced yourself as Ada", reply)
        self.assertIn("self-reported", reply)
        self.assertIn("can't verify", reply)

    def test_unaddressed_speech_has_no_spoken_reply_or_dialogue_history(self):
        for proposal in (decision("ignore", ""), decision("reply", "Hello.")):
            with patch("talos_core.room.room_service.plan", return_value=proposal):
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

    def test_absolute_reminders_target_named_clock_times(self):
        from datetime import UTC, datetime

        from talos_core.room.room_dialogue import absolute_reminder

        now = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
        cases = {
            "Remind me to call Sam tomorrow at 9.": ("call Sam", 1380),
            "Remind me to call Sam tomorrow.": ("call Sam", 1380),
            "Set a reminder for tomorrow at 9.": ("Reminder", 1380),
            "Remind me to call Sam at 3pm.": ("call Sam", 300),
            "Remind me to call Sam at 9.": ("call Sam", 1380),
            "Remind me tomorrow at 9 to call Sam.": ("call Sam", 1380),
            "Remind me at 3pm to call Sam.": ("call Sam", 300),
            "Remind me tomorrow to call Sam.": ("call Sam", 1380),
            "Remind me at 9 to call Sam.": ("call Sam", 1380),
            "Remind me at 9 tomorrow to call Sam.": ("call Sam", 1380),
        }
        for text, (detail, minutes) in cases.items():
            with self.subTest(text=text):
                result = absolute_reminder(text, now=now)
                self.assertEqual(result["tool"], "set_reminder")
                self.assertEqual(result["detail"], detail)
                self.assertEqual(result["remind_in_minutes"], minutes)

    def test_simple_timers_are_local(self):
        from talos_core.room.room_dialogue import timer_request

        result = timer_request("Set a timer for 10 minutes.")
        self.assertEqual(result["tool"], "set_reminder")
        self.assertEqual(result["detail"], "Timer")
        self.assertEqual(result["remind_in_minutes"], 10)

    def test_invalid_short_tool_output_cannot_apply(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"set_reminder","text":"bad","minutes":false}'), self.assertRaises(RuntimeError):
            self.turn("Set a reminder for the call.")

    def test_malformed_reply_is_retried_once(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", side_effect=[
                "not json", '{"action":"reply","text":"Recovered."}']) as model:
            self.assertEqual(self.turn("Tell me something.", source="text")["reply"], "Recovered.")
            self.assertEqual(model.call_count, 2)

    def test_explicit_lookup_only_sends_query_and_surfaces_sources(self):
        sources = [{"title": "Example", "url": "https://example.org", "excerpt": "A statement."}]
        with patch("talos_core.room.room_service.search_web", return_value=sources) as search, patch(
            "talos_core.room.room_service.model_text", return_value="A statement."
        ):
            result = self.turn("Talos, search the web for lunar eclipses.")
            search.assert_called_once_with("lunar eclipses.")
            self.assertEqual(result["sources"], sources)
        self.room.online = False
        with patch("talos_core.room.room_service.public_answer") as remote:
            self.assertIn("off", self.turn("Ask Respan what is ice?")["reply"])
            remote.assert_not_called()

    def test_planner_can_select_search_for_typed_question(self):
        with patch("agent.check_ollama_model"), patch("agent.model_text", return_value=
                '{"action":"search_web","text":"public facts"}'), patch(
                    "talos_core.room.room_service.search_web", return_value=[]) as search:
            result = self.turn("Which comet is visible tonight?", source="text")
            search.assert_called_once_with("Which comet is visible tonight?")
            self.assertIn("find", result["reply"])

    def test_respan_rotates_free_routes_and_uses_no_conversation_history(self):
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"choices":[{"message":{"content":"Four."}}]}'
        with patch.dict(os.environ, RESPAN_API_KEY="test"), patch(
            "talos_core.room.room_research.http.client.HTTPSConnection", return_value=connection
        ), patch("talos_core.room.room_research._turns", iter([0, 1, 2])), patch("talos_core.room.room_research._cooldown", {}):
            self.assertEqual(public_answer("Two plus two?"), "Four.")
            payload = json.loads(connection.request.call_args.kwargs["body"])
            self.assertEqual(payload["model"], FREE_MODEL)
            self.assertEqual(payload["messages"][-1]["content"], "Two plus two?")
            response.status = 429
            with self.assertRaisesRegex(RuntimeError, "free text routes"):
                public_answer("Again?")
            attempted = [json.loads(call.kwargs["body"])["model"] for call in connection.request.call_args_list]
            self.assertEqual(attempted[1], FREE_MODELS[1])
            self.assertEqual(set(attempted[1:]), {*FREE_MODELS, "phonic/phonic-1"})
            self.assertEqual(len(attempted), 7)
            with self.assertRaises(RuntimeError):
                public_answer("Still there?")
            self.assertEqual(connection.request.call_count, 7)

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
        with patch("talos_core.self_improvement.improve_talos.shutil.which", return_value="mock-codex"), patch(
            "talos_core.self_improvement.improve_talos.subprocess.run", side_effect=run
        ):
            patch_file = draft_patch("Fix the example.", root=self.root)
        self.assertTrue(patch_file.exists())
        self.assertEqual((self.root / "example.py").read_text(), "value = 1\n")

    def test_slow_stages_drive_code_issue_selection_without_transcripts(self):
        from talos_core.room.room_service import record_slow_stage
        from talos_core.self_improvement.improve_talos import observed_issue

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
        from talos_core.room.room_dialogue import explicit_reminder
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

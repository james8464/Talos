"""Typed requests that must resolve locally without a model round trip."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.room.room_service import CAPABILITIES, Room


class QuickTextAnswerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.room = Room(self.root / "room.sqlite", "local", speak=False, online=False)

    def ask(self, text):
        return self.room.route("POST", "/v1/turn", {"text": text, "source": "text"})[1]

    def test_capability_question_is_answered_without_the_model(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            self.assertEqual(self.ask("Hello Talos, what can you do?")["reply"], CAPABILITIES)

    def test_inbox_question_lists_open_items_without_the_model(self):
        self.ask("Remind me in five minutes to stretch")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            reply = self.ask("What is on my plate today?")["reply"]
        self.assertIn("reminder", reply)
        self.assertIn("stretch", reply)

    def test_knowledge_search_does_not_reuse_the_previous_reminder(self):
        result = {"results": [{"store": "code", "title": "Room", "snippet": "Room routes turns."}]}
        with patch("talos_core.room.room_service.cognitive_engine.query_knowledge",
                   return_value=result) as query:
            self.ask("Remind me in five minutes to stretch")
            reply = self.ask("Search your knowledge for Room.")["reply"]
        query.assert_called_once()
        self.assertIn("Room routes turns.", reply)

    def test_time_question_is_answered_without_the_model(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            reply = self.ask("What time is it?")["reply"]
        self.assertRegex(reply, r"^It is \d\d:\d\d \S+ on \w+ \d\d \w+ \d{4}\.$")

    def test_memory_search_requires_every_term(self):
        rows = [
            {"excerpt": "You told me your workshop code is Gamma-9-Delta.",
             "text": "You told me your workshop code is Gamma-9-Delta."},
            {"excerpt": "I can search your code graph.", "text": "I can search your code graph."},
        ]
        with patch("talos_core.room.room_service.search_transcripts", return_value=rows):
            reply = self.ask("Search your memory for workshop code.")["reply"]
        self.assertIn("Gamma-9-Delta", reply)
        self.assertNotIn("code graph", reply)

    def test_saved_note_can_be_recalled_without_the_model(self):
        self.ask("Remember that the live pass color is cobalt.")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            reply = self.ask("What is the live pass color?")["reply"]
        self.assertEqual(reply, "Live pass color is cobalt.")

    def test_small_talk_and_offline_weather_are_local(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            self.assertEqual(self.ask("Thank you.")["reply"], "You're welcome.")
            self.assertEqual(self.ask("Are you there?")["reply"], "Yes, I'm here.")
            self.assertIn("Online weather lookups are off", self.ask("What's the weather?")["reply"])

    def test_mute_questions_are_answered_locally(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            self.assertIn("audio is muted", self.ask("Are you muted?")["reply"])
            self.assertIn("already muted", self.ask("Stop speaking.")["reply"])

    def test_memory_search_includes_saved_personal_facts(self):
        self.ask("Remember that my live code is cobalt.")
        reply = self.ask("Search your memory for live code.")["reply"]
        self.assertIn("cobalt", reply)

    def test_forgotten_fact_is_not_invented_from_private_context(self):
        self.ask("Remember that my favorite editor is Neovim.")
        self.ask("Forget my favorite editor.")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            reply = self.ask("What is my favorite editor?")["reply"]
        self.assertIn("don't have a saved fact", reply)

    def test_todays_date_is_answered_without_the_model(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            reply = self.ask("What is today's date?")["reply"]
        self.assertRegex(reply, r"^It is \d\d:\d\d \S+ on \w+ \d\d \w+ \d{4}\.$")

    def test_screenshot_and_inspect_are_local_mac_actions(self):
        computer = type("Computer", (), {})()
        computer.screenshot = lambda: type("Receipt", (), {
            "ok": True, "path": "/tmp/shot.png", "detail": "", "metadata": {"bytes": 12}})()
        computer.see = lambda app: {"app": app, "text": "Untitled window"}
        computer.frontmost = lambda: {"app": "TextEdit"}
        computer.windows = lambda: [{"raw": "Untitled — TextEdit"}]
        computer.apps = lambda: [{"name": "TextEdit"}, {"name": "Safari"}]
        computer.find_controls = lambda app, query: [{"ref": "abc123", "label": "Search"}]
        computer.ocr = lambda app: {"text": "Hello from TextEdit"}
        computer.activate = lambda app: type("Receipt", (), {"ok": True, "detail": ""})()
        with patch("talos_core.room.room_service.ComputerUse", return_value=computer):
            shot = self.ask("Take a screenshot.")["reply"]
            seen = self.ask("Inspect TextEdit.")["reply"]
            opened = self.ask("Open TextEdit.")["reply"]
            windows = self.ask("What windows are open?")["reply"]
            front = self.ask("Which app is frontmost?")["reply"]
            apps = self.ask("List running apps.")["reply"]
            found = self.ask("Find Search in TextEdit.")["reply"]
            ocr = self.ask("Read text in TextEdit.")["reply"]
        self.assertIn("/tmp/shot.png", shot)
        self.assertEqual(seen, "TextEdit: Untitled window")
        self.assertEqual(opened, "Opened TextEdit.")
        self.assertIn("Untitled — TextEdit", windows)
        self.assertIn("TextEdit", front)
        self.assertIn("Safari", apps)
        self.assertIn("abc123", found)
        self.assertIn("Hello from TextEdit", ocr)

    def test_permission_question_reports_both_grants(self):
        with patch("talos_core.room.room_service.permissions",
                   return_value={"accessibility": True, "screen_recording": False}):
            reply = self.ask("Check Mac permissions.")["reply"]
        self.assertIn("Accessibility is granted", reply)
        self.assertIn("screen recording is not granted", reply)

    def test_arithmetic_health_and_code_graph_are_local(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            arithmetic = self.ask("What is 144 / 12?")["reply"]
            health = self.ask("Check your health.")["reply"]
            with patch("talos_core.knowledge_graph.graph.search",
                       return_value=[{"source": "talos_core/room/room_service.py",
                                      "line": "L1", "title": "Room"}]):
                code = self.ask("Search the code graph for Room.")["reply"]
        self.assertEqual(arithmetic, "144 divided by 12 is 12.")
        self.assertIn("Audio is muted", health)
        self.assertIn("room_service.py", code)

    def test_skills_nodes_todos_and_maintenance_are_local(self):
        from talos_core import nodes, skills
        skills.create(self.root, "Live check", "Runs live checks", "Ask Talos.")
        nodes.register(self.root, "local-mac")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            self.assertIn("Live check", self.ask("List my skills.")["reply"])
            self.assertIn("local-mac", self.ask("List nodes.")["reply"])
            self.assertIn("check the build", self.ask("Add a todo to check the build.")["reply"])
            self.assertIn("check the build", self.ask("Show my todos.")["reply"])
        (self.root / "Maintenance.txt").write_text("Self-check · healthy\n")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("planner should not run")):
            self.assertIn("Self-check", self.ask("Show the maintenance report.")["reply"])


if __name__ == "__main__":
    unittest.main()

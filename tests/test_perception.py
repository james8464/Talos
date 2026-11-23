import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core.mac import perception

SNAP = {
    "app": "Notes", "count": 4,
    "elements": [
        {"ref": "a1", "role": "AXButton", "title": "New Note", "center_screen": [812, 64]},
        {"ref": "b2", "role": "AXButton", "title": "Delete", "center_screen": [700, 64]},
        {"ref": "c3", "role": "AXTextArea", "title": "", "value": "Meeting notes",
         "center_screen": [500, 300]},
        {"ref": "d4", "role": "AXButton", "title": "Share", "center_screen": [900, 64]},
    ],
}


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append(list(argv))
        command = " ".join(argv[1:])
        for needle, payload in self.responses.items():
            if needle in command:
                body = json.dumps(payload) if isinstance(payload, dict) else payload
                return SimpleNamespace(returncode=0, stdout=body, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")


class PerceptionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_find_ranks_visible_controls_by_label(self):
        runner = FakeRunner({"ax snapshot": SNAP})
        matches = perception.find("Notes", "new note", runner=runner)
        self.assertEqual(matches[0]["ref"], "a1")
        self.assertEqual(matches[0]["role"], "AXButton")
        self.assertEqual(matches[0]["center"], [812, 64])

    def test_describe_is_compact_and_includes_refs(self):
        text = perception.describe(SNAP)
        self.assertIn('#a1 AXButton "New Note"', text)
        self.assertIn("@screen[812,64]", text)
        self.assertLessEqual(len(text.splitlines()), 4)

    def test_choose_lexically_finds_an_unseen_button(self):
        runner = FakeRunner({"ax snapshot": SNAP, "shot annotate": {"path": "/tmp/marks.png", "marks": []}})
        choice = perception.choose("Notes", "delete", runner=runner)
        self.assertEqual(choice["ref"], "b2")
        self.assertEqual(choice["source"], "labels")

    def test_choose_accepts_a_vision_model_ref(self):
        runner = FakeRunner({"ax snapshot": SNAP, "shot annotate": {"path": "/tmp/marks.png",
                                                                  "marks": []}})
        choice = perception.choose(
            "Notes", "share this", runner=runner,
            decider=lambda goal, marks, elements, shot: {"ref": "d4", "reason": "share control"},
        )
        self.assertEqual(choice["ref"], "d4")
        self.assertEqual(choice["source"], "model")

    def test_click_and_set_value_use_ax_refs(self):
        runner = FakeRunner({"ax press": {"ok": True, "verified": True},
                             "ax setvalue": {"ok": True}})
        self.assertTrue(perception.click_ref("Notes", "a1", runner=runner)["ok"])
        self.assertTrue(perception.type_into_ref("Notes", "c3", "hello", runner=runner)["ok"])
        self.assertTrue(any("--ref a1" in " ".join(call) for call in runner.calls))
        self.assertTrue(any("--text hello" in " ".join(call) for call in runner.calls))

    def test_missing_kit_reports_install_hint(self):
        with patch.object(perception, "command", side_effect=perception.KitUnavailable("install")):
            self.assertFalse(perception.available())
            with self.assertRaises(perception.KitUnavailable):
                perception.snapshot("Notes")

    def test_stats_reports_availability(self):
        self.assertIn("available", perception.stats())


if __name__ == "__main__":
    unittest.main()

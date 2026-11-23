import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
from talos_core.mac.computer_use import ComputerUse
from talos_core.memory.agent_memory import open_memory


class _Image:
    def convert(self, mode):
        return self

    def thumbnail(self, size):
        return None

    def save(self, stream, fmt, **options):
        stream.write(b"\xff\xd8\xfffixture\xff\xd9")


class CapabilitySmokeTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.computer = ComputerUse(self.state, cursor=False)

    def test_control_helpers_are_callable_and_targeted(self):
        with patch.object(self.computer.perception(), "find", return_value=[{"ref": "a"}]) as find, \
             patch.object(self.computer.perception(), "type_into_ref",
                          return_value={"ok": True}) as type_ref, \
             patch.object(self.computer, "window_bounds", return_value=(0, 0, 100, 100)), \
             patch.object(self.computer, "_show_cursor"):
            self.assertEqual(self.computer.find_controls("TextEdit", "save"), [{"ref": "a"}])
            self.assertTrue(self.computer.type_ref("TextEdit", "a", "hello")["ok"])
        find.assert_called_once()
        type_ref.assert_called_once()

    def test_image_helpers_produce_bounded_jpeg(self):
        fake_pil = SimpleNamespace(Image=SimpleNamespace(open=lambda stream: _Image()))
        with patch.dict(sys.modules, {"PIL": fake_pil, "PIL.Image": fake_pil.Image}), \
             patch.object(self.computer, "screenshot_bytes", return_value=b"\x89PNG"):
            self.assertTrue(self.computer.screenshot_jpeg())

        def fake_shot(path=None, region=None):
            Path(path).write_bytes(b"\x89PNG")
            return SimpleNamespace(ok=True)

        with patch.dict(sys.modules, {"PIL": fake_pil, "PIL.Image": fake_pil.Image}), \
             patch.object(self.computer, "_harness", return_value=None), \
             patch.object(self.computer, "window_bounds", return_value=(0, 0, 10, 10)), \
             patch.object(self.computer, "screenshot", side_effect=fake_shot):
            self.assertTrue(self.computer.window_jpeg("TextEdit"))

    def test_planner_read_tools(self):
        database = open_memory(self.state / "agent.sqlite3")
        self.addCleanup(database.close)
        self.assertIn("T", agent.read_tool(database, "current_time", ""))
        self.assertEqual(agent.read_tool(database, "review_decisions", ""), [])
        self.assertIn("matches", agent.read_tool(database, "search_code_graph", "decomposition"))
        with patch("talos_core.mac.computer_use.ComputerUse.see",
                   return_value={"text": "[#a] AXButton", "elements": []}):
            result = agent.read_tool(database, "inspect_app", "TextEdit")
        self.assertIn("AXButton", result["text"])


if __name__ == "__main__":
    unittest.main()

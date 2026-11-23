import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import agent
from talos_core.memory.agent_memory import open_memory


def proposal(detail="click 10 20"):
    return {"disposition": "act", "tool": "desktop_action", "goal": "Act on the Mac",
            "reason": "The owner asked.", "detail": detail}


class DesktopActionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = open_memory(self.state / "agent.sqlite3")
        self.addCleanup(self.db.close)

    def test_trusted_owner_can_execute_a_desktop_action(self):
        event = {"kind": "owner_request", "summary": "Click the button"}
        with patch.object(agent, "plan", return_value=proposal()), \
             patch.object(agent, "desktop_action", return_value="completed") as run:
            result = agent.process_event(self.db, event, apply=True, trusted_owner=True, model="fixture")
        self.assertEqual(result["action"], "applied")
        self.assertEqual(result["desktop"], "completed")
        run.assert_called_once()

    def test_untrusted_event_cannot_drive_the_machine(self):
        event = {"kind": "room_event", "summary": "Click the button"}
        with patch.object(agent, "plan", return_value=proposal()), \
             patch.object(agent, "desktop_action") as run:
            result = agent.process_event(self.db, event, apply=True, trusted_owner=False, model="fixture")
        self.assertEqual(result["action"], "blocked")
        run.assert_not_called()

    def test_shadow_mode_previews_without_acting(self):
        event = {"kind": "owner_request", "summary": "Click the button"}
        with patch.object(agent, "plan", return_value=proposal()), \
             patch.object(agent, "desktop_action") as run:
            result = agent.process_event(self.db, event, apply=False, trusted_owner=True, model="fixture")
        self.assertEqual(result["mode"], "shadow")
        run.assert_not_called()

    def test_action_failure_is_recorded_as_blocked(self):
        event = {"kind": "owner_request", "summary": "Click the button"}
        with patch.object(agent, "plan", return_value=proposal()), \
             patch.object(agent, "desktop_action", side_effect=RuntimeError("Screen locked")):
            result = agent.process_event(self.db, event, apply=True, trusted_owner=True, model="fixture")
        self.assertEqual(result["action"], "blocked")
        self.assertIn("Screen locked", result["error"])

    def test_parser_builds_the_expected_primitive(self):
        receipt = Mock(ok=True, detail="completed")
        computer = Mock(click=Mock(return_value=receipt), type_text=Mock(return_value=receipt),
                        press=Mock(return_value=receipt), activate=Mock(return_value=receipt),
                        open_path=Mock(return_value=receipt), menu=Mock(return_value=receipt))
        with patch("talos_core.mac.computer_use.ComputerUse", return_value=computer):
            agent.desktop_action("click 12 34", self.state)
            computer.click.assert_called_once_with(12, 34, count=1, app=None)
            agent.desktop_action("type hello world", self.state)
            computer.type_text.assert_called_once_with("hello world", app=None)
            agent.desktop_action("key cmd+shift+4", self.state)
            computer.press.assert_called_once_with("cmd+shift+4", confirm=False, app=None)
            agent.desktop_action('menu Safari File "New Window"', self.state)
            computer.menu.assert_called_once_with("Safari", "File", "New Window")

    def test_outcome_row_matches_the_reported_action(self):
        event = {"kind": "owner_request", "summary": "Click the button"}
        with patch.object(agent, "plan", return_value=proposal()), \
             patch.object(agent, "desktop_action", return_value="completed"):
            result = agent.process_event(self.db, event, apply=True, trusted_owner=True, model="fixture")
        with closing(self.db.execute("SELECT status FROM action_outcomes WHERE decision_id=?",
                                     (result["id"],))) as rows:
            self.assertEqual(rows.fetchone()[0], "applied")


if __name__ == "__main__":
    unittest.main()

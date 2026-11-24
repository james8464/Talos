"""Managed GUI application lifecycle, ported from OpenCowork."""

import sys
import tempfile
import unittest
from pathlib import Path

from talos_core.mac import gui_apps


class GuiAppManagerTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manager = gui_apps.GuiAppManager(self.state)

    def test_requires_explicit_confirmation(self):
        with self.assertRaises(gui_apps.GuiAppRefused):
            self.manager.start([sys.executable, "-c", "print(1)"])

    def test_start_status_log_and_stop(self):
        result = self.manager.start(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            name="fixture", wait=0.2, confirm=True,
        )
        self.assertTrue(result["running"])
        status = self.manager.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["name"], "fixture")
        log = Path(status["log"])
        self.assertTrue(log.is_file())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        stopped = self.manager.stop()
        self.assertTrue(stopped["stopped"])
        self.assertFalse(self.manager.status()["running"])

    def test_second_start_is_refused_while_running(self):
        self.manager.start([sys.executable, "-c", "import time; time.sleep(30)"],
                           wait=0.1, confirm=True)
        with self.assertRaises(gui_apps.GuiAppError):
            self.manager.start([sys.executable, "-c", "print(1)"],
                               wait=0.1, confirm=True)
        self.manager.stop(force=True)

    def test_immediate_exit_is_reported(self):
        with self.assertRaises(gui_apps.GuiAppError) as caught:
            self.manager.start([sys.executable, "-c", "raise SystemExit(3)"],
                               wait=0.5, confirm=True)
        self.assertIn("code 3", str(caught.exception))

    def test_bad_commands_are_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.start([], confirm=True)
        with self.assertRaises(ValueError):
            self.manager.start("   ", confirm=True)
        with self.assertRaises(ValueError):
            self.manager.start([sys.executable], wait=99, confirm=True)

    def test_stop_without_an_app_is_a_noop(self):
        self.assertIn("no managed", self.manager.stop()["reason"])

    def test_status_survives_a_corrupt_record(self):
        (self.state / "gui-apps.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(self.manager.status()["running"])


if __name__ == "__main__":
    unittest.main()

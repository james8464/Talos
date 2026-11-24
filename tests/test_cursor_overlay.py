import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import cursor


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class CursorOverlayTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"
        self.process = FakeProcess()
        self.overlay = cursor.CursorOverlay(
            self.state, popen=lambda *a, **k: self.process, enabled=True
        )

    def lines(self):
        return [json.loads(line) for line in self.process.stdin.getvalue().splitlines()]

    def test_show_writes_coordinates_and_hide_clears(self):
        with patch.object(self.overlay, "_ensure_binary", return_value=True):
            self.assertTrue(self.overlay.show(120, 240, "TYPING", hide_after=None))
            self.overlay.hide()
        self.assertEqual(self.lines()[0], {"x": 120, "y": 240, "label": "TYPING",
                                          "visible": True, "control": True})
        self.assertEqual(self.lines()[1], {"visible": False, "control": False})

    def test_click_sends_a_ripple_and_control_flag(self):
        with patch.object(self.overlay, "_ensure_binary", return_value=True):
            self.assertTrue(self.overlay.click(30, 40, "click", hide_after=None))
        self.assertEqual(self.lines()[0], {"x": 30, "y": 40, "label": "click",
                                          "visible": True, "control": True, "click": True})

    def test_session_keeps_the_indicator_until_it_ends(self):
        with patch.object(self.overlay, "_ensure_binary", return_value=True):
            self.assertTrue(self.overlay.begin_session("computer use"))
            self.overlay.end_session()
        self.assertEqual(self.lines()[0]["session"], True)
        self.assertEqual(self.lines()[1], {"session": False})

    def test_labels_are_bounded_and_a_failed_helper_never_raises(self):
        with patch.object(self.overlay, "_ensure_binary", return_value=True):
            self.overlay.show(1, 2, "x" * 200, hide_after=None)
        self.assertEqual(len(self.lines()[0]["label"]), 60)

        broken = cursor.CursorOverlay(self.state, popen=lambda *a, **k: (_ for _ in ()).throw(OSError()), enabled=True)
        with patch.object(broken, "_ensure_binary", return_value=True):
            self.assertFalse(broken.show(1, 2))

    def test_disabled_overlay_is_a_no_op(self):
        overlay = cursor.CursorOverlay(self.state, enabled=False)
        self.assertFalse(overlay.show(1, 1))
        overlay.hide()
        overlay.close()


if __name__ == "__main__":
    unittest.main()

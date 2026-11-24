"""Unified GUI actions and coordinate mapping."""

import unittest
from unittest.mock import patch

from talos_core.mac import gui_actions


class Receipt:
    def __init__(self, action):
        self.action = action

    def to_dict(self):
        return {"action": self.action, "ok": True}


class FakeComputer:
    def __init__(self):
        self.calls = []

    def hid_click(self, x, y, **kwargs):
        return self._call("hid_click", x, y, **kwargs)

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return Receipt(name)

    def click(self, x, y, **kwargs):
        return self._call("click", x, y, **kwargs)

    def right_click(self, x, y, **kwargs):
        return self._call("right_click", x, y, **kwargs)

    def move(self, x, y, **kwargs):
        return self._call("move", x, y, **kwargs)

    def type_text(self, text, **kwargs):
        return self._call("type_text", text, **kwargs)

    def press(self, combo, **kwargs):
        return self._call("press", combo, **kwargs)

    def drag(self, x1, y1, x2, y2, **kwargs):
        return self._call("drag", x1, y1, x2, y2, **kwargs)

    def screenshot(self, **kwargs):
        return self._call("screenshot", **kwargs)

    def choose(self, app, goal):
        return {"ref": "7", "center": [110, 220], "label": "Start"}

    def click_ref(self, app, ref):
        return self._call("click_ref", app, ref)

    def type_ref(self, app, ref, text):
        return self._call("type_ref", app, ref, text)

    def hover(self, x, y, **kwargs):
        return self._call("hover", x, y, **kwargs)

    def window_jpeg(self, app):
        return "jpeg-base64"

    def ocr(self, app, query=None):
        self.calls.append(("ocr", app, query))
        return {"matches": []}


class GuiActionTests(unittest.TestCase):
    def setUp(self):
        self.computer = FakeComputer()

    def test_click_variants_and_move(self):
        gui_actions.act(self.computer, "click", x=10, y=20)
        gui_actions.act(self.computer, "double_click", x=10, y=20)
        gui_actions.act(self.computer, "right_click", x=10, y=20)
        gui_actions.act(self.computer, "move", x=10, y=20)
        self.assertEqual([call[0] for call in self.computer.calls],
                         ["hid_click", "hid_click", "hid_click", "move"])
        self.assertEqual(self.computer.calls[1][2]["count"], 2)

    def test_type_key_drag_and_screenshot(self):
        gui_actions.act(self.computer, "type", value="hello", app="TextEdit")
        gui_actions.act(self.computer, "key", value="cmd+s", confirm=True)
        gui_actions.act(self.computer, "drag", value="1,2,3,4", app="Finder")
        gui_actions.act(self.computer, "screenshot")
        names = [call[0] for call in self.computer.calls]
        self.assertEqual(names, ["type_text", "press", "drag", "screenshot"])
        self.assertEqual(self.computer.calls[2][1], (1, 2, 3, 4))

    def test_wait_is_bounded(self):
        with patch("talos_core.mac.gui_actions.time.sleep") as sleep:
            result = gui_actions.act(self.computer, "wait", value=99)
        sleep.assert_called_once_with(gui_actions.MAX_WAIT)
        self.assertEqual(result["seconds"], gui_actions.MAX_WAIT)

    def test_invalid_actions_fail_closed(self):
        with self.assertRaises(ValueError):
            gui_actions.act(self.computer, "explode")
        with self.assertRaises(ValueError):
            gui_actions.act(self.computer, "click", x=1)
        with self.assertRaises(ValueError):
            gui_actions.act(self.computer, "type", value="")
        with self.assertRaises(ValueError):
            gui_actions.act(self.computer, "drag", value="1,2")
        self.assertEqual(self.computer.calls, [])

    def test_goal_clicks_and_types_by_description(self):
        result = gui_actions.target(self.computer, "App", "click", "the start button")
        self.assertEqual(result["ref"], "7")
        self.assertEqual(result["center"], [110, 220])
        gui_actions.target(self.computer, "App", "type", "the name field", value="Talos")
        names = [call[0] for call in self.computer.calls]
        self.assertEqual(names, ["click_ref", "click_ref", "type_ref"])

    def test_goal_without_a_match_does_nothing(self):
        with patch.object(self.computer, "choose", return_value={}):
            result = gui_actions.target(self.computer, "App", "click", "nothing")
        self.assertFalse(result["ok"])
        self.assertEqual([call for call in self.computer.calls if call[0] != "ocr"], [])

    def test_ocr_fallback_clicks_a_custom_drawn_control(self):
        with patch.object(self.computer, "choose", return_value={}), \
             patch.object(self.computer, "ocr", return_value={"matches": [
                 {"text": "Start", "center_screen": [80, 90]}]}):
            result = gui_actions.target(self.computer, "App", "click", "the Start button")
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "ocr")
        self.assertIn(("hid_click", (80, 90), {"button": "left", "count": 1, "confirm": False, "app": None}), self.computer.calls)

    def test_ocr_fallback_types_after_clicking_the_text(self):
        with patch.object(self.computer, "choose", return_value={}), \
             patch.object(self.computer, "ocr", return_value={"matches": [
                 {"text": "Name", "center_screen": [40, 50]}]}):
            result = gui_actions.target(self.computer, "App", "type", "the Name field",
                                        value="Talos")
        self.assertTrue(result["ok"])
        names = [call[0] for call in self.computer.calls]
        self.assertEqual(names, ["hid_click", "type_text"])

    def test_verify_asks_the_local_vision_model(self):
        with patch("talos_core.room.room_vision.describe_image",
                   return_value="yes") as describe:
            answer = gui_actions.verify(self.computer, "App", "Is it ready?")
        self.assertEqual(answer, "yes")
        describe.assert_called_once_with("Is it ready?", "jpeg-base64")

    def test_retina_coordinates_are_halved(self):
        self.assertEqual(
            gui_actions.image_point_to_screen(200, 100, 2880, 1800,
                                              bounds=(0, 0, 1440, 900)),
            (100, 50),
        )
        self.assertEqual(
            gui_actions.image_point_to_screen(0, 0, 2880, 1800,
                                              bounds=(10, 20, 1440, 900)),
            (10, 20),
        )


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from talos_core.mac import computer_use as cu


class FakeRunner:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options))
        if argv[0] == cu.SCREENCAPTURE:
            Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


class ComputerUseTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"
        self.runner = FakeRunner(stdout="Safari")
        self.computer = cu.ComputerUse(self.state, runner=self.runner, cursor=False)

    def receipts(self):
        path = self.state / "computer-use.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_observation_reports_frontmost_app_and_private_screenshot(self):
        self.assertEqual(self.computer.frontmost()["app"], "Safari")
        receipt = self.computer.screenshot()
        self.assertTrue(receipt.ok)
        self.assertEqual(Path(receipt.path).read_bytes(), b"\x89PNG\r\n\x1a\nfixture")
        self.assertEqual(Path(receipt.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipts()[0]["action"], "screenshot")

    def test_type_text_escapes_quotes_and_never_logs_content(self):
        text = 'say "hello" \\n next'
        receipt = self.computer.type_text(text)
        self.assertTrue(receipt.ok)
        argv = self.runner.calls[-1][0]
        self.assertEqual(argv[:2], [cu.OSASCRIPT, "-e"])
        self.assertIn('\\"hello\\"', argv[2])
        self.assertEqual(receipt.metadata["characters"], len(text))
        self.assertNotIn("hello", (self.state / "computer-use.jsonl").read_text())
        self.assertEqual(receipt.metadata["sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_typing_with_an_app_targets_that_pid_without_stealing_focus(self):
        from unittest.mock import patch

        with (
            patch.object(self.computer, "app_pid", return_value=4321),
            patch.object(self.computer, "window_bounds", return_value=(10, 20, 500, 300)),
            patch.object(self.computer, "_post_unicode_to_pid") as post,
            patch.object(self.computer, "_show_cursor") as cursor,
        ):
            receipt = self.computer.type_text("direct text", app="TextEdit")
        post.assert_called_once_with(4321, "direct text")
        cursor.assert_called_once()
        self.assertEqual(receipt.action, "type_text_to_pid")
        self.assertEqual(receipt.metadata["pid"], 4321)
        self.assertNotIn("direct text", (self.state / "computer-use.jsonl").read_text())

    def test_shortcut_can_target_a_background_app(self):
        from unittest.mock import patch

        with (
            patch.object(self.computer, "app_pid", return_value=99),
            patch.object(self.computer, "window_bounds", return_value=(0, 0, 200, 100)),
            patch.object(self.computer, "_post_key_to_pid") as post,
        ):
            receipt = self.computer.press("cmd+n", app="TextEdit")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 99)
        self.assertEqual(receipt.action, "press_keys_to_pid")

    def test_multiline_typing_presses_return_between_lines(self):
        self.computer.type_text("one\ntwo")
        script = self.runner.calls[-1][0][2]
        self.assertEqual(script.count("key code 36"), 1)

    def test_window_bounds_parse_position_and_size(self):
        runner = FakeRunner(stdout="100, 200, 800, 600")
        computer = cu.ComputerUse(self.state, runner=runner, cursor=False)
        self.assertEqual(computer.window_bounds("TextEdit"), (100, 200, 800, 600))
        self.assertIn('process "TextEdit"', runner.calls[-1][0][2])

    def test_destructive_shortcut_requires_explicit_confirmation(self):
        with self.assertRaises(cu.PolicyError):
            self.computer.press("cmd+q")
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(self.computer.press("cmd+q", confirm=True).ok)
        self.assertIn("command down", self.runner.calls[-1][0][2])

    def test_click_double_click_and_menu_construct_bounded_scripts(self):
        self.computer.click(10, 20)
        self.assertIn('click at {10, 20}', self.runner.calls[-1][0][2])
        self.computer.click(30, 40, count=2)
        self.assertIn("repeat 2 times", self.runner.calls[-1][0][2])
        self.computer.menu("Safari", "File", "New Window")
        script = self.runner.calls[-1][0][2]
        self.assertIn('menu item "New Window"', script)
        self.assertIn('menu "File"', script)
        with self.assertRaises(ValueError):
            self.computer.menu("Safari")

    def test_osascript_permission_failure_is_explained(self):
        runner = FakeRunner(returncode=1, stderr="execution error: Not allowed assistive access. (-1719)")
        computer = cu.ComputerUse(self.state, runner=runner, cursor=False)
        result = computer.frontmost()
        self.assertFalse(result["receipt"]["ok"])
        self.assertIn("Accessibility permission", result["receipt"]["detail"])

    def test_clipboard_and_audit_redact_payloads(self):
        receipt = self.computer.set_clipboard("secret-value")
        self.assertTrue(receipt.ok)
        log = (self.state / "computer-use.jsonl").read_text()
        self.assertNotIn("secret-value", log)
        self.assertIn('"characters": 12', log)

    def test_scroll_hover_drag_and_window_management_use_the_kit(self):
        from unittest.mock import patch

        from talos_core.mac import perception

        with patch.object(perception, "run", return_value={"ok": True}) as run, \
             patch.object(self.computer, "_show_cursor"):
            self.assertTrue(self.computer.scroll(5, app="TextEdit", x=10, y=20)["ok"])
            self.assertTrue(self.computer.hover(10, 20, app="TextEdit")["ok"])
            self.assertTrue(self.computer.drag(10, 20, 30, 40, app="TextEdit")["ok"])
            self.assertTrue(self.computer.window("resize", app="TextEdit", width=800, height=600)["ok"])
        commands = [" ".join(call.args) for call in run.call_args_list]
        self.assertTrue(any("input scroll" in command and "--amount 5" in command for command in commands))
        self.assertTrue(any("input hover" in command and "--x 10" in command for command in commands))
        self.assertTrue(any("input drag" in command and "--to-x 30" in command for command in commands))
        self.assertTrue(any("window resize" in command and "--width 800" in command for command in commands))

    def test_app_scroll_without_coordinates_uses_the_window_center(self):
        from unittest.mock import patch

        from talos_core.mac import perception

        with patch.object(perception, "run", return_value={"ok": True}) as run, \
             patch.object(self.computer, "window_bounds", return_value=(100, 200, 300, 400)), \
             patch.object(self.computer, "_show_cursor"):
            self.assertTrue(self.computer.scroll(5, app="TextEdit")["ok"])
        command = " ".join(run.call_args.args)
        self.assertIn("--app TextEdit", command)
        self.assertIn("--x 250", command)
        self.assertIn("--y 400", command)

    def test_closing_a_window_requires_confirmation(self):
        with self.assertRaises(cu.PolicyError):
            self.computer.window("close", app="TextEdit")

    def test_background_accessibility_uses_the_harness_when_present(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        state = {"pid": 77, "window_bounds": {"x": 10, "y": 20, "w": 300, "h": 200},
                 "elements": [{"index": 0}], "text": "[0] AXTextArea"}
        engine = SimpleNamespace(
            get_app_state=Mock(return_value=state),
            click_element=Mock(return_value={"clicked": 0, "via": "AXPress"}),
            set_element_value=Mock(return_value={"set_value": 0, "chars": 5}),
        )
        with patch.object(self.computer, "_harness", return_value=engine), \
             patch.object(self.computer, "_show_cursor"):
            self.assertEqual(self.computer.app_state("TextEdit")["pid"], 77)
            self.computer.click_element("TextEdit", 0)
            self.computer.set_element_value("TextEdit", 0, "hello")
        engine.click_element.assert_called_once_with(77, state["elements"], 0, method="ax")
        engine.set_element_value.assert_called_once_with(77, state["elements"], 0, "hello")
        self.assertNotIn("hello", (self.state / "computer-use.jsonl").read_text())

    def test_missing_control_extra_reports_a_clear_error(self):
        from unittest.mock import patch

        with patch.object(self.computer, "_harness", return_value=None):
            with self.assertRaises(cu.ComputerUseError):
                self.computer.app_state("TextEdit")

    def test_click_goal_selects_then_clicks_a_discovered_control(self):
        from unittest.mock import patch

        with patch.object(self.computer, "choose", return_value={"ref": "abc", "center": [10, 20]}), \
             patch.object(self.computer, "click_ref", return_value={"ok": True}) as click:
            result = self.computer.click_goal("Notes", "new note")
        click.assert_called_once_with("Notes", "abc")
        self.assertTrue(result["click"]["ok"])

    def test_click_goal_falls_back_to_a_coordinate_click(self):
        from unittest.mock import patch

        with patch.object(self.computer, "choose",
                          return_value={"ref": "abc", "center": [10, 20]}), \
             patch.object(self.computer, "click_ref",
                          return_value={"ok": False,
                                        "hint": "The AX action did not change observable state"}), \
             patch.object(self.computer, "activate", return_value=cu.Receipt("activate", True, "", "")), \
             patch.object(self.computer, "click",
                          return_value=cu.Receipt("click", True, "", "")) as click:
            result = self.computer.click_goal("Notes", "new note")
        click.assert_called_once_with(10, 20)
        self.assertIn("fallback", result)

    def test_highlight_rings_without_clicking(self):
        cursor = Mock()
        cursor.highlight.return_value = True
        self.computer.cursor = cursor
        with patch.object(self.computer, "choose",
                          return_value={"ref": "7", "center": [100, 200], "label": "7"}):
            result = self.computer.highlight("Calculator", "7")
        cursor.highlight.assert_called_once_with(72, 172, 56, 56, "7", hide_after=8.0)
        self.assertTrue(result["highlighted"])

    def test_permission_probe_is_safe_on_this_platform(self):
        result = cu.permissions()
        self.assertIn("platform", result)
        self.assertIn("accessibility", result)
        self.assertIn("screen_recording", result)

    def test_observe_act_loop_accepts_only_known_actions(self):
        actions = iter([
            {"action": "activate", "app": "Safari"},
            {"action": "type", "text": "talos"},
            {"action": "done"},
        ])
        loop = cu.ComputerUseLoop(self.computer, max_steps=4)
        result = loop.run("Open Safari and search", lambda goal, view, history: next(actions))
        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"], 3)
        self.assertEqual([item["action"] for item in result["history"]], ["activate", "type"])
        self.assertTrue(Path(self.state / "computer-use.jsonl").is_file())

    def test_loop_refuses_destructive_shortcut_and_step_overflow(self):
        loop = cu.ComputerUseLoop(self.computer, max_steps=2)
        with self.assertRaises(cu.PolicyError):
            loop.run("quit everything", lambda *_: {"action": "key", "combo": "cmd+q"})
        result = loop.run("wait", lambda *_: {"action": "click", "x": 1, "y": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "step limit reached")
        with self.assertRaises(ValueError):
            loop.run("bad", lambda *_: {"action": "delete_disk"})


if __name__ == "__main__":
    unittest.main()

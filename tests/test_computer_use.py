import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        self.computer = cu.ComputerUse(self.state, runner=self.runner)

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

    def test_multiline_typing_presses_return_between_lines(self):
        self.computer.type_text("one\ntwo")
        script = self.runner.calls[-1][0][2]
        self.assertEqual(script.count("key code 36"), 1)

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
        computer = cu.ComputerUse(self.state, runner=runner)
        result = computer.frontmost()
        self.assertFalse(result["receipt"]["ok"])
        self.assertIn("Accessibility permission", result["receipt"]["detail"])

    def test_clipboard_and_audit_redact_payloads(self):
        receipt = self.computer.set_clipboard("secret-value")
        self.assertTrue(receipt.ok)
        log = (self.state / "computer-use.jsonl").read_text()
        self.assertNotIn("secret-value", log)
        self.assertIn('"characters": 12', log)

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

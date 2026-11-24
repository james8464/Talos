import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core import cli


class CliUtilityTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main([*args, "--state", str(self.state)])
        return code, out.getvalue()

    def test_worktree_lists_git_worktrees(self):
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="worktree /tmp\n", stderr="")):
            code, text = self.run_cli("worktree")
        self.assertEqual(code, 0)
        self.assertIn("worktree", text)

    def test_checkpoints_debug_insights_monitoring_and_webapp(self):
        for command in ("checkpoints", "debug", "insights", "monitoring", "webapp"):
            code, text = self.run_cli(command)
            self.assertIn(code, (0, 1), command)
            self.assertTrue(text.strip(), command)

    def test_approvals_bundles_connectors_hooks_and_tour(self):
        for command in ("approvals", "bundles", "connectors", "hooks", "tour"):
            code, text = self.run_cli(command)
            self.assertEqual(code, 0, command)
            self.assertTrue(text.strip(), command)

    def test_focus_pane_targets_the_window(self):
        with patch("talos_core.mac.computer_use.ComputerUse.window",
                   return_value={"ok": True}) as focus:
            code, text = self.run_cli("focus_pane", "TextEdit")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(text)["ok"])
        self.assertEqual(focus.call_args.kwargs["app"], "TextEdit")

    def test_prompt_size_and_profile_are_available(self):
        code, text = self.run_cli("prompt_size")
        self.assertIn(code, (0, 1))
        self.assertTrue(text.strip())
        code, text = self.run_cli("profile")
        self.assertEqual(code, 0)
        self.assertIn("permissions", text)

    def test_config_logs_and_theme_are_inspectable(self):
        for command in ("config", "logs", "theme"):
            code, text = self.run_cli(command)
            self.assertEqual(code, 0)
            self.assertTrue(text.strip())
        code, text = self.run_cli("theme")
        self.assertIn("cyan", json.loads(text)["themes"])


if __name__ == "__main__":
    unittest.main()

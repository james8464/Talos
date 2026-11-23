import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from talos_core import cli


class CliTests(unittest.TestCase):
    def test_version_and_help_are_discoverable(self):
        with self.assertRaises(SystemExit) as exit_code, redirect_stdout(io.StringIO()):
            cli.main(["--version"])
        self.assertEqual(exit_code.exception.code, 0)
        text = cli.build_parser().format_help()
        for command in ("doctor", "decompose", "status", "test", "improve", "computer"):
            self.assertIn(command, text)

    def test_doctor_reports_a_clean_tree(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "talos_core").mkdir()
        (root / "talos_core/__init__.py").write_text("")
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["doctor", "--root", str(root), "--quick", "--no-lint", "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"ok": true', output.getvalue())

    def test_decompose_writes_a_plan_and_renders_leaves(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main([
                "decompose", "Refactor the memory pipeline and migrate storage",
                "--state", str(state), "--file", "talos_core/memory/personal_index.py",
            ])
        self.assertEqual(code, 0)
        self.assertIn("leaves", output.getvalue())
        self.assertEqual(len(list((state / "plans").glob("*.json"))), 1)

    def test_status_surfaces_permissions_and_health(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["status", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("Talos", output.getvalue())
        self.assertIn("Mac access", output.getvalue())


if __name__ == "__main__":
    unittest.main()

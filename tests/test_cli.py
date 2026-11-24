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

    def test_console_renders_brain_without_speech(self):
        database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "console.sqlite"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["console", "--db", str(database), "--script", "/status", "/quit"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertGreaterEqual(len(text), 1000)
        self.assertIn("model: qwen2.5:1.5b", text)

    def test_graph_query_reports_nodes_and_sources(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["graph", "decomposition", "manager", "--limit", "2"])
        self.assertEqual(code, 0)
        payload = output.getvalue()
        self.assertIn("code_graph", payload)
        self.assertIn("decomposition.py", payload)

    def test_status_surfaces_permissions_and_health(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["status", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("Talos", output.getvalue())
        self.assertIn("Mac access", output.getvalue())
        self.assertIn("Audio", output.getvalue())

    def test_acp_command_option_does_not_overwrite_subcommand(self):
        args = cli.build_parser().parse_args(
            ["acp", "--command", "python fixture.py", "--prompt", "hi", "--confirm"]
        )
        self.assertEqual(args.command, "acp")
        self.assertEqual(args.acp_command, "python fixture.py")


if __name__ == "__main__":
    unittest.main()

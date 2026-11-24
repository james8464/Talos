import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from talos_core.self_improvement import health


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_clean_tree_passes_syntax_and_import_checks(self):
        self.write("talos_core/__init__.py", "")
        self.write("talos_core/app.py", "VALUE = 1\n")
        self.write("talos_core/use.py", "from talos_core.app import VALUE\n")
        report = health.health(self.root, self.state, quick=True, lint=False)
        self.assertTrue(report["ok"])
        names = {item["name"]: item for item in report["checks"]}
        self.assertTrue(names["syntax"]["ok"])
        self.assertTrue(names["imports"]["ok"])
        self.assertEqual((self.state / "health.json").stat().st_mode & 0o777, 0o600)

    def test_unresolved_and_broken_files_are_reported_with_locations(self):
        self.write("talos_core/__init__.py", "")
        self.write("talos_core/broken.py", "from talos_core.missing.module import thing\n")
        self.write("talos_core/syntax.py", "def nope(:\n")
        report = health.health(self.root, self.state, quick=True, lint=False)
        self.assertFalse(report["ok"])
        names = {item["name"]: item for item in report["checks"]}
        self.assertFalse(names["imports"]["ok"])
        self.assertTrue(any("missing.module" in item for item in names["imports"]["items"]))
        self.assertFalse(names["syntax"]["ok"])
        self.assertIn("syntax.py", "\n".join(names["syntax"]["items"]))

    def test_tests_and_lint_are_skipped_when_not_requested(self):
        self.write("talos_core/__init__.py", "")
        report = health.health(self.root, self.state, quick=True, lint=False)
        self.assertEqual([item["name"] for item in report["checks"]], ["syntax", "imports"])

    def test_failed_regression_suite_is_surfaced(self):
        self.write("talos_core/__init__.py", "")
        calls = []

        def runner(command, **options):
            calls.append(command)
            self.assertNotIn("TALOS_MUTE", options["env"])
            self.assertEqual(options["env"]["TALOS_CURSOR"], "0")
            return SimpleNamespace(returncode=1, stdout="", stderr="FAILED (failures=2)\n")

        report = health.health(self.root, self.state, quick=False, lint=False,
                               tests=True, runner=runner)
        self.assertFalse(report["ok"])
        tests = next(item for item in report["checks"] if item["name"] == "tests")
        self.assertFalse(tests["ok"])
        self.assertIn("FAILED", tests["detail"])
        self.assertIn("-m", calls[0])

    def test_ruff_fix_runs_before_the_final_scan(self):
        self.write("talos_core/__init__.py", "")
        commands = []

        def runner(command, **options):
            commands.append(command)
            payload = "[]" if "--fix" in command else '[{"filename":"a.py","location":{"row":1},"code":"F401","message":"unused"}]'
            return SimpleNamespace(returncode=0 if "--fix" in command else 1,
                                   stdout=payload, stderr="")

        report = health.health(self.root, self.state, quick=True, lint=True,
                               fix=True, runner=runner)
        self.assertEqual(len(commands), 2)
        self.assertIn("--fix", commands[0])
        self.assertFalse(report["ok"])

    def test_summary_and_failures_are_human_readable(self):
        self.write("talos_core/__init__.py", "")
        report = health.health(self.root, self.state, quick=True, lint=False)
        self.assertIn("healthy", health.summary(self.state))
        self.assertEqual(list(health.failures(report)), [])
        stored = json.loads((self.state / "health.json").read_text())
        self.assertEqual(stored["root"], str(self.root.resolve()))


if __name__ == "__main__":
    unittest.main()

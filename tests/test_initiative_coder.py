import json
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import initiative_coder


class InitiativeCoderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.state.mkdir()
        (self.root / "talos_core").mkdir()
        (self.root / "talos_core/__init__.py").write_text("")
        (self.root / "talos_core/demo.py").write_text("VALUE = 1\n")
        (self.root / "tests").mkdir()
        plan = {
            "root": "p" * 12,
            "leaves": ["l" * 12],
            "nodes": {"l" * 12: {"title": "Implement demo", "status": "leaf",
                                  "files": ["talos_core/demo.py"],
                                  "acceptance": "VALUE is 2"}},
        }
        plan_path = self.state / "plan.json"
        plan_path.write_text(json.dumps(plan))
        (self.state / "feature-requests.jsonl").write_text(json.dumps({
            "status": "proposed", "id": "a" * 16, "origin": "initiative:test",
            "source": "talos_core/demo.py", "feature": "Change VALUE",
            "acceptance": "VALUE is 2", "time": 1, "plan_path": str(plan_path),
        }) + "\n")

    def values(self):
        return {
            "before": "VALUE = 1",
            "after": "VALUE = 2",
            "regression_test": (
                "import unittest\nfrom talos_core.demo import VALUE\n"
                "class DemoTest(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(VALUE, 2)\n"
            ),
        }

    def test_verified_patch_is_applied_and_receipted(self):
        result = initiative_coder.run_once(self.state, self.root,
                                           infer=lambda prompt: json.dumps(self.values()))
        self.assertTrue(result["ok"])
        self.assertEqual((self.root / "talos_core/demo.py").read_text(), "VALUE = 2\n")
        receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
        self.assertTrue(receipt["sandbox_verified"])
        self.assertTrue(receipt["applied"])
        self.assertNotEqual(receipt["before_sha256"], receipt["after_sha256"])
        self.assertIn("tests.test_candidate_regression", receipt["tests_run"])
        outcomes = [json.loads(line) for line in
                    (self.state / "feature-requests.jsonl").read_text().splitlines()]
        self.assertEqual(outcomes[-1]["status"], "done")

    def test_invalid_patch_is_declined_without_touching_source(self):
        result = initiative_coder.run_once(
            self.state, self.root, infer=lambda prompt: json.dumps({**self.values(), "before": "NOPE"}))
        self.assertFalse(result["ok"])
        self.assertEqual((self.root / "talos_core/demo.py").read_text(), "VALUE = 1\n")
        outcomes = [json.loads(line) for line in
                    (self.state / "feature-requests.jsonl").read_text().splitlines()]
        self.assertEqual(outcomes[-1]["status"], "declined")

    def test_no_plan_or_queue_is_a_clean_no_op(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertFalse(initiative_coder.run_once(empty, self.root,
                                                   infer=lambda prompt: "{}")["ok"])


if __name__ == "__main__":
    unittest.main()

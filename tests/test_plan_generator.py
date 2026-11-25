"""Free-form request to a validated coding plan."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import plan_generator


class PlanGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "talos_core").mkdir()
        (self.root / "talos_core" / "__init__.py").write_text("")
        (self.root / "talos_core" / "example.py").write_text("VALUE = 1\n")
        (self.root / "talos_core" / "other.py").write_text("OTHER = 2\n")

    def plan(self, steps):
        return json.dumps({"steps": steps})

    def test_generated_plan_is_normalized_and_ordered(self):
        raw = self.plan([
            {"id": "second", "targets": ["talos_core/other.py"], "request": "Follow up.",
             "needs": ["first"]},
            {"id": "first", "targets": ["talos_core/example.py"], "request": "Start."},
        ])
        result = plan_generator.generate("Change both.", root=self.root,
                                         infer=lambda prompt: raw)
        self.assertEqual([step["id"] for step in result["steps"]], ["second", "first"])
        self.assertEqual(result["steps"][1]["targets"], ["talos_core/example.py"])
        self.assertTrue(all(step["status"] == "queued" for step in result["steps"]))

    def test_single_target_is_normalized(self):
        raw = self.plan([{"id": "a", "target": "talos_core/example.py", "request": "x"}])
        result = plan_generator.generate("x", root=self.root, infer=lambda prompt: raw)
        self.assertEqual(result["steps"][0]["targets"], ["talos_core/example.py"])

    def test_repair_can_depend_on_completed_step(self):
        completed = [{"id": "first", "status": "verified",
                      "targets": ["talos_core/example.py"]}]
        raw = self.plan([{"id": "fixed", "targets": ["talos_core/other.py"],
                          "request": "follow up", "needs": ["first"]}])
        result = plan_generator.repair(
            "Change both.", root=self.root,
            failed={"id": "dead", "targets": ["talos_core/example.py"],
                    "request": "start"},
            completed=completed, error="unit tests failed",
            infer=lambda prompt: raw)
        self.assertEqual(result["steps"][0]["needs"], ["first"])

    def test_cycle_is_retried_with_feedback(self):
        prompts = []
        bad = self.plan([{"id": "a", "targets": ["talos_core/example.py"],
                          "request": "x", "needs": ["b"]},
                         {"id": "b", "targets": ["talos_core/other.py"],
                          "request": "y", "needs": ["a"]}])
        good = self.plan([{"id": "a", "targets": ["talos_core/example.py"],
                           "request": "x"}])

        def infer(prompt):
            prompts.append(prompt)
            return bad if len(prompts) == 1 else good

        result = plan_generator.generate("Do it.", root=self.root, infer=infer)
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(len(prompts), 2)
        self.assertIn("previous plan was rejected", prompts[1])

    def test_unknown_target_and_oversized_plan_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_generator.generate("x", root=self.root, infer=lambda prompt: self.plan([
                {"id": "a", "targets": ["talos_core/missing.py"], "request": "x"}]))
        too_many = [{"id": f"s{i}", "targets": ["talos_core/example.py"],
                     "request": "x"} for i in range(4)]
        with self.assertRaises(ValueError):
            plan_generator.generate("x", root=self.root,
                                    infer=lambda prompt: self.plan(too_many))


if __name__ == "__main__":
    unittest.main()

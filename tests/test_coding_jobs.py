"""Bounded coding jobs: queue, draft, sandbox verification and reporting."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import coding_jobs

EXAMPLE = '''"""Example module."""


def add(a, b):
    return a + b
'''

FAILING_REGRESSION = '''"""A deliberately failing regression."""

import unittest

from talos_core.example import add


class AddTest(unittest.TestCase):
    def test_adds(self):
        self.assertEqual(add(2, 3), 6)
'''

REGRESSION = '''"""Regression for the coding job."""

import unittest

from talos_core.example import add


class AddTest(unittest.TestCase):
    def test_adds(self):
        self.assertEqual(add(2, 3), 5)
'''


class CodingJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "talos_core").mkdir()
        (self.tmp / "talos_core" / "__init__.py").write_text("")
        (self.tmp / "talos_core" / "example.py").write_text(EXAMPLE)
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "__init__.py").write_text("")

    def start(self, **kwargs):
        return coding_jobs.start(self.tmp / "state", "Make add commutative.",
                                 "talos_core/example.py", root=self.tmp, **kwargs)

    def test_start_rejects_unsafe_targets(self):
        with self.assertRaises(ValueError):
            coding_jobs.start(self.tmp / "state", "x", "../outside.py", root=self.tmp)
        with self.assertRaises(ValueError):
            coding_jobs.start(self.tmp / "state", "x", "talos_core/missing.py", root=self.tmp)
        with self.assertRaises(ValueError):
            coding_jobs.start(self.tmp / "state", "", "talos_core/example.py", root=self.tmp)

    def test_invalid_model_reply_is_recorded_and_source_untouched(self):
        job = self.start()
        result = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp,
                                 infer=lambda prompt: "not json")
        self.assertEqual(result["status"], "declined")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)
        report = json.loads((Path(job["folder"]) / "report.json").read_text())
        self.assertEqual(report["status"], "declined")

    def test_sandbox_test_failure_retries_once_with_feedback(self):
        job = self.start(apply=False)
        prompts = []

        def infer(prompt):
            prompts.append(prompt)
            test = FAILING_REGRESSION if len(prompts) == 1 else REGRESSION
            return json.dumps({"before": "    return a + b\n",
                               "after": "    return b + a\n",
                               "regression_test": test})

        result = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp, infer=infer)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn("failed sandbox unit tests", prompts[1])
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)

    def test_retry_cap_stops_after_two_failing_attempts(self):
        job = self.start(apply=False)
        prompts = []

        def infer(prompt):
            prompts.append(prompt)
            return json.dumps({"before": "    return a + b\n",
                               "after": "    return b + a\n",
                               "regression_test": FAILING_REGRESSION})

        result = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp, infer=infer)
        self.assertEqual(result["status"], "declined")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(prompts), 2)

    def test_verified_patch_leaves_source_untouched(self):
        job = self.start(apply=False)
        values = {"before": "    return a + b\n",
                  "after": "    return b + a\n",
                  "regression_test": REGRESSION}
        result = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp,
                                 infer=lambda prompt: json.dumps(values))
        self.assertEqual(result["status"], "verified", result.get("message"))
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)
        self.assertTrue((Path(job["folder"]) / "change.diff").is_file())

    def test_budget_exhaustion_defers_before_drafting(self):
        job = self.start()
        ticks = iter([0.0, 400.0, 400.0])
        result = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp,
                                 infer=lambda prompt: self.fail("model must not run"),
                                 clock=lambda: next(ticks))
        self.assertEqual(result["status"], "deferred")

    def test_run_is_idempotent_for_finished_jobs(self):
        job = self.start()
        first = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp,
                                infer=lambda prompt: "{}")
        second = coding_jobs.run(self.tmp / "state", job["id"], root=self.tmp,
                                 infer=lambda prompt: self.fail("must not rerun"))
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(len(coding_jobs.jobs(self.tmp / "state")), 1)

    def test_run_due_processes_queued_jobs(self):
        self.start()
        self.start()
        results = coding_jobs.run_due(self.tmp / "state", root=self.tmp,
                                      infer=lambda prompt: "{}", limit=1)
        self.assertEqual([item["status"] for item in results], ["declined"])
        self.assertEqual(len(coding_jobs.jobs(self.tmp / "state")), 2)


if __name__ == "__main__":
    unittest.main()


OTHER = '''"""Other module."""


def label():
    return "one"
'''

OTHER_REGRESSION = '''"""Regression for the second step."""

import unittest

from talos_core.other import label


class LabelTest(unittest.TestCase):
    def test_label(self):
        self.assertEqual(label(), "two")
'''


class CodingPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "talos_core").mkdir()
        (self.tmp / "talos_core" / "__init__.py").write_text("")
        (self.tmp / "talos_core" / "example.py").write_text(EXAMPLE)
        (self.tmp / "talos_core" / "other.py").write_text(OTHER)
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "__init__.py").write_text("")
        self.state = self.tmp / "state"

    def infer(self, prompt):
        if "TARGET: talos_core/example.py" in prompt:
            values = {"before": "    return a + b\n", "after": "    return b + a\n",
                      "regression_test": REGRESSION}
        else:
            values = {"before": '    return "one"\n', "after": '    return "two"\n',
                      "regression_test": OTHER_REGRESSION}
        return json.dumps(values)

    def plan(self, steps, **kwargs):
        return coding_jobs.start_plan(self.state, steps, root=self.tmp, **kwargs)

    def test_plan_rejects_cycles_and_unknown_dependencies(self):
        with self.assertRaises(ValueError):
            self.plan([{"id": "a", "target": "talos_core/example.py", "request": "x",
                        "needs": ["b"]},
                       {"id": "b", "target": "talos_core/other.py", "request": "y",
                        "needs": ["a"]}])
        with self.assertRaises(ValueError):
            self.plan([{"id": "a", "target": "talos_core/example.py", "request": "x",
                        "needs": ["missing"]}])

    def test_start_plan_accepts_already_normalized_steps(self):
        clean = coding_jobs.validate_steps(
            [{"id": "a", "target": "talos_core/example.py", "request": "x"}],
            root=self.tmp)
        plan = coding_jobs.start_plan(self.state, clean, root=self.tmp)
        self.assertEqual(plan["steps"][0]["targets"], ["talos_core/example.py"])

    def test_dependent_step_is_blocked_when_dependency_declines(self):
        plan = self.plan([
            {"id": "first", "target": "talos_core/example.py", "request": "first"},
            {"id": "second", "target": "talos_core/other.py", "request": "second",
             "needs": ["first"]},
        ])
        result = coding_jobs.run_plan(self.state, plan["id"], root=self.tmp,
                                      infer=lambda prompt: "{}")
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(steps["first"]["status"], "declined")
        self.assertEqual(steps["second"]["status"], "blocked")
        self.assertIsNone(steps["second"]["job_id"])

    def test_plan_runs_dependency_order_and_verifies_each_step(self):
        plan = self.plan([
            {"id": "second", "target": "talos_core/other.py", "request": "second",
             "needs": ["first"]},
            {"id": "first", "target": "talos_core/example.py", "request": "first"},
        ])
        result = coding_jobs.run_plan(self.state, plan["id"], root=self.tmp, infer=self.infer)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["order"], ["first", "second"])
        steps = {step["id"]: step for step in result["steps"]}
        self.assertTrue(all(steps[key]["status"] == "verified" for key in ("first", "second")))
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)
        self.assertEqual((self.tmp / "talos_core" / "other.py").read_text(), OTHER)

    def test_plan_does_not_rerun_after_terminal(self):
        plan = self.plan([{"id": "only", "target": "talos_core/example.py", "request": "x"}])
        first = coding_jobs.run_plan(self.state, plan["id"], root=self.tmp, infer=self.infer)
        second = coding_jobs.run_plan(self.state, plan["id"], root=self.tmp,
                                      infer=lambda prompt: self.fail("must not rerun"))
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(len(coding_jobs.jobs(self.state)), 1)


MULTI_FAILING = '''"""A deliberately failing combined regression."""

import unittest

from talos_core.example import add
from talos_core.other import label


class CombinedTest(unittest.TestCase):
    def test_both(self):
        self.assertEqual(add(2, 3), 6)
        self.assertEqual(label(), "two")
'''

MULTI_REGRESSION = '''"""Regression for a two-file step."""

import unittest

from talos_core.example import add
from talos_core.other import label


class CombinedTest(unittest.TestCase):
    def test_both_files(self):
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(label(), "two")
'''


class MultiFileStepTests(CodingPlanTests):
    def multi(self):
        return {
            "targets": ["talos_core/example.py", "talos_core/other.py"],
            "request": "Update both files together.",
        }

    def test_multi_file_step_verifies_together(self):
        plan = self.plan([{"id": "both", **self.multi()}])
        patches = [
            {"target": "talos_core/example.py", "before": "    return a + b\n",
             "after": "    return b + a\n"},
            {"target": "talos_core/other.py", "before": '    return "one"\n',
             "after": '    return "two"\n'},
        ]
        result = coding_jobs.run_plan(
            self.state, plan["id"], root=self.tmp,
            infer=lambda prompt: self.fail("single-file model must not run"),
            infer_many=lambda prompt: json.dumps(
                {"patches": patches, "regression_test": MULTI_REGRESSION}))
        step = result["steps"][0]
        self.assertEqual(result["status"], "done")
        self.assertEqual(step["status"], "verified", step.get("message"))
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)
        self.assertEqual((self.tmp / "talos_core" / "other.py").read_text(), OTHER)
        report = json.loads((Path(result["folder"]) / "report.json").read_text())
        self.assertEqual(set(report["steps"][0]["targets"]),
                         {"talos_core/example.py", "talos_core/other.py"})

    def test_multi_file_sandbox_failure_retries(self):
        plan = self.plan([{"id": "both", **self.multi()}])
        patches = [
            {"target": "talos_core/example.py", "before": "    return a + b\n",
             "after": "    return b + a\n"},
            {"target": "talos_core/other.py", "before": '    return "one"\n',
             "after": '    return "two"\n'},
        ]
        prompts = []

        def infer_many(prompt):
            prompts.append(prompt)
            regression = MULTI_FAILING if len(prompts) == 1 else MULTI_REGRESSION
            return json.dumps({"patches": patches, "regression_test": regression})

        result = coding_jobs.run_plan(
            self.state, plan["id"], root=self.tmp,
            infer=lambda prompt: self.fail("single-file model must not run"),
            infer_many=infer_many)
        step = result["steps"][0]
        self.assertEqual(result["status"], "done")
        self.assertEqual(step["status"], "verified")
        self.assertEqual(step["attempts"], 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn("failed sandbox unit tests", prompts[1])

    def test_multi_file_step_rejects_partial_patch(self):
        plan = self.plan([{"id": "both", **self.multi()}])
        partial = [{"target": "talos_core/example.py", "before": "    return a + b\n",
                    "after": "    return b + a\n"}]
        result = coding_jobs.run_plan(
            self.state, plan["id"], root=self.tmp,
            infer=lambda prompt: self.fail("single-file model must not run"),
            infer_many=lambda prompt: json.dumps(
                {"patches": partial, "regression_test": MULTI_REGRESSION}))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"][0]["status"], "declined")
        self.assertIn("every target", result["steps"][0]["message"])
        self.assertEqual((self.tmp / "talos_core" / "other.py").read_text(), OTHER)

    def test_multi_file_targets_are_validated(self):
        with self.assertRaises(ValueError):
            coding_jobs.start_many(self.state, "x", ["talos_core/example.py"],
                                   root=self.tmp)
        with self.assertRaises(ValueError):
            coding_jobs.start_many(
                self.state, "x", ["talos_core/example.py", "talos_core/example.py"],
                root=self.tmp)

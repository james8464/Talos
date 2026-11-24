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
        self.assertEqual((self.tmp / "talos_core" / "example.py").read_text(), EXAMPLE)
        report = json.loads((Path(job["folder"]) / "report.json").read_text())
        self.assertEqual(report["status"], "declined")

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

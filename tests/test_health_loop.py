import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import health, self_diagnosis


class HealthLoopTests(unittest.TestCase):
    def test_failed_check_becomes_an_incident_and_proposal(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = root / "state"
        (root / "talos_core").mkdir()
        (root / "talos_core/__init__.py").write_text("")
        (root / "talos_core/broken.py").write_text("from talos_core.gone import nope\n")
        report = health.health(root, state, quick=True, lint=False)
        self.assertFalse(report["ok"])
        incidents = health.report_failures(report, state)
        self.assertEqual(len(incidents), 1)
        promoted = self_diagnosis.promote_incidents_to_proposals(state, threshold=1)
        self.assertEqual(len(promoted), 1)
        proposal = promoted[0]
        self.assertEqual(proposal["origin"], "self-diagnosis")
        self.assertIn("incident", proposal["acceptance"].lower())
        plan = Path(proposal["plan_path"])
        self.assertTrue(plan.is_file())
        self.assertGreaterEqual(len(__import__("json").loads(plan.read_text())["leaves"]), 1)

    def test_healthy_report_creates_no_incidents(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = root / "state"
        (root / "talos_core").mkdir()
        (root / "talos_core/__init__.py").write_text("")
        report = health.health(root, state, quick=True, lint=False)
        self.assertTrue(report["ok"])
        self.assertEqual(health.report_failures(report, state), [])
        self.assertFalse((state / self_diagnosis.DB).exists())


if __name__ == "__main__":
    unittest.main()

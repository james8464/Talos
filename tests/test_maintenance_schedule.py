import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.cognition.conversation_activity import foreground
from talos_core.self_improvement.maintenance_schedule import MaintenanceSchedule, source_versions, stamp


class MaintenanceScheduleTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.state / "permission.json"
        self.db = self.state / "message.sqlite"
        self.code_file = self.state / "review-target.py"
        self.imports = self.state / "imports"
        self.sources = Mock(side_effect=lambda: source_versions(self.config, [self.db], self.imports, ("OCR",)))
        self.code = Mock(side_effect=lambda: stamp(self.code_file))
        self.code_file.touch()
        self.jobs = []
        self.scheduler = MaintenanceSchedule()

    def run_job(self, kind, **kwargs):
        self.jobs.append((kind, kwargs))
        return {"rows": 1} if kind == "sync" else "done"

    def check(self, now, run=None):
        with patch("talos_core.self_improvement.maintenance_schedule.time.monotonic", return_value=now):
            return self.scheduler.poll(self.state, sources=self.sources, code=self.code, run=run or self.run_job)

    def test_unchanged_sources_reconcile_and_changed_sources_trigger_early_work(self):
        for now in range(0, 900, 60):
            self.check(now)
        self.assertEqual(self.jobs, [("sync", {"changed": [], "files": True}), ("review", {})])
        self.check(900)
        self.assertEqual(self.jobs[-1], ("sync", {"changed": [], "files": True}))
        sample = self.imports / "OCR" / "fixture"
        sample.parent.mkdir(parents=True)
        sample.write_text("synthetic")
        self.check(960)
        self.assertEqual(self.jobs[-1], ("sync", {"changed": ["OCR"], "files": False}))
        sample.unlink()
        sample.parent.rmdir()
        self.check(1020)
        self.assertEqual(self.jobs[-1], ("sync", {"changed": ["OCR"], "files": False}))
        self.db.with_suffix(".sqlite-wal").write_bytes(b"synthetic")
        self.check(1080)
        self.assertEqual(self.jobs[-1], ("sync", {"changed": [], "files": False}))
        self.config.write_text("disabled")
        self.check(1140)
        self.assertEqual(sum(k == "sync" for k, _ in self.jobs), 6)
        self.check(1800)
        self.assertEqual(self.jobs[-1], ("review", {}))
        self.code_file.write_text("# changed")
        self.check(1860)
        self.assertEqual(sum(k == "review" for k, _ in self.jobs), 3)

    def test_activity_skips_metadata_and_interrupted_work_remains_pending(self):
        with foreground(self.state):
            self.assertIsNone(self.check(0))
        self.sources.assert_not_called()
        self.code.assert_not_called()
        os.utime(self.state / "conversation.activity", (0, 0))
        self.assertIsNone(self.check(60, lambda *args, **kwargs: None))
        self.assertIsNone(self.scheduler.sources)
        self.check(120)
        self.assertEqual([k for k, _ in self.jobs], ["sync", "review"])

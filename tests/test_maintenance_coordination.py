"""The worker's polling clock, enabled app and source files are synthetic."""
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.runtime import maintenance


class StopClock(Exception):
    pass


class MaintenanceCoordinationTests(unittest.TestCase):
    def drive(self, update=lambda t, state: None, *, interrupted=False):
        counts = Counter()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def job(state, model, kind, *, changed=(), files=False):
                counts[kind] += 1
                calls.append((clock[0], kind, tuple(changed), files))
                if interrupted and counts[kind] == 1:
                    return None
                return {} if kind == "sync" else "fixture"
            clock = [0]
            def sleep(seconds):
                clock[0] += seconds
                if clock[0] >= 600:
                    raise StopClock
                update(clock[0], state)
            with patch.object(maintenance.Path, "home", return_value=state), \
                 patch.object(maintenance.time, "monotonic", lambda: clock[0]), \
                 patch.object(maintenance.time, "sleep", sleep), \
                 patch.object(maintenance, "QuietWorker", return_value=Mock(request=lambda kind, **kw: job(state, model="fixture", kind=kind, **kw))), self.assertRaises(StopClock):
                maintenance.watch(state, "fixture")
        return counts, calls

    def test_unchanged_sources_and_code_do_not_repeat_heavy_jobs(self):
        counts, calls = self.drive()
        self.assertEqual(counts, {"review": 1, "sync": 1, "health": 1, "reflection": 1, "initiative": 1})
        self.assertTrue(calls[0][-1])

    def test_changed_export_appearing_and_being_deleted_both_refresh(self):
        def update(seconds, state):
            path = state / "knowledge-imports/OCR/a.txt"
            if seconds == 60:
                path.parent.mkdir(parents=True)
                path.write_text("synthetic")
            if seconds == 120:
                path.unlink()
        _, calls = self.drive(update)
        self.assertEqual([t for t, k, changed, _ in calls if k == "sync" and changed == ("OCR",)], [60, 120])

    def test_preemption_preserves_pending_sync_and_review(self):
        counts, calls = self.drive(interrupted=True)
        self.assertEqual(counts, {"sync": 2, "review": 2, "health": 2, "reflection": 2, "initiative": 2})
        self.assertEqual([time for time, kind, _, _ in calls if kind in ("sync", "review")], [0, 5, 5, 10])

    def test_sqlite_wal_and_consent_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = home / "fixture-state"
            state.mkdir()
            (state / "mac-context.json").write_text('["Messages"]')
            wal = home / "Library/Messages/chat.db-wal"
            wal.parent.mkdir(parents=True)
            first = maintenance.enabled_versions(state, home)
            wal.write_bytes(b"synthetic")
            second = maintenance.enabled_versions(state, home)
            self.assertNotEqual(first, second)
            (state / "mac-context.json").write_text('[]')
            self.assertNotEqual(second, maintenance.enabled_versions(state, home))

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from talos_core.runtime import talos_activity
from talos_core.self_improvement import health, source_observer
from talos_core.self_improvement.worker_supervision import worker_lock


class SourceObserverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        (self.root / "talos_core").mkdir()
        (self.root / "tests").mkdir()
        self.sample = self.root / "talos_core/sample.py"
        self.sample.write_text("VALUE = 1\n")
        (self.root / "tests/test_sample.py").write_text(
            "from talos_core import sample\n\ndef test_value():\n    assert sample.VALUE\n"
        )
        self.now = 1000.0
        self.test_code = 0
        self.test_stderr = "OK\n"
        self.calls = []
        self.enterContext(patch.object(
            source_observer.health, "check_ruff",
            side_effect=lambda root, runner=None: health.Check(
                "ruff", True, "0 lint finding(s)", 1),
        ))

    def clock(self):
        return self.now

    def runner(self, command, **kwargs):
        self.calls.append(tuple(command))
        return subprocess.CompletedProcess(command, self.test_code,
                                          stdout="", stderr=self.test_stderr)

    def observe(self, *, busy=None, debounce=0):
        return source_observer.observe_once(
            self.root, self.state, clock=self.clock, runner=self.runner,
            busy=busy or (lambda state: False),
            audit=lambda root, state: {"total": 1, "tested": 1},
            debounce=debounce,
        )

    def latest(self):
        return json.loads((self.state / "source-observer/latest.json").read_text())

    def status(self):
        return json.loads((self.state / "source-observer/status.json").read_text())

    def test_snapshot_and_diff_are_content_free_and_skip_private_state(self):
        (self.state / "private.py").write_text("PRIVATE_STATE_TEXT\n")
        link = self.root / "talos_core/link.py"
        link.symlink_to(self.sample)
        first = source_observer.snapshot(self.root)
        self.assertIn("talos_core/sample.py", first)
        self.assertIn("tests/test_sample.py", first)
        self.assertNotIn("state/private.py", first)
        self.assertNotIn("talos_core/link.py", first)
        self.assertNotIn("PRIVATE_STATE_TEXT", json.dumps(first))
        self.sample.write_text("VALUE = 2\n")
        second = source_observer.snapshot(self.root)
        changes = source_observer.diff(first, second)
        self.assertEqual(changes, {"added": [], "removed": [],
                                   "modified": ["talos_core/sample.py"]})
        self.assertEqual(source_observer.changed_paths(changes),
                         ["talos_core/sample.py"])
        self.assertEqual(
            source_observer.diff({"a.py": "old"}, {"b.py": "new"}),
            {"added": ["b.py"], "removed": ["a.py"], "modified": []},
        )

    def test_first_observation_records_a_baseline_without_running_checks(self):
        self.assertEqual(self.observe(), "baseline")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state / "source-observer/latest.json").exists())
        self.assertIn("talos_core/sample.py", self.status()["fingerprint"])
        self.assertIsNone(self.observe())
        self.assertEqual(self.calls, [])

    def test_targeted_change_runs_relevant_tests_and_records_a_receipt(self):
        self.observe()
        self.sample.write_text("VALUE = 2\nSECRET_SOURCE_TEXT = 'private'\n")
        self.now += 10
        value = self.observe()
        self.assertIsInstance(value, dict)
        self.assertTrue(value["ok"])
        self.assertEqual(value["changes"]["modified"], ["talos_core/sample.py"])
        self.assertEqual(value["change_count"], 1)
        self.assertEqual([item["name"] for item in value["checks"]], ["ruff", "tests"])
        self.assertEqual(value["tests"], {"mode": "targeted",
                                          "files": ["tests/test_sample.py"]})
        self.assertIn("test_sample.py", self.calls[0])
        self.assertNotIn("tests/test_sample.py", self.calls[0])
        receipt = self.latest()
        self.assertEqual(receipt, value)
        self.assertTrue((self.state / f"source-observer/receipts/{value['id']}.json").is_file())
        rendered = json.dumps(receipt, sort_keys=True)
        self.assertNotIn("SECRET_SOURCE_TEXT", rendered)
        self.assertNotIn("VALUE = 2", rendered)
        self.assertEqual(self.status()["receipt"], value["id"])
        self.assertEqual(json.loads((self.state / "source-observer/pending.json").read_text()), {})
        journal = [event[2] for event in talos_activity.events(self.state)]
        self.assertIn("Source observer", journal)

    def test_failed_checks_surface_a_content_free_diagnosis_incident(self):
        self.observe()
        self.sample.write_text("VALUE = 3\nSECRET_SOURCE_TEXT = 'private'\n")
        self.test_code = 1
        self.test_stderr = "FAILED (failures=1)\n"
        value = self.observe()
        self.assertFalse(value["ok"])
        self.assertFalse(self.latest()["ok"])
        self.assertFalse(value["checks"][1]["ok"])
        self.assertIn("FAILED", value["checks"][1]["detail"])
        with closing(sqlite3.connect(self.state / "self-diagnosis.sqlite3")) as db:
            rows = db.execute("SELECT component,private_report FROM incidents").fetchall()
        self.assertTrue(rows)
        self.assertEqual({row[0] for row in rows}, {"Self-check"})
        for _component, report in rows:
            self.assertNotIn("SECRET_SOURCE_TEXT", report)
            self.assertNotIn("VALUE = 3", report)

    def test_unmapped_change_falls_back_to_the_full_suite(self):
        unmapped = self.root / "talos_core/unmapped.py"
        unmapped.write_text("VALUE = 1\n")
        self.observe()
        unmapped.write_text("VALUE = 2\n")
        value = self.observe()
        self.assertTrue(value["ok"])
        self.assertEqual(value["tests"], {"mode": "full", "files": []})
        self.assertTrue(any("test_*.py" in call for call in self.calls))

    def test_busy_foreground_and_debounce_defer_then_run_once(self):
        self.observe()
        self.sample.write_text("VALUE = 4\n")
        self.assertEqual(self.observe(busy=lambda state: True), "deferred")
        self.assertEqual(self.calls, [])
        pending = json.loads((self.state / "source-observer/pending.json").read_text())
        self.assertTrue(pending["revision"])
        self.now += 1
        self.assertEqual(self.observe(debounce=5), "settling")
        self.assertEqual(self.calls, [])
        self.now += 5
        value = self.observe(debounce=5)
        self.assertIsInstance(value, dict)
        self.assertTrue(value["ok"])
        self.assertTrue(self.calls)
        self.assertEqual(self.observe(), None)
        self.assertEqual(len(self.calls), 1)

    def test_changed_test_file_targets_itself(self):
        self.observe()
        (self.root / "tests/test_sample.py").write_text(
            "from talos_core import sample\n\ndef test_value():\n    assert sample.VALUE == 1\n"
        )
        value = self.observe()
        self.assertEqual(value["tests"], {"mode": "targeted",
                                          "files": ["tests/test_sample.py"]})

    def test_worker_lock_matches_the_supervised_watch_name(self):
        argv = [sys.executable, "-m", "talos_core.self_improvement.source_observer",
                "--watch"]
        self.assertEqual(worker_lock(argv), "source-observer-watch")

    def test_summary_is_readable_without_starting_work(self):
        self.assertIn("no baseline", source_observer.summary(self.state)[0])
        self.observe()
        self.assertIn("baseline", source_observer.summary(self.state)[0])
        self.sample.write_text("VALUE = 5\n")
        self.observe()
        text = source_observer.summary(self.state)[0]
        self.assertIn("Source observer", text)
        self.assertIn("passed", text)
        self.assertNotIn("VALUE", text)


if __name__ == "__main__":
    unittest.main()

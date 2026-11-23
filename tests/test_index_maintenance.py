import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import index_maintenance as maintenance
import talos_activity
from conversation_activity import foreground


class IndexMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.state / maintenance.INDEX_NAME
        self.db = self.enterContext(closing(sqlite3.connect(self.path)))
        self.db.execute(
            "CREATE VIRTUAL TABLE personal USING fts5(app UNINDEXED, source UNINDEXED, record UNINDEXED, body)"
        )
        self.db.execute("INSERT INTO personal(personal,rank) VALUES('automerge',0)")
        self.db.execute("INSERT INTO personal(personal,rank) VALUES('pgsz',512)")
        self.db.commit()

    def populate(self, batches):
        for batch in range(batches):
            with self.db:
                self.db.executemany(
                    "INSERT INTO personal VALUES('fixture','private-source',?,?)",
                    [
                        (
                            str(i),
                            f"meeting tomorrow the project confidential private-person-{batch}-{i} "
                            + " ".join(f"ref{batch}_{i}_{n}" for n in range(20)),
                        )
                        for i in range(20)
                    ],
                )

    def status(self):
        return self.db.execute(
            "SELECT active,passes,initial_segments,receipt FROM index_merge_state"
        ).fetchone()

    def test_noop_writes_nothing_and_locked_or_busy_index_defers(self):
        self.populate(2)
        version = self.db.execute("PRAGMA data_version").fetchone()
        self.assertEqual(maintenance.merge_step(self.state)["status"], "idle")
        self.assertEqual(self.db.execute("PRAGMA data_version").fetchone(), version)
        self.assertFalse(
            self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='index_merge_state'"
            ).fetchone()
        )
        self.db.execute("BEGIN IMMEDIATE")
        self.assertEqual(maintenance.merge_step(self.state)["status"], "deferred")
        self.db.rollback()
        with foreground(self.state):
            self.assertEqual(maintenance.merge_step(self.state)["status"], "deferred")
        self.assertFalse((self.state / talos_activity.JOURNAL).exists())

    def test_restart_resume_probe_rollback_and_private_receipt(self):
        self.populate(7)
        initial = maintenance.segment_count(self.db)
        expected = maintenance.probe_snapshot(self.db)
        original_connect = sqlite3.connect
        commands = []

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(
                lambda sql: commands.append(sql) if "VALUES('merge'," in sql else None
            )
            return connection

        with (
            patch.object(maintenance, "MERGE_PAGES", 2),
            patch.object(sqlite3, "connect", side_effect=connect),
        ):
            self.assertEqual(maintenance.merge_step(self.state)["status"], "merging")
            self.assertIn("'merge',-2", commands[-1])
            before = self.status()
            before_segments = maintenance.segment_count(self.db)
            with patch.object(
                maintenance, "probe_snapshot", side_effect=[expected, (expected[0], ())]
            ):
                self.assertEqual(maintenance.merge_step(self.state)["status"], "failed")
            self.assertEqual(self.status(), before)
            self.assertEqual(maintenance.segment_count(self.db), before_segments)
            with patch.object(
                maintenance, "conversation_busy", side_effect=[False, True]
            ):
                self.assertEqual(
                    maintenance.merge_step(self.state)["status"], "deferred"
                )
            self.assertEqual(self.status(), before)
            for _ in range(200):
                result = maintenance.merge_step(self.state)
                if result["status"] == "complete":
                    break
            self.assertEqual(result["status"], "complete")
            self.assertTrue(all("'merge',2" in sql for sql in commands[1:]))
        self.assertEqual(maintenance.segment_count(self.db), 1)
        self.assertEqual(maintenance.probe_snapshot(self.db), expected)
        active, passes, start, receipt = self.status()
        self.assertFalse(active)
        self.assertGreater(passes, 1)
        self.assertEqual(start, initial)
        self.assertIn("4 probes verified", receipt)
        events = talos_activity.events(self.state, 0)
        self.assertTrue(
            any(r[2] == "Index maintenance" and r[3] == receipt for r in events)
        )
        for private in ("private-person", "private-source", "meeting", "confidential"):
            self.assertNotIn(private, str(events))
        self.assertEqual(maintenance.merge_step(self.state)["status"], "idle")

    def test_normal_maintenance_job_returns_verified_result(self):
        self.populate(7)
        result = subprocess.run(
            [sys.executable, "maintenance.py", "--state", str(self.state), "--jobs"],
            cwd=Path(__file__).resolve().parents[1],
            input='{"kind":"index_maintenance"}\n',
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        step = json.loads(result.stdout)["result"]
        self.assertEqual(step["status"], "merging")
        self.assertIn("140 documents unchanged", step["message"])
        self.assertIn("probes verified", self.status()[3])

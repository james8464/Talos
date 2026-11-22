import fcntl
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import mac_context
import talos_activity
from vault_watch import VaultPublisher


class LiveReceiptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.publisher = VaultPublisher(self.state, self.root)
        self.note = self.state / "Talos live.md"
        self.enterContext(patch("talos_activity.probe", return_value="unknown (local connection blocked)"))

    def test_lock_transitions_refresh_the_note_without_file_writes_or_events(self):
        self.publisher.publish_live()
        self.assertIn("Maintenance · stopped", self.note.read_text())
        with (self.state/"maintenance.lock").open("a") as worker:
            self.publisher.publish_live()  # the file exists, but has no owner yet
            fcntl.flock(worker, fcntl.LOCK_EX)
            self.publisher.publish_live()
            self.assertIn("Maintenance · running", self.note.read_text())
            fcntl.flock(worker, fcntl.LOCK_UN)
            self.publisher.publish_live()
            self.assertIn("Maintenance · stopped", self.note.read_text())
        self.assertFalse((self.state / talos_activity.JOURNAL).exists())

    def test_pulse_only_rewrites_after_interval(self):
        with patch("vault_watch.time.monotonic", side_effect=[10, 65, 70]), \
             patch("vault_watch.time.time", side_effect=[10000, 10060]):
            self.publisher.publish_live()
            first = self.note.read_text()
            self.publisher.publish_live()
            self.assertEqual(first,self.note.read_text())
            self.publisher.publish_live()
            self.assertNotEqual(first, self.note.read_text())
            self.assertIn(talos_activity.checked_time(10060), self.note.read_text())

    def test_machine_result_persists_without_journal_and_migration_is_attributed(self):
        with closing(sqlite3.connect(self.state/mac_context.INDEX_NAME)) as db, db:
            db.execute("CREATE TABLE index_merge_state(singleton,checked_at,receipt)")
            db.execute("INSERT INTO index_merge_state VALUES (1,1000,'segments 6 to 3; records 500 unchanged')")
        (self.state/"heartbeat.json").write_text(json.dumps({"checked_at":2000, "baseline":"passed",
            "source_version":"ab"*32,"baseline_verified_at":1500, "trial":9, "stage":"speech", "last_result":"no candidate"}))
        talos_activity.record(self.state,"Migration","ported quiet polls")
        talos_activity.record(self.state,"Respan","Request failed; retrying")
        self.publisher.publish_live()
        for output in (self.note.read_text(), "\n".join(talos_activity.snapshot(self.state,None))):
            self.assertIn("Local index maintenance · "+talos_activity.checked_time(1000), output)
            self.assertIn("Local dialogue regression · passed · source abababababab · "+talos_activity.checked_time(1500),output)
            self.assertIn("Developer-verified migration",output)
            self.assertIn("none in the retained activity log",output)
            self.assertIn("no candidate",output)
        (self.state / talos_activity.JOURNAL).unlink()
        self.publisher.publish_live()
        self.assertIn("segments 6 to 3",self.note.read_text())

    def test_local_model_inference_and_verdict_are_separate_from_cloud_success(self):
        talos_activity.record(self.state, "Model", "local Qwen completed inference in 1s · 20 output tokens")
        talos_activity.record(self.state, "Model", "Verified Hermes/test.py at byte 0 · no supported improvement")
        from feature_queue import review
        with (self.state / "feature-requests.jsonl").open("w") as queue:
            queue.write(json.dumps({"status":"proposed", "id":"abc", "origin":"local-qwen-source-review",
                                    "feature":"Check-in cancellation"}) + "\n")
        review(self.state, "fixture", complete=("abc", "Spoken replay passed"))
        self.publisher.publish_live()
        body = self.note.read_text()
        self.assertIn("Local model inference",body)
        self.assertIn("Local source review",body)
        self.assertIn("Model-proposed change verified", body)
        self.assertIn("Spoken replay passed", body)
        self.assertIn("none in the retained activity log",body)

    def test_quiet_terminal_observes_lock_change_before_probe_backoff(self):
        lock = self.enterContext((self.state / "vault-watch.lock").open("a"))
        class Stop:
            calls = 0
            def wait(self, _):
                self.calls += 1
                if self.calls == 1:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                return self.calls >= 2
        out = io.StringIO()
        with patch("talos_activity.time.monotonic",side_effect=[0,2]):
            talos_activity.display(self.state, output=out, stop=Stop())
        self.assertIn("Obsidian publisher · stopped", out.getvalue())
        self.assertIn("Obsidian publisher · running", out.getvalue())

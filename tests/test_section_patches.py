import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from talos_core.self_improvement.section_patches import draft_next


class SectionPatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / "state"
        self.target = self.root / "talos_core/self_improvement/respan_worker.py"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("def interval():\n    return 1\n")
        self.rel_target = "talos_core/self_improvement/respan_worker.py"
        rel = "src/auto-reply/heartbeat.ts"
        reference = self.state / "openclaw-src" / rel
        reference.parent.mkdir(parents=True)
        reference.write_text("export const interval = 2;")
        sha = hashlib.sha256(reference.read_bytes()).hexdigest()
        target_sha = hashlib.sha256(self.target.read_bytes()).hexdigest()
        key = hashlib.sha256(f"OpenClaw:{rel}:{sha}:0:{self.rel_target}:{target_sha}".encode()).hexdigest()[
            :16
        ]
        self.item = {
            "id": key,
            "status": "proposed",
            "source": self.rel_target,
            "reference": rel,
            "origin": "OpenCode-section-review",
            "upstream": "OpenClaw",
            "source_sha256": sha,
            "target_sha256": target_sha,
            "upstream_quote": "interval = 2",
            "byte_offset": 0,
            "talos_quote": "return 1",
            "feature": "Match quiet interval",
            "acceptance": "Verify two seconds",
        }
        self.queue = self.state / "feature-requests.jsonl"
        self.queue.write_text(json.dumps(self.item) + "\n")
        self.answer = {
            "before": "return 1",
            "after": "return 2",
            "regression_test": "import unittest\nfrom talos_core.self_improvement.respan_worker import interval\nclass IntervalTest(unittest.TestCase):\n"
            "    def test_interval(self):\n        self.assertEqual(interval(),2)\n",
        }
        self.folder = self.state / "section-patches" / key

    def draft(self, request, now=100):
        return draft_next(
            self.root,
            self.state,
            "OpenCode",
            "OpenClaw",
            "free-model",
            request,
            clock=lambda: now,
        )

    def test_produces_applicable_diff_regression_and_receipt_only_once(self):
        call = Mock(return_value=self.answer)
        self.assertIn("ready", self.draft(call))
        self.assertIn("return 1", self.target.read_text())
        self.assertFalse((self.root / "tests").exists())
        receipt = json.loads((self.folder / "receipt.json").read_text())
        diff = (self.folder / "change.diff").read_bytes()
        self.assertEqual(receipt["diff_sha256"], hashlib.sha256(diff).hexdigest())
        self.assertFalse(receipt["regression_executed"])
        self.assertFalse(receipt["applied"])
        self.assertEqual((self.folder / "regression.py").read_text(), self.answer["regression_test"])
        self.assertIn(b"b/tests/test_section_", diff)
        self.assertFalse(self.draft(call, 1000))
        # Replay publication after a crash between rename and database acknowledgement.
        with closing(sqlite3.connect(self.state / "section-reviews.sqlite3")) as db, db:
            db.execute("DELETE FROM section_candidates")
        self.assertIn("ready", self.draft(call, 2000))
        call.assert_called_once()

    def test_changes_in_flight_or_completed_queue_are_not_presented_as_ready(self):
        def changed(*_):
            self.target.write_text("def interval(): return 3")
            return self.answer

        self.assertIn("superseded", self.draft(changed))
        self.assertFalse(self.folder.exists())
        with self.queue.open("a") as queue:
            queue.write(json.dumps({"id": self.item["id"], "status": "done"}) + "\n")
        self.assertIsNone(self.draft(Mock(side_effect=AssertionError), 1000))

    def test_invalid_edit_and_broken_test_defer_then_recover_without_changing_source(
        self,
    ):
        self.assertIn(
            "retry",
            self.draft(Mock(return_value={**self.answer, "before": "return 9"})),
        )
        self.assertFalse(self.folder.exists())
        self.assertIsNone(self.draft(Mock(side_effect=AssertionError), 200))
        self.assertIn(
            "retry",
            self.draft(Mock(return_value={**self.answer, "regression_test": "invalid("}), 701),
        )
        self.assertFalse(self.folder.exists())
        self.assertIn("ready", self.draft(Mock(return_value=self.answer), 2200))
        self.assertIn("return 1", self.target.read_text())

    def test_ready_receipt_is_invalidated_after_source_changes(self):
        self.draft(Mock(return_value=self.answer))
        self.target.write_text("def interval(): return 99")
        self.assertIsNone(self.draft(Mock(side_effect=AssertionError), 1000))
        with closing(sqlite3.connect(self.state / "section-reviews.sqlite3")) as db:
            self.assertEqual(
                db.execute("SELECT status FROM section_candidates").fetchone()[0],
                "superseded",
            )
        self.assertTrue((self.folder / "change.diff").is_file())

    def test_abstention_and_unrelated_queue_entry_never_create_artifacts(self):
        self.assertIn("abstained", self.draft(Mock(return_value=dict.fromkeys(self.answer, ""))))
        self.assertFalse(self.folder.exists())
        self.queue.write_text(json.dumps({**self.item, "id": "e" * 16, "origin": "other-origin"}) + "\n")
        self.assertIsNone(self.draft(Mock(side_effect=AssertionError), 1000))

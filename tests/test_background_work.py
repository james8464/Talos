import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agent_memory import open_memory
from room_service import Room


class SourceAdaptationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "agent.sqlite3"
        self.room = Room(self.path, "fixture", online=False)

    def ask(self, text, **kwargs):
        return self.room.route("POST", "/v1/turn", {"text": text, **kwargs})[1]["reply"]

    def test_idle_or_explicit_jobs_do_not_probe_the_model(self):
        import background_work
        def unavailable():
            raise AssertionError("unnecessary model probe")
        with closing(open_memory(self.path)) as db:
            self.assertFalse(background_work.run_one(db, None, "fixture", ready=unavailable))
        self.ask("Queue this work: Save a note that Atlas ships Friday.")
        self.assertTrue(self.room.orders_ready.is_set())
        with closing(open_memory(self.path)) as db:
            self.assertTrue(background_work.run_one(db, None, "fixture", ready=unavailable))
            self.assertEqual(db.execute("SELECT status FROM background_work").fetchone()[0], "done")

    def test_model_warmup_does_not_delay_a_ready_explicit_order(self):
        import background_work
        self.ask("Queue this work: Put checking the launch document on my list.")
        self.ask("Queue this work: Save a note that Cedar opens Tuesday.")
        with closing(open_memory(self.path)) as db:
            while background_work.run_one(db, None, "fixture", ready=False):
                pass
            statuses = [row[0] for row in db.execute("SELECT status FROM background_work ORDER BY id")]
            self.assertEqual(statuses, ["queued", "done"])
            self.assertEqual(db.execute("SELECT count(*) FROM inbox").fetchone()[0], 1)

    def test_background_order_resumes_once_without_model(self):
        from uuid import uuid4
        import background_work
        text = "Queue this work: Save a note that Atlas launches Tuesday and add a task to review the launch."
        key = str(uuid4())
        self.assertEqual(self.ask(text, request_id=key), self.ask(text, request_id=key))
        with closing(open_memory(self.path)) as db, db:
            db.execute("UPDATE background_work SET status='running',claim='old',available='2000-01-01'")
        with closing(open_memory(self.path)) as db:
            self.assertTrue(background_work.run_one(db, None, "fixture", ready=False))
            self.assertFalse(background_work.run_one(db, None, "fixture", ready=False))
            self.assertEqual(db.execute("SELECT count(*) FROM inbox").fetchone()[0], 2)
        self.assertIn("Receipts", self.ask("show my background jobs"))
        self.assertNotIn("Atlas", self.ask("show my background jobs", source="voice"))

    def test_cancellation_during_planning_prevents_late_commit(self):
        import background_work
        self.ask("Queue this work: Put checking the launch document on my list.")
        def cancel_while_planning(*args, **kwargs):
            self.assertIn("Cancelled", self.ask("Cancel background job #1"))
            return '{"steps":[{"tool":"create_task","clause":1,"detail":"checking the launch document"}]}'
        with closing(open_memory(self.path)) as db:
            self.assertFalse(background_work.run_one(db, cancel_while_planning, "fixture", ready=True))
            self.assertEqual(db.execute("SELECT count(*) FROM inbox").fetchone()[0], 0)
        self.assertIn("cancelled", self.ask("show my background jobs"))

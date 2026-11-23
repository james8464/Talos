import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from talos_core.memory.agent_memory import open_memory
from talos_core.memory.memory_ranking import diverse, tokens
from talos_core.cognition.recurring_reminders import advance, next_due
from talos_core.room.room_service import Room


class SourceAdaptationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "agent.sqlite3"
        self.room = Room(self.path, "fixture", online=False)

    def ask(self, text, **kwargs):
        return self.room.route("POST", "/v1/turn", {"text": text, **kwargs})[1]["reply"]

    def test_request_replays_and_identical_due_schedules_reuse_calendar_results(self):
        one = "Remind me every weekday at 09:00 in Europe/Paris to check notes"
        two = "Remind me every weekday at 09:00 in Europe/Paris to check drafts"
        first = self.ask(one, request_id="one")
        with patch("talos_core.cognition.recurring_reminders.next_due", side_effect=AssertionError("unnecessary calculation")):
            self.assertEqual(self.ask(one, request_id="one"), first)
        self.ask(two, request_id="two")
        with closing(open_memory(self.path)) as db:
            with db:
                db.execute("UPDATE recurring_reminders SET due='2000-01-01'")
            with patch("talos_core.cognition.recurring_reminders.next_due", wraps=next_due) as engine:
                advance(db, datetime.now(UTC))
                self.assertEqual(engine.call_count, 1)
            self.assertEqual(db.execute("SELECT count(DISTINCT receipt) FROM recurring_reminders").fetchone()[0], 2)

    def test_timezone_gap_and_fold(self):
        spec = {"kind": "cron", "expr": "30 2 * * *", "tz": "Europe/Paris"}
        self.assertEqual(next_due(spec, datetime(2026, 3, 28, 12, tzinfo=UTC)), datetime(2026, 3, 30, 0, 30, tzinfo=UTC))
        first = next_due(spec, datetime(2026, 10, 24, 12, tzinfo=UTC))
        self.assertEqual(first, datetime(2026, 10, 25, 0, 30, tzinfo=UTC))
        self.assertEqual(next_due(spec, first), datetime(2026, 10, 26, 1, 30, tzinfo=UTC))

    def test_recurring_brief_restart_catchup_and_privacy(self):
        prompt = "Brief me on Atlas every weekday at 09:00 in Europe/Paris"
        first = self.ask(prompt, request_id="abc")
        self.assertEqual(first, self.ask(prompt, request_id="abc"))
        self.assertIn("private message", self.ask("list my routines", source="voice"))
        self.ask("Save a note that Atlas launches Thursday.")
        with closing(open_memory(self.path)) as db, db:
            db.execute("UPDATE recurring_reminders SET due='2001-01-01'")
        now = datetime.now(UTC)
        for _ in range(2):
            with closing(open_memory(self.path)) as db:
                advance(db, now)
        with closing(open_memory(self.path)) as db:
            notes = db.execute("SELECT text FROM inbox WHERE kind='alert'").fetchall()
            self.assertEqual(len(notes), 1)
            self.assertIn("launches Thursday", notes[0][0])
            self.assertNotIn("Atlas", self.room.speech(db)["text"])
        self.assertIn("Cancelled", self.ask("cancel routine #1"))

    def test_recurring_reminder_retries_do_not_duplicate_receipts(self):
        prompt = "Remind me every Monday at 7:15 am to check the calendar"
        self.assertEqual(self.ask(prompt, request_id="r"), self.ask(prompt, request_id="r"))
        with closing(open_memory(self.path)) as db:
            row = db.execute("SELECT due FROM recurring_reminders").fetchone()
            advance(db, datetime.fromisoformat(row[0]) + timedelta(days=23))
            self.assertEqual(db.execute("SELECT count(*) FROM inbox WHERE kind='reminder'").fetchone()[0], 2)
        self.assertIn("Cancelled", self.ask("cancel recurring reminder #1"))

    def test_memory_diversity_and_cjk(self):
        rows = [{"excerpt": s, "fts_rank": -1.0} for s in ("violet keys", "violet keys", "violet bicycle")]
        self.assertEqual(diverse(rows, 2), [rows[0], rows[2]])
        self.assertIn("京都", tokens("京都駅"))

    def test_long_reviewed_dictation_keeps_voice_scope(self):
        text = "Can you help me think this through? " * 65
        answer = {"disposition":"reply", "tool":"none", "detail":"Let's start with your deadline."}
        with patch("talos_core.room.room_service.plan", return_value=answer) as planner:
            self.assertIn("deadline", self.ask(text, source="voice", reviewed_dictation=True))
            self.assertFalse(planner.call_args.kwargs["trusted_owner"])
            self.assertEqual(planner.call_args.args[1]["summary"], text.strip())
            with self.assertRaises(ValueError):
                self.ask(text, source="voice")
        with closing(open_memory(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 0)

    def test_idle_or_explicit_jobs_do_not_probe_the_model(self):
        from talos_core.runtime import background_work
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
        from talos_core.runtime import background_work
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
        from talos_core.runtime import background_work
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
        from talos_core.runtime import background_work
        self.ask("Queue this work: Put checking the launch document on my list.")
        def cancel_while_planning(*args, **kwargs):
            self.assertIn("Cancelled", self.ask("Cancel background job #1"))
            return '{"steps":[{"tool":"create_task","clause":1,"detail":"checking the launch document"}]}'
        with closing(open_memory(self.path)) as db:
            self.assertFalse(background_work.run_one(db, cancel_while_planning, "fixture", ready=True))
            self.assertEqual(db.execute("SELECT count(*) FROM inbox").fetchone()[0], 0)
        self.assertIn("cancelled", self.ask("show my background jobs"))

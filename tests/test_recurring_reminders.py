import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_memory import open_memory
from recurring_reminders import advance, next_due
from room_service import Room


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
        with patch("recurring_reminders.next_due", side_effect=AssertionError("unnecessary calculation")):
            self.assertEqual(self.ask(one, request_id="one"), first)
        self.ask(two, request_id="two")
        with closing(open_memory(self.path)) as db:
            with db:
                db.execute("UPDATE recurring_reminders SET due='2000-01-01'")
            with patch("recurring_reminders.next_due", wraps=next_due) as engine:
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

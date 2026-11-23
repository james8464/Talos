import json
import os
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import recurring_reminders
from agent_memory import open_memory
from room_service import Room


class RoutineControlsTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "room.db"
        self.room = Room(self.path, "fixture", online=False)
        self.db = self.enterContext(closing(open_memory(self.path)))

    def say(self, text, **options):
        return self.room.route(
            "POST", "/v1/voice/facing", {"text": text, "source": "voice", **options}
        )[1]["reply"]

    def test_spoken_creation_pause_and_future_only_resume_after_restart(self):
        reply = self.say(
            "Hey Talos, remind me every weekday at nine thirty a.m. to water the plants.",
            request_id="one",
        )
        self.assertIn("09:30", reply)
        self.assertNotIn("plants", reply)
        self.assertIn("1 active", self.say("list my routines"))
        self.assertIn(
            "Paused",
            self.say("pause my routine to water the plants.", request_id="two"),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM inbox WHERE acknowledged_at IS NULL"
            ).fetchone()[0],
            0,
        )
        with self.db:
            self.db.execute("UPDATE recurring_reminders SET due='2000-01-01'")
        recurring_reminders.advance(self.db, datetime.now(UTC))
        self.room = Room(self.path, "fixture", online=False)
        self.assertIn("1 paused", self.say("list my routines"))
        self.assertIn("Resumed", self.say("resume routine number one"))
        self.assertIn(
            "Paused", self.say("pause my routine to water the plants", request_id="two")
        )
        row = self.db.execute("SELECT active,due FROM recurring_reminders").fetchone()
        self.assertEqual(row[0], 1)
        self.assertGreater(datetime.fromisoformat(row[1]), datetime.now(UTC))
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM inbox WHERE acknowledged_at IS NULL"
            ).fetchone()[0],
            1,
        )

    def test_ambiguous_subject_preview_and_cancelled_job(self):
        for time in ("09:00", "10:30"):
            self.say(f"Remind me every weekday at {time} to water plants")
        self.assertIn("More than one", self.say("Pause my routine to water plants"))
        self.assertIn("couldn't find", self.say("Pause my routine to water%"))
        self.room.apply = False
        self.assertIn("preview", self.say("Pause routine number 1"))
        self.room.apply = True
        self.assertIn("Paused", self.say("Pause routine number 1"))
        self.assertIn("Cancelled", self.say("Cancel routine number 1"))
        self.assertIn("already cancelled", self.say("Resume routine number 1"))
        self.assertEqual(
            [
                r[0]
                for r in self.db.execute(
                    "SELECT active FROM recurring_reminders ORDER BY id"
                )
            ],
            [0, 1],
        )

    def test_pause_cancels_missed_deliveries_without_touching_other_routines(self):
        self.say("Remind me every day at 9 am to water plants")
        self.say("Remind me every day at 9 am to check mail")
        now = datetime.now(UTC)
        with self.db:
            self.db.execute(
                "UPDATE recurring_reminders SET due=? WHERE id=1",
                ((now - timedelta(days=2)).isoformat(),),
            )
        recurring_reminders.advance(self.db, now)
        self.say("Pause routine number 1")
        self.assertEqual(
            [
                r[0]
                for r in self.db.execute(
                    "SELECT text FROM inbox WHERE acknowledged_at IS NULL"
                )
            ],
            ["check mail"],
        )
        with patch.object(
            recurring_reminders, "next_due", side_effect=ValueError("Invalid calendar")
        ):
            with self.assertRaisesRegex(ValueError, "Invalid calendar"):
                self.say("Resume routine number 1")
        self.assertEqual(
            self.db.execute(
                "SELECT active FROM recurring_reminders WHERE id=1"
            ).fetchone()[0],
            -1,
        )

    def test_room_migrates_existing_routines_and_request_reuse(self):
        self.say("Remind me every day at 08:00 to have breakfast")
        with self.db:
            self.db.execute("DROP TABLE routine_deliveries")
        self.assertIn("Paused", self.say("Pause routine 1", request_id="legacy"))
        with self.assertRaisesRegex(ValueError, "already used"):
            self.say("Resume routine 1", request_id="legacy")
        self.assertIn("Resumed", self.say("Resume routine 1"))
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM inbox WHERE acknowledged_at IS NULL"
            ).fetchone()[0],
            1,
        )

    @unittest.skipUnless(
        os.getenv("TALOS_TEST_SPOKEN_CHECKIN") == "1",
        "requires installed Whisper and Kokoro",
    )
    def test_recognized_creation_and_pause(self):
        root = Path(__file__).resolve().parents[1]
        replay = (
            "import json;from telegram_voice import render_note,transcribe_note;"
            "print(json.dumps([transcribe_note(render_note(x)) for x in "
            "('Remind me every day at nine to water the plants.', 'Pause routine number one.')]))"
        )
        call = subprocess.run(
            [str(root / "state/telegram-voice-env/bin/python"), "-c", replay],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        request, pause = json.loads(call.stdout)
        self.assertIn("Scheduled routine", self.say(request), request)
        self.assertIn("Paused", self.say(pause), pause)

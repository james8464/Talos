import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from room_service import Room


class GoalFlow(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.folder = self.root / "Atlas"
        self.folder.mkdir()
        self.db = self.root / "trial.sqlite"
        self.room = Room(self.db, "local", online=False)
        self.deadline = datetime.now(UTC) + timedelta(days=3)
        self.body = {
            "id": str(uuid4()),
            "outcome": "Prepare Atlas review",
            "topic": "Atlas review",
            "required_doc": "forecast",
            "event_id": "fixture event",
            "folder": str(self.folder),
            "deadline": self.deadline.isoformat(),
            "preference": "risk",
            "lead": 15,
        }

    def post(self, path, **body):
        return self.room.route("POST", "/v1/" + path, body)[1]

    def inbox(self):
        return self.room.route("GET", "/v1/inbox", {})[1]["items"]

    def test_evidence_scope_restart_move_and_quiet(self):
        other = self.root / "Atlas North"
        other.mkdir()
        unrelated = other / "forecast.md"
        unrelated.write_text("North forecast: 999")
        (self.folder / "forecast.txt").symlink_to(unrelated)
        (self.folder / "forecast-old.txt").write_text("Superseded: 100")
        current = self.folder / "forecast-corrected.md"
        current.write_text("Approved forecast: 120")
        self.post("turn", text="Save a note that Atlas needs approval.")
        goal = self.post("goals/save", **self.body)
        self.assertEqual(self.post("goals/save", **self.body)["reminder_id"], goal["reminder_id"])
        evidence = goal["observation"]["evidence"]["files"]
        self.assertEqual(
            [(e["path"], e["excerpt"]) for e in evidence], [(current.name, "Approved forecast: 120")]
        )
        self.assertTrue(evidence[0]["updated"])
        self.assertTrue(any("approval" in i["text"] for i in goal["observation"]["items"]))
        self.room = Room(self.db, "local", online=False)
        moved = self.deadline + timedelta(hours=2)
        event = {
            "id": goal["id"],
            "event_id": "fixture event",
            "state": "confirmed",
            "revision": 1,
            "deadline": moved.isoformat(),
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.post("goals/calendar", **event), range(2)))
        (reminder,) = [i for i in self.inbox() if i["kind"] == "reminder"]
        self.assertEqual(reminder["id"], goal["reminder_id"])
        self.assertEqual(datetime.fromisoformat(reminder["due_at"]), moved - timedelta(minutes=15))
        self.assertEqual(len([i for i in self.inbox() if i["kind"] == "alert"]), 1)
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.post("goals/calendar", **{**event, "deadline": self.deadline.isoformat()})
        current.unlink()
        for _ in range(3):
            checked = self.post("goals/check", id=goal["id"])
        self.assertEqual(checked["observation"]["evidence"]["state"], "missing")
        alerts = [i for i in self.inbox() if i["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("missing", alerts[0]["text"])
        why = self.post("turn", text="Why are you tracking this?")["reply"]
        self.assertIn("Prepare Atlas review", why)
        self.post(
            "goals/save",
            **{
                **self.body,
                "deadline": moved.isoformat(),
                "revision": checked["revision"],
                "preference": "quiet",
            },
        )
        self.assertFalse(any(i["kind"] == "alert" for i in self.inbox()))
        for _ in range(2):
            self.post("goals/close", id=goal["id"], status="cancelled")
        self.assertFalse(any(i["kind"] == "reminder" for i in self.inbox()))

    def test_suspended_reminder_can_return_and_delivered_stays_delivered(self):
        goal = self.post("goals/save", **self.body)
        event = {"id": goal["id"], "event_id": "fixture event", "deadline": goal["deadline"]}
        for state in ["missing", "confirmed"]:
            goal = self.post("goals/calendar", **event, state=state, revision=goal["revision"])
            self.assertEqual(any(i["kind"] == "reminder" for i in self.inbox()), state == "confirmed")
        with patch("room_service.datetime", wraps=datetime) as clock:
            clock.now.return_value = self.deadline
            self.post("speech/ack", id=goal["reminder_id"])
        for state in ["missing", "confirmed"]:
            goal = self.post("goals/calendar", **event, state=state, revision=goal["revision"])
        self.assertFalse(any(i["kind"] == "reminder" for i in self.inbox()))

    def test_pause_resume_and_material_change(self):
        current = self.folder / "forecast.md"
        current.write_text("Approved total 12")
        goal = self.post("goals/save", **{**self.body, "preference": "changes"})
        for _ in range(2):
            self.post("goals/pause", id=goal["id"])
        self.assertEqual(self.inbox(), [])
        current.write_text("Approved total 13")
        self.room = Room(self.db, "local")
        self.post("goals/resume", id=goal["id"])
        self.post("goals/check", id=goal["id"])
        self.assertEqual([i["id"] for i in self.inbox() if i["kind"] == "reminder"], [goal["reminder_id"]])
        alerts = [i for i in self.inbox() if i["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("evidence changed", alerts[0]["text"])

    def test_ambiguous_evidence_and_shadow(self):
        for suffix in ["west", "east"]:
            (self.folder / f"forecast-{suffix}.csv").write_text(suffix)
        goal = self.post("goals/save", **self.body)
        self.assertIn("multiple", goal["observation"]["evidence"]["state"])
        self.room = Room(self.db, "local", apply=False)
        self.assertEqual(self.room.route("POST", "/v1/goals/close", {"id": goal["id"]})[0], 403)

    def test_followup_cannot_start_a_new_action(self):
        response = self.post(
            "turn", source="voice", follow_up=True, text="Remind me in ten minutes to check the budget."
        )
        self.assertIn("Hey Talos", response["reply"])
        self.assertEqual(self.inbox(), [])
        self.post("turn", source="voice", text="Remind me to stretch.")
        self.post("turn", source="voice", follow_up=True, text="In three minutes.")
        self.assertEqual(len(self.inbox()), 1)

    def test_specific_project_notes_do_not_enter_broader_goal(self):
        self.post("goals/save", **{**self.body, "id": str(uuid4()), "topic": "Atlas North", "lead": 0})
        self.post("turn", text="Save a note that Atlas North has an estimate of 900.")
        self.post("turn", text="Save a note that Atlas has an estimate of 120.")
        goal = self.post("goals/save", **self.body)
        notes = [i["text"] for i in goal["observation"]["items"] if i["kind"] == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("120", notes[0])

    def test_voice_correction_updates_the_recent_receipt_only(self):
        first = self.post("turn", source="voice", text="Remind me in three minutes to stretch.")
        second = self.post("turn", source="voice", text="Actually, make that five minutes.")
        self.assertEqual(second["tool"], "amend_reminder")
        (receipt,) = self.inbox()
        self.assertEqual(receipt["id"], first["id"])
        self.assertAlmostEqual(
            (datetime.fromisoformat(receipt["due_at"]) - datetime.now(UTC)).total_seconds(), 300, delta=2
        )
        self.room = Room(self.db, "local")
        self.assertIn("need a recent", self.post("turn", source="voice", text="I meant six minutes.")["reply"])

    def test_heartbeat_revisits_stale_goal_and_skips_recent_refresh(self):
        from contextlib import closing
        from agent_memory import open_memory
        from overnight_goal import revisit_goals
        state = self.root / "state"
        state.mkdir()
        self.db = state / "agent.sqlite3"
        self.room = Room(self.db, "local", online=False)
        goal = self.post("goals/save", **self.body)
        (self.folder / "forecast-final.md").write_text("Approved forecast 42")
        with closing(open_memory(self.db)) as db, db:
            db.execute("UPDATE goals SET checked='' WHERE id=?", (goal["id"],))
        now = datetime.now(UTC).timestamp()
        self.assertEqual(revisit_goals(state, now), 1)
        self.assertEqual(revisit_goals(state, now), 0)
        with closing(open_memory(self.db)) as db:
            self.assertIn("Approved forecast 42", db.execute("SELECT observation FROM goals").fetchone()[0])

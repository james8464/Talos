import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agent_memory import open_memory
from local_actions import acknowledge
from room_service import Room
from room_work import propose_work
from transcript_memory import remember


class RoomWorkTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "isolated.sqlite"
        self.room = Room(self.path, "local", online=False)
        self.order = {"text": "Save a note that the Atlas cap is 12000. "
                              "Create a task to send the Atlas forecast and remind me in fifteen minutes.",
                      "source": "voice", "request_id": str(uuid4())}

    def post(self, path, **body):
        return self.room.route("POST", "/v1/" + path, body)[1]

    def test_work_survives_restart_and_duplicate_submissions(self):
        def submit(_):
            room = Room(self.path, "local", online=False)
            return room.route("POST", "/v1/turn", self.order)[1]["steps"]
        with ThreadPoolExecutor(max_workers=3) as pool:
            first, second, third = pool.map(submit, range(3))
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual([r["tool"] for r in first], ["save_note", "create_task", "set_reminder"])
        self.assertEqual(first[-1]["minutes"], 15)
        self.assertEqual(first[-1]["detail"], first[-2]["detail"])
        self.assertEqual(len(self.room.route("GET", "/v1/inbox", {})[1]["items"]), 3)
        with self.assertRaises(ValueError):
            self.post("turn", **{**self.order, "text": self.order["text"].replace("12000", "500")})
        with closing(open_memory(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 0)
            self.assertNotIn("Atlas", db.execute("SELECT event FROM decisions LIMIT 1").fetchone()[0])
            acknowledge(db, first[1]["id"])
            remember(db, "synthetic", [{"role": "user", "content": "I promised to check the Atlas estimate."}])
        brief = self.post("meeting/brief", topic="Weekly Atlas review")
        self.assertEqual({i["kind"] for i in brief["items"]}, {"note", "reminder"})
        self.assertIn("12000", brief["reply"])
        self.assertIn("I promised to check", brief["excerpts"][0]["text"])
        self.assertNotIn("12000", self.post("turn", source="voice", text="Brief me on Atlas.")["reply"])

    def test_departure_needs_an_explicit_delay(self):
        brief = self.post("meeting/brief", topic="Atlas", departure_reminder=True, source="voice")
        self.assertIn("How many minutes", brief["question"])
        self.assertEqual(self.room.route("GET", "/v1/inbox", {})[1]["items"], [])
        receipt = self.post("turn", text="In 20 minutes.", source="voice")
        self.assertEqual(receipt["action"], "applied")
        self.assertIn("20 minutes", receipt["reply"])
        self.assertIn("Atlas", receipt["reply"])

    def test_failure_rolls_back_every_step(self):
        with closing(open_memory(self.path)) as db, db:
            db.execute("""CREATE TRIGGER reject_reminder BEFORE INSERT ON inbox WHEN NEW.kind='reminder'
                          BEGIN SELECT RAISE(ABORT,'simulated fault'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.post("turn", **self.order)
        with closing(open_memory(self.path)) as db:
            for table in ("decisions", "action_outcomes", "room_batches", "inbox"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_quoted_instruction_shadow_and_incomplete_request(self):
        single = self.post("turn", text='Save a note that the example is "pack and remind me in one minute".')
        self.assertEqual(single["tool"], "save_note")
        result = self.post("turn", text="Create a task to pack and remind me in zero hours.")
        self.assertIn("clarify", result["reply"])
        self.room = Room(self.path, "local", apply=False)
        self.assertEqual(self.post("turn", **self.order)["action"], "shadow")
        self.assertEqual(len(self.room.route("GET", "/v1/inbox", {})[1]["items"]), 1)

    def test_preview_apply_uses_reviewed_content_after_restart(self):
        request = "Email Alex the budget and save a note that the Atlas cap is 12000."
        raw = json.dumps({"steps": [{"clause": 1, "tool": "create_task", "detail": "Email Alex"},
                                    {"clause": 2, "tool": "save_note", "detail": "120000"}]})
        with patch("room_service.model_text", return_value=raw):
            draft = self.post("work/preview", text=request)
        self.assertIn("Email Alex", draft["remaining"])
        self.assertEqual(draft["steps"][0]["detail"], "the Atlas cap is 12000")
        self.assertEqual(self.room.route("GET", "/v1/inbox", {})[1]["items"], [])
        self.room = Room(self.path, "local")
        original = self.post("work/apply", id=draft["id"])
        self.assertEqual(self.post("work/apply", id=draft["id"]), original)
        self.assertEqual(len(self.room.route("GET", "/v1/inbox", {})[1]["items"]), 1)

    def test_malformed_plan_and_v4_index_backfill(self):
        with self.assertRaises(RuntimeError):
            propose_work(lambda *a, **k: '{"steps":[{"clause":0,"tool":"shell","detail":"bad"}]}',
                         "local", "Draft this.")
        self.post("turn", text="Create a task to check the Atlas estimate.")
        with closing(open_memory(self.path)) as db, db:
            db.execute("DROP TABLE inbox_search")
            db.execute("PRAGMA user_version = 4")
        self.assertEqual(len(self.post("meeting/brief", topic="Atlas")["items"]), 1)

import base64
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from agent_memory import open_memory
import commitment_memory as memory
from idle_lab import IdleLab, experiment
from room_service import Room
from room_vision import visual_question
from test_room_acceptance import RoomAcceptanceTests as _Acceptance


class RefinementTests(unittest.TestCase):
    request = _Acceptance.request
    setUp = _Acceptance.setUp

    def source(self, filename, kind, content, **opts):
        path = self.path.parent / filename
        path.write_text(content)
        return self.request(
            "/v1/context/select",
            dict(
                path=str(path),
                kind=kind,
                owner="James",
                topic="Atlas",
                person="Maya",
                **opts,
            ),
        )["id"]

    def test_family_promise_reminder_with_receipt(self):
        self.source("sent.eml", "email", "From: James\nTo: Maya\nDate: Wed, 18 Nov 2026 10:00:00 +0000\n\nI'll send the Atlas forecast.")
        turn = "/v1/turn"
        self.request(turn, {"text": "Remember that my sister is Maya."})
        body = {"text": "Find what I promised my sister and remind me tomorrow.",
                "request_id": "88888888-4444-4444-8444-123456789abc"}
        self.assertNotIn("forecast", self.request(turn, {**body, "source": "voice"})["reply"])
        preview = self.request(turn, {"text":"What did I promise my sister about Atlas?"})
        self.assertIn("forecast", preview["reply"])
        self.assertTrue(preview["sources"])
        self.assertEqual(self.request("/v1/inbox")["items"], [])
        saved = self.request(turn, body)
        self.assertIn("Reminder saved", saved["reply"])
        self.assertIn("forecast", saved["reply"])
        self.assertEqual(self.request(turn, body)["receipts"], saved["receipts"])
        self.assertEqual(len(self.request("/v1/inbox")["items"]), 1)
        self.source("second.eml", "email", "From: James\nTo: Maya\nDate: Wed, 18 Nov 2026 12:00:00 +0000\n\nI'll bring lunch.")
        self.assertIn("several", self.request(turn, body)["reply"])
        self.assertEqual(len(self.request("/v1/inbox")["items"]), 1)

        item, = self.request("/v1/inbox")["items"]
        self.room = Room(self.path, "offline", online=False)
        ack = {"id": item["id"]}
        self.request("/v1/speech/ack", ack, expected_error=400)
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 86400, delta=3700)
        with patch("room_service.datetime", wraps=datetime) as clock:
            clock.now.return_value = due + timedelta(seconds=1)
            self.request("/v1/presence", {"occupied": False})
            self.assertIsNone(self.request("/v1/speech")["item"])
            self.request("/v1/presence", {"occupied": True})
            spoken = self.request("/v1/speech")["item"]
            self.assertEqual(spoken["id"], item["id"])
            self.assertNotIn("forecast", spoken["text"])
            for _ in range(2):
                self.assertEqual(self.request("/v1/speech/ack", ack)["acknowledged"], item["id"])
            self.assertIsNone(self.request("/v1/speech")["item"])

    def test_commitment_index_reports_remaining_work(self):
        for name in ("a", "b", "c"):
            self.source(name + ".md", "document", "fixture")
        with closing(open_memory(self.path)) as db, db:
            db.execute("UPDATE context_sources SET checked=0")
            db.commit()
            memory.refresh_one(db, time.time())
        result = self.request("/v1/turn", {"text":"Are my commitment sources up to date?"})
        self.assertEqual(result["coverage"], {"current":1, "due":0, "pending":2, "unavailable":0})
        with closing(open_memory(self.path)) as db, db:
            db.execute("UPDATE context_sources SET error='source unavailable' WHERE checked>0")
        self.assertEqual(self.request("/v1/turn", {"text":"Show commitment index status"})["coverage"]["unavailable"], 1)

    def test_commitments_refresh_correct_resolve_and_forget(self):
        mail = "From: James\nTo: Maya\nDate: Wed, 18 Nov 2026 10:00:00 +0000\nMessage-ID: <demo@fixture>\n\nI'll send the Atlas forecast Thursday.\n> I'll send irrelevant material."
        self.source("sent.eml", "email", mail)
        self.source(
            "agenda.ics",
            "calendar",
            "BEGIN:VEVENT\nSUMMARY:Atlas review\nDTSTART:20261120T100000Z\nEND:VEVENT",
        )
        self.source("Atlas-final.md", "document", "Draft prepared.")
        update = self.source(
            "chat.txt", "whatsapp", "[19/11/2026, 09:00] Maya: Friday is fine"
        )

        def brief():
            return self.request("/v1/context/brief")

        self.assertIn("Current deadline: Friday", brief()["reply"])
        self.assertIn("No delivery found", brief()["reply"])
        self.assertNotIn(
            "forecast",
            self.request(
                "/v1/turn", {"text": "What do I owe people?", "source": "voice"}
            )["reply"],
        )
        self.source(
            "messages.csv",
            "imessage",
            "date,sender,text\n2026-11-18T14:23:00+00:00,James,I will send the Atlas outline Wednesday.",
        )
        (q,) = brief()["questions"]
        chosen = next(o for o in q["options"] if "outline" in o["text"])
        self.request(
            "/v1/context/resolve", {"revision": q["revision"], "promise": chosen["id"]}
        )
        self.room = Room(self.path, "offline")
        self.assertEqual(brief()["questions"], [])
        self.assertIn(
            "forecast Thursday. Current deadline: Thursday.", brief()["reply"]
        )
        self.assertIn("outline Wednesday. Current deadline: Friday.", brief()["reply"])
        self.request("/v1/context/forget", {"id": update})
        self.assertNotIn("Friday", json.dumps(brief()))
        (self.path.parent / "sent.eml").unlink()
        with closing(open_memory(self.path)) as db:
            while memory.refresh_one(db, time.time() + 400):
                pass
        self.assertNotIn("forecast", brief()["reply"])

    def test_selected_messages_database_and_mailbox_filter(self):
        path = self.path.parent / "chat.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE chat(chat_identifier TEXT);
                CREATE TABLE message(date INTEGER,is_from_me INTEGER,text TEXT,guid TEXT);
                CREATE TABLE chat_message_join(chat_id INTEGER,message_id INTEGER);
                INSERT INTO chat VALUES('test@example.invalid'),('another@example.invalid');
                INSERT INTO message VALUES(800000000000000000,1,'I will send a sample Monday','abc'),
                                          (800000000000000000,1,'I will send unrelated data Tuesday','xyz');
                INSERT INTO chat_message_join VALUES(1,1),(2,2);
            """)
            db.executescript("""
                ALTER TABLE message ADD COLUMN date_retracted INTEGER DEFAULT 0;
                ALTER TABLE message ADD COLUMN is_system_message INTEGER DEFAULT 0;
                INSERT INTO message VALUES(900000000000000000,1,'I will send withdrawn material','bad',1,0);
                INSERT INTO chat_message_join VALUES(1,3);
            """)
            for i in range(501):
                msg = db.execute("INSERT INTO message VALUES(900000000000000001,0,'ok',?,0,0)", (str(i),))
                db.execute("INSERT INTO chat_message_join VALUES(1,?)", (msg.lastrowid,))
        self.request(
            "/v1/context/select",
            dict(
                kind="imessage",
                path=str(path),
                person="Maya",
                topic="Atlas",
                owner="James",
                selector="test@example.invalid",
                speak=True,
            ),
        )

        def brief():
            return self.request("/v1/context/brief")["reply"]

        self.assertIn("sample", brief())
        self.assertNotIn("unrelated", brief())
        self.assertNotIn("withdrawn", brief())
        self.assertIn(
            "sample",
            self.request("/v1/turn", {"text": "What do I owe?", "source": "voice"})[
                "reply"
            ],
        )
        message = "From james@example.invalid Wed Nov 18 10:00:00 2026\nFrom: James <james@example.invalid>\nTo: Maya <maya@example.invalid>\nDate: Wed, 18 Nov 2026 10:00:00 +0000\nSubject: Atlas\n\nI'll deliver the Atlas draft Monday.\n"
        self.source(
            "mail.mbox",
            "email",
            message
            + message.replace("Subject: Atlas", "Subject: Elsewhere").replace(
                "Atlas draft", "unrelated mbox item"
            ),
        )
        self.assertIn("Atlas draft", brief())
        self.assertNotIn("unrelated", brief())

    def test_project_folder_delivery_evidence_and_deadline_filter(self):
        project = self.path.parent / "project"
        project.mkdir()
        (project / "draft.md").write_text("a draft")
        self.request(
            "/v1/context/select",
            dict(
                kind="document",
                path=str(project),
                owner="James",
                person="Maya",
                topic="Atlas",
            ),
        )
        self.source(
            "updates.csv",
            "imessage",
            "date,sender,text\n2026-11-18T14:23:00+00:00,James,I will send the Atlas forecast on 2099-01-01.\n2026-11-19T10:00:00+00:00,James,I sent the Atlas forecast today.",
        )
        reply = self.request("/v1/context/brief")
        self.assertIn("draft.md", reply["reply"])
        self.assertIn("Possible delivery", reply["reply"])
        self.assertTrue(any(s["url"].endswith("draft.md") for s in reply["sources"]))
        self.assertNotIn(
            "forecast",
            self.request("/v1/turn", {"text": "What do I owe people before tomorrow?"})[
                "reply"
            ],
        )
        with closing(open_memory(self.path)) as db:
            memory.select(
                db, project, "document", "James", "Atlas", "Maya", retain=False
            )
        self.assertNotIn("draft.md", self.request("/v1/context/brief")["reply"])

    def test_interrupting_with_new_reminder_cannot_arm_a_later_action(self):
        self.room.model_state = lambda: "warming"
        response = self.request(
            "/v1/turn",
            {
                "text": "Remind me to check the mailbox.",
                "source": "voice",
                "follow_up": True,
            },
        )
        self.assertIn("Hey Talos", response["reply"])
        self.request(
            "/v1/turn",
            {"text": "In five minutes.", "source": "voice", "follow_up": True},
            expected_error=503,
        )
        self.assertEqual(self.request("/v1/inbox")["items"], [])

    def test_saved_fact_corrected_across_restarts(self):
        def turn(text, **opts):
            return self.request("/v1/turn", dict(text=text, **opts))["reply"]

        self.assertIn(
            "green", turn("Remember that my bike key is in the green drawer.")
        )
        self.room = Room(self.path, "fixture")
        self.assertIn("green", turn("Where is my bike key?"))
        self.assertIn("blue", turn("Actually, my bike key is in the blue tin."))
        self.assertIn("blue", turn("Where is my bike key?"))
        self.assertIn(
            "Turn on",
            turn("Remember that my bike key is somewhere else.", source="voice"),
        )
        self.assertIn("removed", turn("Forget my bike key."))
        with closing(open_memory(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM personal_facts").fetchone()[0], 0
            )

    def test_frame_handshake_and_no_image_fallback(self):
        for route in ("/v1/voice/facing", "/v1/turn"):
            question = {
                "text": "Hey Talos, what am I wearing?",
                "source": "voice",
                "camera_available": True,
            }
            self.assertEqual(self.request(route, question), {"request_frame": True})
            self.assertIn(
                "fresh camera",
                self.request(route, {**question, "camera_jpeg": ""})["reply"],
            )
            self.request(route, {**question, "camera_jpeg": "junk"}, expected_error=400)
        for people, phrase in ((0, "clearly enough"), (2, "more than one person")):
            result = self.request(
                "/v1/turn",
                {
                    "text": "What am I wearing?",
                    "camera_jpeg": "/9j//9k=",
                    "frame_people": people,
                },
            )
            self.assertIn(phrase, result["reply"])
        self.assertFalse(visual_question("Search the web for what color is the sky"))
        with patch("room_vision.check_ollama_model", side_effect=RuntimeError):
            result = self.request(
                "/v1/turn", {"text": "Describe my outfit.", "camera_jpeg": "/9j//9k="}
            )
            self.assertIn("couldn't inspect", result["reply"])

    def test_current_people_count_uses_only_the_requested_frame(self):
        with patch("room_vision.check_ollama_model", side_effect=AssertionError("unexpected inference")):
            for route in ("/v1/turn", "/v1/voice/facing"):
                body = {"text": "Hey Talos, how many people can you see?", "camera_available": True}
                self.assertEqual(self.request(route, body), {"request_frame": True})
                for count, phrase in ((0, "detected no people"), (1, "detected one person"), (3, "detected 3 people")):
                    reply = self.request(route, {**body, "camera_jpeg": "/9j//9k=", "frame_people": count})
                    self.assertIn(phrase, reply["reply"])
                reply = self.request(route, {**body, "camera_jpeg": "/9j//9k=", "occupied": True})
                self.assertIn("no person count", reply["reply"])
        for text in ("How many people can you see in my holiday photo?",
                     "How many people are in the room tomorrow?"):
            self.assertFalse(visual_question(text))

    def test_person_specific_visual_questions_reject_ambiguous_frames(self):
        with patch("room_vision.check_ollama_model", side_effect=AssertionError("unexpected inference")):
            for question in ("What's in my hand?", "What am I showing?", "What color is my jacket?"):
                for count, phrase in ((0, "clearly enough"), (2, "more than one person")):
                    reply = self.request("/v1/turn", {"text": question, "camera_jpeg": "/9j//9k=",
                                                      "frame_people": count})
                    self.assertIn(phrase, reply["reply"])

    @unittest.skipUnless(
        os.environ.get("TALOS_TEST_VISION") == "1",
        "requires installed vision model and generated portrait fixture",
    )
    def test_generated_clothing_via_http(self):
        for garment in ("shirt", "sweater"):
            file = Path(__file__).parent / "fixtures" / f"synthetic-{garment}.jpg"
            frame = base64.b64encode(file.read_bytes()).decode()
            start = time.monotonic()
            result = self.request(
                "/v1/turn",
                {
                    "source": "voice",
                    "text": "What am I wearing? Describe the garment and its color.",
                    "camera_jpeg": frame,
                },
            )
            print(
                f"Synthetic {garment}: {result['reply']} ({time.monotonic() - start:.2f}s)",
                flush=True,
            )
            self.assertIn(garment, result["reply"].lower())
            self.assertNotIn(
                frame[:200], self.path.read_bytes().decode(errors="ignore")
            )

    def test_performance_turn_reports_all_eligible_stages(self):
        with closing(open_memory(self.path)) as db, db:
            db.executemany("INSERT INTO room_slow_stages VALUES(?,?,?)",
                           [("voice decision", 4, 2400), ("speech synthesis", 2, 2100)])
        report = self.request("/v1/turn", {"text":"How fast are you?", "source":"voice"})
        self.assertEqual(report["pending_stages"], ["voice decision", "speech synthesis"])
        self.assertEqual(report["voice_latency"]["samples"], 0)
        with closing(open_memory(self.path)) as db, db:
            db.execute("INSERT INTO coding_trials(stage,occurrences,started,status) VALUES(?,4,?,'started')",
                       ("voice decision", time.time()))
        self.assertEqual(self.request("/v1/turn", {"text":"Show performance"})["pending_stages"], ["speech synthesis"])

    def test_idle_lab_requires_idle_power_and_repeated_failure(self):
        with closing(open_memory(self.path)) as db, db:
            db.execute("INSERT INTO room_slow_stages VALUES('voice decision',2,3000)")
        lab = IdleLab(self.path, "fixture")
        with (
            patch("idle_lab.power_and_idle", return_value=False),
            patch("idle_lab.subprocess.Popen") as launch,
        ):
            lab.next_check = 0
            lab.tick()
            launch.assert_not_called()
        with (
            patch("idle_lab.power_and_idle", return_value=True),
            patch("idle_lab.subprocess.Popen") as launch,
        ):
            lab.next_check = 0
            lab.tick()
            launch.assert_called_once()
            lab.job.poll.return_value = None
            with patch("idle_lab.os.killpg") as kill:
                lab.pause()
                kill.assert_called_once()
        with closing(open_memory(self.path)) as db:
            trial = db.execute("SELECT id FROM coding_trials").fetchone()[0]
        with (
            patch("idle_lab.check_ollama_model"),
            patch(
                "idle_lab.request_ollama_json",
                return_value={"response": "invalid patch"},
            ),
        ):
            experiment(self.path, trial, "fixture")
        self.assertFalse((self.path.parent / "proposals").exists())

    def test_generated_proposal_can_be_checked_without_changing_source(self):
        root = self.path.parent / "source"
        root.mkdir()
        source = root / "room_dialogue.py"
        source.write_text("answer = 1 + 1\n")
        with closing(open_memory(self.path)) as db, db:
            trial = db.execute(
                "INSERT INTO coding_trials(stage,occurrences,started,status) VALUES('voice decision',2,0,'started')"
            ).lastrowid
        response = {"response": json.dumps({"before": "1 + 1", "after": "2"})}
        with (
            patch("idle_lab.ROOT", root.resolve()),
            patch("idle_lab.check_ollama_model"),
            patch("idle_lab.request_ollama_json", return_value=response),
        ):
            experiment(self.path, trial, "fixture")
        self.assertIn(
            "+answer = 2",
            (self.path.parent / "proposals" / f"trial-{trial}.patch").read_text(),
        )
        self.assertEqual(source.read_text(), "answer = 1 + 1\n")


del _Acceptance

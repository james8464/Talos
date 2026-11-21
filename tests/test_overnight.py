import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import mac_context
import overnight_goal
from agent_memory import open_memory
from room_service import Room


class PrivateRoomTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.library = self.root / "Library"
        self.state = self.root / "state"
        self.room = Room(
            self.state / "room.db", "offline", model_state=lambda: "warming"
        )
        self.enterContext(patch("mac_context.Path.home", return_value=self.root))

    def fixture(self, relative, sql):
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript(sql)
        return path

    def ask(self, text, **options):
        return self.room.route("POST", "/v1/turn", {"text": text, **options})[1]

    def test_cross_app_receipts_deletion_and_source_controls(self):
        self.fixture(
            "Application Support/AddressBook/AddressBook-v22.abcddb",
            """
            CREATE TABLE ZABCDRECORD(Z_PK,ZFIRSTNAME,ZLASTNAME,ZORGANIZATION,ZNICKNAME,ZCONTAINERWHERECONTACTISME);
            INSERT INTO ZABCDRECORD VALUES(1,'Ada','Smith',NULL,NULL,NULL),(2,'Fixture','Owner',NULL,NULL,3);
            CREATE TABLE ZABCDEMAILADDRESS(ZOWNER,ZADDRESS);
            INSERT INTO ZABCDEMAILADDRESS VALUES(1,'ada@example.test');
            CREATE TABLE ZABCDPHONENUMBER(ZOWNER,ZFULLNUMBER);
            INSERT INTO ZABCDPHONENUMBER VALUES(1,'+1 (415) 555-0136');
            CREATE TABLE ZABCDRELATEDNAME(Z_PK,ZOWNER,ZLABEL,ZNAME);
            INSERT INTO ZABCDRELATEDNAME VALUES(1,1,'parent','Unrelated person'),(2,2,'sister','Ada');
        """,
        )
        messages = self.fixture(
            "Messages/chat.db",
            """
            CREATE TABLE handle(id);
            INSERT INTO handle VALUES('+14155550136'),('ada@example.test.invalid');
            CREATE TABLE message(guid,handle_id,is_from_me,date,text,is_system_message,date_retracted);
            INSERT INTO message VALUES('first',1,0,600000000,'Cobalt bicycle',0,0),
                ('removed',1,0,600000001,'Retracted',0,1),('different',2,0,600000002,'Unrelated',0,0);
        """,
        )
        self.fixture(
            "Mail/V10/MailData/Envelope Index",
            """
            CREATE TABLE messages(sender,subject,summary,date_received,date_sent,deleted);
            CREATE TABLE addresses(comment,address);
            CREATE TABLE subjects(subject);
            CREATE TABLE summaries(summary);
            INSERT INTO addresses VALUES('Fixture Sender','fixture@example.test');
            INSERT INTO subjects VALUES('Cobalt inventory');
            INSERT INTO summaries VALUES('Saturday');
            INSERT INTO messages VALUES(1,1,1,88,88,0);
        """,
        )
        self.fixture(
            "Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
            """
            CREATE TABLE ZWAMESSAGE(Z_PK,ZISFROMME,ZCHATSESSION,ZTEXT,ZMESSAGEDATE,ZMESSAGETYPE);
            CREATE TABLE ZWACHATSESSION(Z_PK,ZPARTNERNAME,ZCONTACTJID);
            INSERT INTO ZWACHATSESSION VALUES(42,'Fixture Peer','14155550136@s.whatsapp.net');
            INSERT INTO ZWAMESSAGE VALUES(7,1,42,'Cobalt basket',77,0);
        """,
        )
        self.assertIn("enabled", self.ask("Search my apps for cobalt")["reply"])
        mac_context.configure(self.state, mac_context.APPS)
        with patch(
            "room_service.plan",
            side_effect=AssertionError("private search called planner"),
        ):
            result = self.ask("Search my apps for cobalt")
            self.assertEqual(result["matches"], 3)
            self.assertEqual(set(result["checked"]), set(mac_context.APPS))
            for evidence in ("Messages [first]", "Mail [1]", "WhatsApp [7]"):
                self.assertIn(evidence, result["reply"])
            result = self.ask("Search my messages for Smith")
            self.assertEqual(result["matches"], 1)
            self.assertIn("Cobalt bicycle", result["reply"])
            self.assertIn("basket", self.ask("Search my WhatsApp for Smith")["reply"])
            self.assertNotIn(
                "Unrelated person", self.ask("Show my family relationships")["reply"]
            )
            self.assertIn(
                "sister: Ada", self.ask("Show my family relationships")["reply"]
            )
            with closing(sqlite3.connect(messages)) as db, db:
                db.execute("DELETE FROM message WHERE guid='first'")
            self.assertEqual(self.ask("Search my messages for Smith")["matches"], 0)
            for query in ("Search my mail for", "Search my apps for " + "a" * 175):
                self.assertTrue(self.ask(query)["private"])
            with patch(
                "mac_context.lookup",
                side_effect=AssertionError("voice opened private source"),
            ):
                self.assertIn(
                    "private message",
                    self.ask("Show my family relationships", source="voice")["reply"],
                )
            with closing(open_memory(self.room.db_path)) as db:
                self.assertNotIn("Cobalt", "\n".join(db.iterdump()))
            mac_context.configure(self.state, [])
            self.assertIn("enabled", self.ask("Search my mail for cobalt")["reply"])

    def test_scoped_recall_during_model_warmup(self):
        first = "Can you hear me?"
        self.ask(first, source="voice")
        self.assertIn(first, self.ask("What did I just say?", source="voice")["reply"])
        self.assertNotIn(first, self.ask("What did I just say?")["reply"])

        self.room.route("POST", "/v1/presence", {"occupied": False})
        self.assertNotIn(
            first, self.ask("What did I just say?", source="voice")["reply"]
        )

        def facing(text, **kwargs):
            return self.room.route(
                "POST", "/v1/voice/facing", {"text": text, **kwargs}
            )[1]

        turn = facing(first)
        self.assertIn(
            first, facing("Repeat my last question", context=turn["context"])["reply"]
        )
        self.assertNotIn(
            first, facing("Repeat my last question", context=turn["context"])["reply"]
        )


class HandoffTests(unittest.TestCase):
    def test_quota_requires_recent_nonexpired_codex_window(self):
        now = 1800000000
        event = {
            "timestamp": datetime.fromtimestamp(now, UTC).isoformat(),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "limit_id": "codex",
                    "primary": {"used_percent": 98, "resets_at": now + 600},
                    "secondary": {"used_percent": 100, "resets_at": now - 1},
                },
            },
        }
        self.assertEqual(
            overnight_goal.quota_from_event(event, now), (now, [(2, now + 600)])
        )
        self.assertIsNone(overnight_goal.quota_from_event(event, now + 901))
        event["payload"]["rate_limits"]["limit_id"] = "other"
        self.assertIsNone(overnight_goal.quota_from_event(event, now))

    def test_handoff_threshold_reset_deduplication_and_patch(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        state, source = root / "state", root / "source"
        source.mkdir()
        target = source / "room_dialogue.py"
        target.write_text("result = 1 + 1\n")
        now, end = 1800000000, 1800010000

        def check(delta, windows):
            return overnight_goal.check(
                state, end, now + delta, quota=lambda _: windows
            )

        idle = self.enterContext(
            patch("overnight_goal.power_and_idle", return_value=False)
        )
        with (
            patch("idle_lab.ROOT", source),
            patch(
                "overnight_goal.free_edit",
                return_value={"before": "1 + 1", "after": "2"},
            ) as review,
        ):
            self.assertEqual(check(0, [(3, now + 5000)]), "Codex budget available")
            review.assert_not_called()
            self.assertIn("idle Mac", check(1, [(2, now + 5000)]))
            review.assert_not_called()
            idle.return_value = True
            check(2, [(2, now + 5000)])
            review.assert_called_once_with("conversation latency", target.read_text())
            self.assertIn(
                "+result = 2", next((state / "proposals").glob("*.patch")).read_text()
            )
            self.assertEqual(target.read_text(), "result = 1 + 1\n")
            self.assertEqual(check(300, []), "free reviewer on standby")
            self.assertEqual(check(600, [(40, now + 5000)]), "Codex budget available")
            self.assertEqual(check(901, []), "quota unavailable")
            self.assertEqual(check(10000, []), "ended")
            self.assertEqual(review.call_count, 1)

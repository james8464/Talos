import json
import os
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

    def test_messages_search_honors_each_available_visibility_flag(self):
        path = self.fixture("Messages/chat.db", """
            CREATE TABLE handle(id);
            INSERT INTO handle VALUES('example@test.invalid');
            CREATE TABLE message(guid,handle_id,is_from_me,date,text);
            INSERT INTO message VALUES('a',1,0,700000000,'Fixture first'),('b',1,0,700000001,'Fixture second');
        """)
        mac_context.configure(self.state, ["Messages"])
        def count():
            result = self.ask("Search my messages for Fixture")
            self.assertEqual(result["unavailable"], [])
            return result["matches"]
        self.assertEqual(count(), 2)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("ALTER TABLE message ADD COLUMN date_retracted INTEGER")
            db.execute("UPDATE message SET date_retracted=700000002 WHERE guid='a'")
        self.assertEqual(count(), 1)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("ALTER TABLE message ADD COLUMN is_system_message INTEGER DEFAULT 0")
            db.execute("UPDATE message SET is_system_message=1 WHERE guid='b'")
        self.assertEqual(count(), 0)

    def test_relationship_message_lookup_requires_unambiguous_contacts_evidence(self):
        book = self.fixture("Application Support/AddressBook/AddressBook-v22.abcddb", """
            CREATE TABLE ZABCDRECORD(Z_PK,ZFIRSTNAME,ZLASTNAME,ZORGANIZATION,ZNICKNAME,ZCONTAINERWHERECONTACTISME);
            INSERT INTO ZABCDRECORD VALUES(1,'Dev','Example',NULL,NULL,NULL),(2,'Me','Example',NULL,NULL,4),
                                         (3,'Other','Person',NULL,NULL,NULL);
            CREATE TABLE ZABCDRELATEDNAME(Z_PK,ZOWNER,ZLABEL,ZNAME);
            INSERT INTO ZABCDRELATEDNAME VALUES(7,2,'_$!<Sister>!$_','Dev Example'),(8,3,'sister','Other Person');
            CREATE TABLE ZABCDEMAILADDRESS(ZOWNER,ZADDRESS);
            INSERT INTO ZABCDEMAILADDRESS VALUES(1,'dev@example.test');
            CREATE TABLE ZABCDPHONENUMBER(ZOWNER,ZFULLNUMBER);
        """)
        self.fixture("Messages/chat.db", """
            CREATE TABLE handle(id);
            INSERT INTO handle VALUES('dev@example.test'),('other@example.test');
            CREATE TABLE message(guid,handle_id,is_from_me,date,text,is_system_message,date_retracted);
            INSERT INTO message VALUES('wanted',1,0,600000000,'Fixture pickup',0,0),
                                      ('other',2,0,600000001,'Ask my sister',0,0),
                                      ('sent',1,1,600000002,'Outbound fixture',0,0);
        """)
        mac_context.configure(self.state, ["Contacts", "Messages"])
        query = "Search my messages for my sister"
        hit = self.ask(query)
        self.assertEqual(hit["matches"], 2)
        self.assertIn("Messages [sent]", hit["reply"])
        incoming = self.ask("Show me messages from my sister.")
        self.assertEqual(incoming["matches"], 1)
        self.assertIn("Messages [wanted]", incoming["reply"])
        self.assertNotIn("Messages [sent]", incoming["reply"])
        self.assertEqual(self.ask("Show messages with my sister.")["matches"], 2)
        self.assertIn("Messages [wanted]", hit["reply"])
        self.assertIn("Dev Example", hit["reply"])
        self.assertIn("AddressBook-v22.abcddb · relationship 7", hit["reply"])
        self.assertEqual(hit["checked"], ["Contacts", "Messages"])
        self.assertNotIn("Messages [other]", hit["reply"])
        with closing(sqlite3.connect(book)) as db, db:
            db.execute("INSERT INTO ZABCDEMAILADDRESS VALUES(3,'DEV@example.test')")
        self.assertEqual(self.ask(query)["matches"], 0)
        with closing(sqlite3.connect(book)) as db, db:
            db.execute("DELETE FROM ZABCDEMAILADDRESS WHERE ZOWNER=3")
            db.execute("INSERT INTO ZABCDRECORD VALUES(4,'Dev','Example',NULL,NULL,NULL)")
        self.assertEqual(self.ask(query)["matches"], 0)
        with closing(sqlite3.connect(book)) as db, db:
            db.execute("DELETE FROM ZABCDRECORD WHERE Z_PK=4")
            db.execute("INSERT INTO ZABCDEMAILADDRESS VALUES(3,'other@example.test')")
            db.execute("UPDATE ZABCDRELATEDNAME SET ZNAME='Other Person' WHERE Z_PK=7")
        corrected = self.ask(query)
        self.assertEqual(corrected["matches"], 1)
        self.assertIn("Messages [other]", corrected["reply"])
        self.assertNotIn("Messages [wanted]", corrected["reply"])
        with closing(sqlite3.connect(book)) as db, db:
            db.execute("DELETE FROM ZABCDRELATEDNAME WHERE Z_PK=7")
        self.assertEqual(self.ask(query)["matches"], 0)
        mac_context.configure(self.state, ["Messages"])
        with patch("mac_context.read", side_effect=AssertionError("Contacts disabled")):
            self.assertEqual(self.ask(query)["matches"], 0)

        with patch("mac_context.family_message_lookup", side_effect=AssertionError("voice read private records")):
            self.assertIn("private message", self.ask(query, source="voice")["reply"])
        with closing(open_memory(self.room.db_path)) as db:
            self.assertNotIn("Fixture pickup", "\n".join(db.iterdump()))

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

    def test_log_reader_keeps_latest_fresh_observation_per_window(self):
        now = 1800000000
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("overnight_goal.Path.home", return_value=home))
        day = home / ".codex/sessions/2027/01/15"
        day.mkdir(parents=True)
        def record(delta, **windows):
            return json.dumps({
                "type": "event_msg", "timestamp": datetime.fromtimestamp(now + delta, UTC).isoformat(),
                "payload": {"type": "token_count", "rate_limits": {"limit_id": "codex", **windows}},
            })
        low = {"used_percent": 98, "resets_at": now + 600}
        healthy = {"used_percent": 40, "resets_at": now + 5000}
        (day / "first.jsonl").write_text(record(-5, primary=low) + "\n")
        other = day / "second.jsonl"
        other.write_text(record(-1, secondary=healthy) + "\n" +
                         record(0, primary=["invalid"]).replace(": ", ":") + "\n")
        # Use synthetic log modification times as well as synthetic event times.
        for path in day.glob("*.jsonl"):
            os.utime(path, (now, now))
        self.assertEqual(overnight_goal.latest_quota(now), [(2, now + 600), (60, now + 5000)])
        with other.open("a") as stream:
            stream.write(record(0, primary={**low, "used_percent": 80}) + "\n")
        os.utime(other, (now, now))
        self.assertEqual(overnight_goal.latest_quota(now), [(20, now + 600), (60, now + 5000)])
        self.assertEqual(overnight_goal.latest_quota(now + 901), [])

    def test_partial_read_keeps_previously_low_window_until_reset(self):
        now = 1800000000
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        def check(delta, windows):
            return overnight_goal.check(state, now + 9000, now + delta, quota=lambda _: windows)
        with patch("overnight_goal.power_and_idle", return_value=False):
            self.assertIn("idle Mac", check(0, {"primary": (1, now + 600), "secondary": (2, now + 5000)}))
            self.assertIn("idle Mac", check(1, {"secondary": (50, now + 5000)}))
            self.assertEqual(check(601, []), "quota unavailable")

    def test_old_handoff_survives_partial_read_and_clears_on_complete_read(self):
        now = 1800000000
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        status = state / "overnight-status.json"
        status.write_text(json.dumps({"handoff_until": now + 400, "last_review": now}))
        healthy = (70, now + 1000)
        for windows, outcome in (({"primary": healthy}, "free reviewer on standby"),
                                 ({"primary": healthy, "secondary": healthy}, "Codex budget available")):
            self.assertEqual(overnight_goal.check(state, now + 9000, now, quota=lambda _: windows), outcome)

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

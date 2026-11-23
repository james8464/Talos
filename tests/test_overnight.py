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

    def test_live_family_reminder_excludes_groups_and_incoming_promises(self):
        self.fixture("Application Support/AddressBook/AddressBook-v22.abcddb", """
            CREATE TABLE ZABCDRECORD(Z_PK,ZFIRSTNAME,ZLASTNAME,ZORGANIZATION,ZNICKNAME,ZCONTAINERWHERECONTACTISME);
            INSERT INTO ZABCDRECORD VALUES(1,'Ada','Smith',NULL,NULL,NULL),(2,'Me',NULL,NULL,NULL,1);
            CREATE TABLE ZABCDEMAILADDRESS(ZOWNER,ZADDRESS);
            INSERT INTO ZABCDEMAILADDRESS VALUES(1,'ada@example.test');
            CREATE TABLE ZABCDPHONENUMBER(ZOWNER,ZFULLNUMBER);
            CREATE TABLE ZABCDRELATEDNAME(Z_PK,ZOWNER,ZLABEL,ZNAME);
            INSERT INTO ZABCDRELATEDNAME VALUES(1,2,'sister','Ada Smith');
        """)
        self.fixture("Messages/chat.db", """
            CREATE TABLE handle(id);
            INSERT INTO handle VALUES('ada@example.test'),('other@example.test');
            CREATE TABLE chat_handle_join(chat_id,handle_id);
            INSERT INTO chat_handle_join VALUES(1,1),(2,1),(2,2);
            CREATE TABLE chat_message_join(chat_id,message_id);
            INSERT INTO chat_message_join VALUES(1,1),(1,2),(2,3);
            CREATE TABLE message(guid,text,is_from_me,is_system_message,date_retracted,date);
            INSERT INTO message VALUES('direct','I will bring the keys.',1,0,0,1),
                ('incoming','I will bring flowers.',0,0,0,2),('group','I will bring lunch.',1,0,0,3);
        """)
        with closing(sqlite3.connect(self.library / "Messages/chat.db")) as messages, messages:
            messages.executemany("INSERT INTO message VALUES(?, 'Thanks',1,0,0,?)",
                                 ((str(i), i) for i in range(4, 2006)))
            messages.executemany("INSERT INTO chat_message_join VALUES(1,?)", ((i,) for i in range(4, 2006)))
        mac_context.configure(self.state, ["Contacts", "Messages"])
        result = self.ask("Find what I promised my sister and remind me tomorrow")
        self.assertIn("bring the keys", result["reply"])
        self.assertEqual(result["sources"][0]["title"], "Messages [direct]")
        specific = self.ask("Find what I promised my sister about keys and remind me tomorrow")
        self.assertIn("bring the keys", specific["reply"])
        self.assertIn("no recorded promise", self.ask("Find what I promised my sister about flowers and remind me tomorrow")["reply"])
        self.assertIn("bring the keys", self.ask("List my upcoming reminders")["reply"])
        self.assertNotIn("keys", self.ask("List my reminders", source="voice")["reply"])
        self.room.speak = True
        with closing(open_memory(self.room.db_path)) as db, db:
            db.execute("UPDATE inbox SET due_at='2000-01-01'")
            self.assertNotIn("keys", self.room.speech(db)["text"])

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
            self.assertEqual(self.ask("Search my messages for Smith bicycle")["matches"], 1)
            self.assertEqual(self.ask("Search my messages for Smith suitcase")["matches"], 0)
            self.assertIn("basket", self.ask("Search my WhatsApp for Smith")["reply"])
            self.assertNotIn(
                "Unrelated person", self.ask("Show my family relationships")["reply"]
            )
            self.assertIn(
                "sister: Ada", self.ask("Show my family relationships")["reply"]
            )
            self.assertIn("sister: Ada", self.ask("Who is my sister?")["reply"])
            self.assertNotIn("Ada", self.ask("Who is my father?")["reply"])
            self.ask("Remember that my sister is Bea.")
            self.assertIn("Bea", self.ask("Who’s my sister?")["reply"])
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


class HeartbeatTests(unittest.TestCase):
    def test_offline_tick_and_hourly_fallback_survive_restart(self):
        import json
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with (
            patch("overnight_goal.baseline", return_value=("version", "passed")),
            patch("overnight_goal.power_and_idle", return_value=False) as idle,
            patch("overnight_goal.local_edit", side_effect=RuntimeError("unavailable")) as local,
            patch("overnight_goal.free_edit", side_effect=RuntimeError("provider down")) as cloud,
            patch("overnight_goal.offline_edit", return_value={"before": "", "after": ""}) as offline,
            patch("overnight_goal.latest_quota", side_effect=AssertionError("quota dependency")),
        ):
            self.assertEqual(overnight_goal.heartbeat(state, "test"), "no candidate")
            self.assertIn("waiting for AC", overnight_goal.heartbeat(state, "test"))
            self.assertEqual(offline.call_count, 1)
            idle.return_value = True
            self.assertEqual(overnight_goal.heartbeat(state, "test"), "no candidate")
            self.assertEqual(local.call_count, 1)
            self.assertEqual(cloud.call_count, 1)
            self.assertEqual(offline.call_count, 2)
            self.assertIn("next hourly", overnight_goal.heartbeat(state, "test"))
            status = json.loads((state / "heartbeat.json").read_text())
            self.assertEqual((status["reviewer"], status["attempts"]), ("Ruff", 1))
            self.assertEqual(len(status["fallbacks"]), 2)
            status["last_attempt"] = 0
            overnight_goal.save_status(state / "heartbeat.json", status)
            overnight_goal.heartbeat(state, "test", local_only=True)
            self.assertEqual(cloud.call_count, 1)

    def test_map_existing_notes_enabled_sources_and_verified_changes(self):
        import json
        import knowledge_map
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        vault = state / "Talos Memory"
        vault.mkdir()
        (vault / "Conversation-12.md").write_text("An existing hand-edited note")
        mac_context.configure(state, ["Contacts"])
        events = [{"id": "0123456789abcdef", "status": "proposed", "source": "room_voice.py",
                   "feature": "Improve voice playback", "acceptance": "Replay the fixture"},
                  {"id": "0123456789abcdef", "status": "done", "verification": "Fixture passed"}]
        (state / "feature-requests.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events) + "{")
        knowledge_map.publish(state)
        folder = vault / "Talos maps"
        self.assertIn("[[Conversation-12]]", (folder / "Conversation index.md").read_text())
        self.assertTrue((folder / "Contacts index.md").is_file())
        self.assertIn("Fixture passed", next((folder / "Changes").glob("Improve*.md")).read_text())
        mac_context.configure(state, [])
        knowledge_map.publish(state)
        self.assertFalse((folder / "Contacts index.md").exists())
        self.assertEqual((vault / "Conversation-12.md").read_text(), "An existing hand-edited note")

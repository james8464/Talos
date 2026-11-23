import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import mac_context
from talos_core.memory.personal_index import sync
from talos_core.runtime.maintenance import queue_summary
from talos_core.self_improvement import feature_queue


class SelfImprovementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"

    def test_model_proposal_survives_restart_and_completion(self):
        idea = json.dumps({"feature": "Find a matching passage", "acceptance": "Search hits include the term"})
        with patch("talos_core.self_improvement.feature_queue.model_text", return_value=idea) as infer, patch("talos_core.self_improvement.feature_queue.check_ollama_model"):
            self.assertEqual(feature_queue.review(self.state, "local"), "proposal queued")
            self.assertIn("def sync(", infer.call_args.args[0][0]["content"])
            self.assertEqual(feature_queue.review(self.state, "local"), "duplicate proposal")
        queue = self.state / feature_queue.FILE
        proposal = json.loads(queue.read_text())
        self.assertEqual(proposal["origin"], "local-model")
        for _ in range(2):
            feature_queue.review(self.state, "local", complete=(proposal["id"], "replay passed"))
        self.assertEqual(len(queue.read_text().splitlines()), 2)

    def test_pending_and_completion_remain_available_during_model_review(self):
        def idea(title):
            return json.dumps({"feature": title, "acceptance": "Verify with a fixture"})
        feature_queue.review(self.state, "local", infer=lambda *a, **k: idea("First proposal"))
        def review_while_busy(*args, **kwargs):
            self.assertEqual(feature_queue.review(self.state, "local"), "review already active")
            proposal = json.loads(feature_queue.review(self.state, "local", pending=True))
            result = feature_queue.review(self.state, "local", complete=(proposal["id"], "Passed fixture"))
            self.assertEqual(result, "completion recorded")
            return idea("Second proposal")
        result = feature_queue.review(self.state, "local", infer=review_while_busy)
        self.assertEqual(result, "proposal queued")
        pending = json.loads(feature_queue.review(self.state, "local", pending=True))
        self.assertEqual(pending["feature"], "Second proposal")

    def test_queue_summary_ignores_interrupted_append(self):
        path = self.root / "queue.jsonl"
        events = [{"status":"proposed","id":"one"}, {"status":"done","id":"one","verification":"verified"},
                  {"status":"proposed","id":"two"}]
        path.write_text("".join(json.dumps(event) + "\n" for event in events) + '{"status":')
        self.assertEqual(queue_summary(path), ["1 requests pending · 1 completed", "Last verified change · verified"])

    def test_offline_review_records_source_of_idea(self):
        with patch("talos_core.self_improvement.feature_queue.check_ollama_model", side_effect=RuntimeError("offline")), patch("talos_core.self_improvement.feature_queue.system_idea", return_value=None):
            self.assertEqual(feature_queue.review(self.state, "local"), "proposal queued")
        record = json.loads((self.state / feature_queue.FILE).read_text())
        self.assertEqual(record["origin"], "offline-source-check")
        self.assertTrue((feature_queue.ROOT / record["source"]).is_file())

    def test_live_limits_and_full_history(self):
        library = self.root / "Library"
        contacts = library / "Application Support/AddressBook/AddressBook-v1.abcddb"
        contacts.parent.mkdir(parents=True)
        with closing(sqlite3.connect(contacts)) as db, db:
            db.executescript("""CREATE TABLE ZABCDRECORD(Z_PK,ZFIRSTNAME,ZLASTNAME,ZORGANIZATION,ZNICKNAME);
                CREATE TABLE ZABCDEMAILADDRESS(ZOWNER,ZADDRESS);
                CREATE TABLE ZABCDPHONENUMBER(ZOWNER,ZFULLNUMBER);""")
            db.executemany("INSERT INTO ZABCDRECORD VALUES(?,?,'',NULL,NULL)",
                           ((i, "lastcontact" if i == 5000 else "fixture") for i in range(5001)))
        messages = library / "Messages/chat.db"
        messages.parent.mkdir(parents=True)
        with closing(sqlite3.connect(messages)) as db, db:
            db.executescript("""CREATE TABLE handle(id);
                CREATE TABLE message(guid,text,handle_id,is_from_me,date,is_system_message,date_retracted);""")
            db.executemany("INSERT INTO message VALUES(?,?,1,1,?,0,0)",
                           ((str(i), "oldestmessage" if i == 0 else "fixture", i) for i in range(2001)))
        mac_context.configure(self.state, ["Contacts", "Messages"])
        self.assertIn("Showing 8 of 2000", mac_context.lookup("fixture", {"Messages"}, library)["reply"])
        for term, app in (("lastcontact", "Contacts"), ("oldestmessage", "Messages")):
            live = mac_context.lookup(term, {app}, library)
            self.assertIn("More records exist", live["reply"])
            self.assertIn(term, sync(self.state, library, term)["reply"])

    def test_memory_excerpt_updates_deletes_and_respects_source_setting(self):
        library = self.root / "Library"
        path = library / "Messages/chat.db"
        path.parent.mkdir(parents=True)
        db = self.enterContext(closing(sqlite3.connect(path)))
        db.executescript("""CREATE TABLE handle(id);
            CREATE TABLE message(guid,text,handle_id,is_from_me,date,is_system_message,date_retracted);
            INSERT INTO handle VALUES ('fixture');""")
        db.execute("INSERT INTO message VALUES('one',?,1,1,1,0,0)", ("background " * 100 + "violet",))
        db.commit()
        mac_context.configure(self.state, ["Messages"])
        def search():
            return mac_context.room_answer("Search my memory for violet", self.state, False, library)["reply"]
        self.assertIn("violet", search())
        db.execute("UPDATE message SET text='changed'")
        db.commit()
        self.assertNotIn("Messages [one]", search())
        db.execute("DELETE FROM message")
        db.commit()
        self.assertEqual(sync(self.state, library), {})
        db.execute("INSERT INTO message VALUES('two','violet',1,1,1,0,0)")
        db.commit()
        self.assertIn("Messages [two]", search())
        db.executemany("INSERT INTO message VALUES(?, 'violet', 1,1,1,0,0)", [(str(i),) for i in range(8)])
        db.commit()
        self.assertIn("eight best", search())
        db.execute("UPDATE message SET text='violet blue orchid' WHERE guid='two'")
        db.execute("UPDATE message SET text='violet bright blue' WHERE guid='1'")
        db.commit()
        def ask(text):
            return mac_context.room_answer(text, self.state, False, library)["reply"]
        self.assertIn("Messages [two]", ask('Search my Messages memory for "violet blue"'))
        self.assertNotIn("Messages [1]", ask('Search my memory for “violet blue”'))
        self.assertIn("disabled", ask("Search my Mail memory for violet"))
        self.assertIn("quotation", ask('Search my memory for "violet'))
        self.assertIn("sixteen", ask("Search my memory for !!!"))
        mac_context.configure(self.state, [])
        self.assertNotIn("Messages [two]", search())

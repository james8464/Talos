import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent import FINAL_DECISION_SCHEMA, chat, listen, parse_decision, process_event, watch
from talos_core.memory.agent_memory import decisions, open_memory, recent_context, record_decision, review_decision
from talos_core.runtime.local_actions import acknowledge, inbox_items
from talos_core.memory.transcript_memory import remember, search_transcripts

EVENT = {"kind": "room", "summary": "I need to find the spare bike key."}
LOOKUP = {
    "disposition": "look_up", "tool": "search_memory", "goal": "Find the key",
    "reason": "The location may have been mentioned earlier.", "detail": "spare bike key",
}
NOTE = {
    "disposition": "act", "tool": "save_note", "goal": "Make the key easy to find",
    "reason": "The earlier conversation gave a location.", "detail": "Spare bike key: blue drawer.",
}


class ToolsAndMemoryTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "talos.sqlite3"
        self.database = self.enterContext(closing(open_memory(self.path)))

    def test_looks_up_earlier_conversation_before_writing_note(self):
        remember(self.database, "voice:example", [
            {"role": "user", "content": "The spare bike key is in the blue drawer."}
        ])

        with (
            patch("agent.check_ollama_model") as check_local,
            patch("agent.model_text", side_effect=[
                json.dumps(LOOKUP),
                json.dumps({**LOOKUP, "tool": "list_inbox", "detail": "Check for duplicate notes"}),
                json.dumps(NOTE),
            ]) as model,
        ):
            result = process_event(
                self.database, EVENT, apply=True, trusted_owner=True, model="test-local:7b",
            )

        check_local.assert_called_once_with("test-local:7b")
        self.assertEqual(model.call_args.kwargs["model"], "test-local:7b")
        message = json.loads(model.call_args.args[0][-1]["content"])
        self.assertIs(message["trusted_owner_request"], True)
        self.assertIn("blue drawer", message["lookup_results"][0]["result"][0]["text"])
        self.assertEqual(message["lookup_results"][1]["result"], [])
        self.assertEqual(result["tools_used"], ["search_memory", "list_inbox"])
        self.assertEqual(result["action"], "applied")
        self.assertEqual(inbox_items(self.database)[0]["text"], NOTE["detail"])
        with closing(open_memory(self.path)) as restarted:
            self.assertEqual(decisions(restarted)[0]["action"], "applied")
            self.assertEqual(inbox_items(restarted)[0]["kind"], "note")

    def test_shadow_and_blocked_tools_never_write_to_inbox(self):
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", side_effect=[
                json.dumps(NOTE), json.dumps({**NOTE, "tool": "speak"}),
            ]),
        ):
            shadow = process_event(self.database, EVENT)
            local = process_event(self.database, EVENT, apply=True)
        self.assertEqual(shadow["mode"], "shadow")
        self.assertEqual(local["action"], "blocked")
        self.assertEqual(inbox_items(self.database), [])

    def test_bad_or_looping_plan_makes_no_changes(self):
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value=json.dumps(LOOKUP)) as model,
            self.assertRaisesRegex(RuntimeError, "lookup limit"),
        ):
            process_event(self.database, EVENT, apply=True)
        self.assertEqual(model.call_count, 3)
        self.assertEqual(model.call_args.kwargs["schema"], FINAL_DECISION_SCHEMA)
        self.assertEqual(json.loads(model.call_args.args[0][-1]["content"])["lookups_remaining"], 0)
        self.assertEqual(decisions(self.database), [])
        self.assertEqual(inbox_items(self.database), [])

    def test_events_cannot_mark_themselves_as_owner_requests(self):
        event = {**EVENT, "kind": "owner_request"}
        with (
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value=json.dumps(NOTE)) as model,
        ):
            status = listen(
                self.database, io.StringIO(json.dumps(event)), io.StringIO(), io.StringIO(),
            )
        self.assertEqual(status, 0)
        message = json.loads(model.call_args.args[0][-1]["content"])
        self.assertIs(message["trusted_owner_request"], False)

    def test_future_reminder_and_acknowledgment(self):
        reminder = parse_decision(json.dumps({
            **NOTE, "tool": "set_reminder", "remind_in_minutes": 15,
        }))
        item_id = record_decision(self.database, EVENT, reminder, apply=True)
        self.assertEqual(inbox_items(self.database)[0]["id"], item_id)
        self.assertEqual(inbox_items(self.database, due_only=True), [])
        with self.database:
            self.database.execute(
                "UPDATE inbox SET due_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", item_id),
            )
        self.assertEqual(inbox_items(self.database, due_only=True)[0]["id"], item_id)
        acknowledge(self.database, item_id)
        self.assertEqual(inbox_items(self.database), [])

    def test_rejects_invalid_reminder_intervals(self):
        for minute_value in (0, True, 1.5, 43_201):
            with self.subTest(minute_value=minute_value), self.assertRaises(RuntimeError):
                parse_decision(json.dumps({
                    **NOTE, "tool": "set_reminder", "remind_in_minutes": minute_value,
                }))

    def test_rolls_back_decision_if_inbox_write_fails(self):
        self.database.execute("""
            CREATE TRIGGER reject_inbox BEFORE INSERT ON inbox BEGIN
                SELECT RAISE(ABORT, 'test failure');
            END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            record_decision(self.database, EVENT, NOTE, apply=True)
        self.assertEqual(decisions(self.database), [])
        self.assertEqual(inbox_items(self.database), [])

    def test_chat_saves_exchange_and_recalls_voice_across_restarts(self):
        remember(self.database, "voice:another", [
            {"role": "user", "content": "The bike lock uses a green tag."}
        ])
        question = "What color is the tag for the bike lock?"
        with (
            closing(open_memory(self.path)) as restarted,
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value="The tag is green.") as model,
        ):
            self.assertEqual(chat(restarted, question), "The tag is green.")
            self.assertIn("green tag", model.call_args.args[0][0]["content"])
        matches = search_transcripts(self.database, "green tag")
        self.assertEqual({row["session"] for row in matches}, {"voice:another", "text"})
        without_voice = search_transcripts(self.database, "green tag", exclude_session="voice:another")
        self.assertTrue(all(row["session"] == "text" for row in without_voice))

    def test_recall_returns_distinct_evidence_before_repeated_equal_rank_hits(self):
        from talos_core.memory.memory_ranking import tokens
        with tempfile.TemporaryDirectory() as directory, closing(open_memory(Path(directory) / "trial.db")) as db:
            items = ["violet bicycle", *["violet keys"] * 8]
            remember(db, "text", [{"role": "user", "content": text} for text in items])
            remember(db, "visit/fixture", [{"role": "user", "content": "violet visitor"}])
            hits = search_transcripts(db, "violet")
            self.assertEqual([row["text"] for row in hits[:2]], ["violet keys", "violet bicycle"])
            self.assertEqual(len(hits), 5)
            self.assertIn("京都", tokens("京都駅"))
            self.assertTrue(all(row["session"] == "text" and "fts_rank" not in row for row in hits))

    def test_recall_treats_search_syntax_as_text(self):
        remember(self.database, "voice:one", [
            {"role": "user", "content": "The passport is in the red folder."}
        ])
        self.assertIn("red folder", search_transcripts(self.database, '("passport")*')[0]["excerpt"])

    def test_opens_original_shadow_database_without_losing_feedback(self):
        old_path = self.path.parent / "old.sqlite3"
        with closing(sqlite3.connect(old_path)) as old:
            with old:
                old.execute("""
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, event TEXT NOT NULL,
                        proposal TEXT NOT NULL, feedback TEXT, reviewed_at TEXT
                    )
                """)
                old.execute(
                    "INSERT INTO decisions VALUES (1, ?, ?, ?, ?, ?)",
                    ("2026-01-01", json.dumps(EVENT), json.dumps(NOTE), "Keep this", "2026-01-01"),
                )
        with closing(open_memory(old_path)) as migrated:
            row = decisions(migrated)[0]
            self.assertEqual(row["feedback"], "Keep this")
            self.assertEqual(row["mode"], "shadow")
            self.assertEqual(row["proposal"], NOTE)
            self.assertEqual(inbox_items(migrated), [])
            retained = recent_context(migrated, "spare bike key")["owner_feedback"]
            self.assertEqual(retained[0]["feedback"], "Keep this")

    def test_retrieves_relevant_correction_even_after_newer_unrelated_reviews(self):
        decision_id = record_decision(self.database, EVENT, NOTE)
        review_decision(self.database, decision_id, "Bike key labels refer to the spare one.")
        for name in ("rain", "plumber", "recycling", "guests"):
            other_id = record_decision(
                self.database, {"kind": "note", "summary": name}, NOTE,
            )
            review_decision(self.database, other_id, f"Only notify me about {name} once.")
        feedback = recent_context(self.database, "Where is the bike key?")["owner_feedback"]
        self.assertIn("Bike key labels refer to the spare one.", [row["feedback"] for row in feedback])
        review_decision(self.database, decision_id, "The bike key labels refer to both keys.")
        updated = recent_context(self.database, "bike key labels")["owner_feedback"]
        self.assertIn("The bike key labels refer to both keys.", [row["feedback"] for row in updated])
        self.assertNotIn("Bike key labels refer to the spare one.", [row["feedback"] for row in updated])

    def test_watch_emits_due_alert_once_per_run_without_inference(self):
        alert_id = record_decision(self.database, EVENT, {**NOTE, "tool": "log_alert"}, apply=True)
        record_decision(self.database, EVENT, {**NOTE, "tool": "create_task"}, apply=True)
        record_decision(self.database, EVENT, {
            **NOTE, "tool": "set_reminder", "remind_in_minutes": 15,
        }, apply=True)
        output = io.StringIO()
        with (
            patch("agent.time.sleep", side_effect=[None, KeyboardInterrupt]),
            patch("agent.model_text") as model,
            redirect_stdout(output),
            self.assertRaises(KeyboardInterrupt),
        ):
            watch(self.database)
        model.assert_not_called()
        self.assertEqual([json.loads(line)["id"] for line in output.getvalue().splitlines()], [alert_id])

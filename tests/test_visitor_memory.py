import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import goal_store
import visitor_memory
from agent_memory import open_memory
from room_service import Room
from room_dialogue import spoken_reply
from transcript_memory import search_transcripts


class VisitorMemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.db = self.root / "room.sqlite"
        self.vault = self.root / "Talos Memory"
        self.room = Room(self.db, "fixture", online=False, model_state=lambda: "warming")
        self.person = str(uuid4())
        self.enrollment = {
            "name": "Robin",
            "id": self.person,
            "introduction": "My name is Robin.",
            "agreed": True,
            "template": base64.b64encode(bytes(range(256))).decode(),
        }

    def post(self, route, **body):
        return self.room.route("POST", "/v1/" + route, body)[1]

    def facing(self, text, **kwargs):
        return self.post("voice/facing", text=text, **kwargs)

    def review(self):
        self.room.review_after = 0
        while self.room.review_memory():
            pass

    def test_private_enrollment_summary_tracks_reference_removal(self):
        question = "Who is enrolled for face recognition?"
        self.assertIn("No visitors", self.post("turn", text=question)["reply"])
        self.post("visitors/enroll", **self.enrollment)
        reply = self.post("turn", text=question)
        self.assertIn("Robin", reply["reply"])
        self.assertNotIn(self.enrollment["template"], str(reply))
        self.assertNotIn("Robin", self.facing(question)["reply"])
        self.post("visitors/forget-reference", id=self.person)
        self.assertNotIn("Robin", self.post("turn", text=question)["reply"])

    def test_consent_restart_vault_backlinks_and_private_recall(self):
        with self.assertRaises(ValueError):
            self.post("visitors/enroll", **{**self.enrollment, "agreed": False})
        for _ in range(2):
            self.post("visitors/enroll", **self.enrollment)
        self.facing("I teach ceramics.", visitor=self.person)
        self.facing("I prefer charcoal.", remember=False)
        self.assertFalse(self.room.review_memory())  # dialogue takes priority
        self.room = Room(self.db, "fixture", online=False, model_state=lambda: "warming")
        self.review()
        self.assertEqual(len(list(self.vault.glob("Person-*.md"))), 1)
        sources = list(self.vault.glob("Conversation-*.md"))
        self.assertEqual(len(sources), 2)
        text = "\n".join(f.read_text() for f in sources)
        self.assertIn(f"[[Person-Robin-{self.person}|Robin]]", text)
        self.assertIn("[[Topic-", text)
        self.assertNotIn("charcoal", text)
        self.assertNotIn(self.enrollment["template"], text)
        self.assertTrue(any("ceramics" in f.read_text() for f in self.vault.glob("Topic-*.md")))
        edited = sources[0]
        edited.write_text(edited.read_text() + "\nMy own annotation")
        self.review()
        self.assertIn("My own annotation", edited.read_text())
        self.assertIn("ceramics", self.facing("What do you remember about me?", visitor=self.person)["reply"])
        self.assertIn("Robin", self.facing("Who am I?", visitor=self.person)["reply"])
        self.assertNotIn("Robin", self.facing("Who am I?")["reply"])
        with closing(open_memory(self.db)) as db:
            self.assertEqual(search_transcripts(db, "ceramics"), [])
        visitor2 = str(uuid4())
        self.post("visitors/enroll", **{**self.enrollment, "id": visitor2, "name": "Robin"})
        self.assertNotIn("ceramics", self.facing("What do you remember about me?", visitor=visitor2)["reply"])

    def test_forget_reference_and_unmatched_introduction(self):
        self.post("visitors/enroll", **self.enrollment)
        self.post("visitors/forget-reference", id=self.person)
        self.assertEqual(self.room.route("GET", "/v1/visitors", {})[1]["profiles"], [])
        with self.assertRaises(ValueError):
            self.facing("Who am I?", visitor=self.person)
        self.facing("My name is Quinn.", remember=True)
        self.review()
        (intro,) = self.vault.glob("Introduction-*.md")
        self.assertIn("Quinn", intro.read_text())
        self.assertEqual(len(list(self.vault.glob("Person-*.md"))), 1)

    def test_spoken_reply_repairs_echo_and_conversation_preserves_context(self):
        replies = iter(['{"talos_says":"I found the missing blue tile."}', '{"talos_says":"Where had it been hiding?"}'])
        self.assertEqual(
            spoken_reply(lambda *a, **k: next(replies), [], "I found the missing blue tile.", "local"),
            "Where had it been hiding?",
        )
        self.room.model_state = lambda: "ready"
        seen = []

        def reply(messages, **kwargs):
            seen.append([m["content"] for m in messages])
            return '{"talos_says":"That sounds worth the effort."}'

        with patch("room_service.check_ollama_model"), patch("room_service.model_text", side_effect=reply):
            token = self.facing("Long day, but I got the kiln going.")["context"]
            token = self.facing("It took all afternoon.", context=token)["context"]
            self.facing("Can you tell me more?", context=token)
            self.assertIn("Long day, but I got the kiln going.", seen[-1])
            self.assertIn("It took all afternoon.", seen[-1])
            self.facing("What happened earlier?", context="expired-or-another-client")
            self.assertNotIn("It took all afternoon.", seen[-1])

    def test_unseen_physical_experience_is_not_spoken_as_talos_memory(self):
        outputs = iter(["I've been looking for that notebook too.", "Behind the piano was a good hiding place."])
        def generate(*args, **kwargs):
            return json.dumps({"talos_says":next(outputs)})
        reply = spoken_reply(generate, [], "I finally found my notebook behind the piano.", "fixture")
        self.assertEqual(reply, "Behind the piano was a good hiding place.")

    def test_optional_model_review_keeps_only_source_sentences(self):
        statement = "Every Tuesday I help at the observatory."
        raw = {
            "facts": [
                {"kind": "Self-description", "quote": statement, "subject": "observatory"},
                {"kind": "Self-description", "quote": "I own an observatory.", "subject": "observatory"},
                {"kind": "Self-description", "quote": statement, "subject": "museum"},
            ]
        }
        self.assertEqual(
            visitor_memory.reviewed_facts(statement, lambda *a, **k: json.dumps(raw)),
            [("Self-description", statement, "observatory")],
        )
        self.assertEqual(visitor_memory.reviewed_facts(statement, lambda *a, **k: "invalid"), [])

    def test_facing_preview_confirm_cancel_retry_and_expiry(self):
        preview = self.facing("Can you remind me in six minutes to check the kiln?")
        self.assertIn("6 minutes", preview["reply"])

        def inbox():
            return self.room.route("GET", "/v1/inbox", {})[1]["items"]

        self.assertEqual(inbox(), [])
        self.facing("confirm", offer="another offer")
        self.assertEqual(inbox(), [])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(lambda _: self.facing("confirm", offer=preview["offer"]), range(2))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(inbox()), 1)
        other = self.facing("Create a task to buy paper.")
        self.facing("cancel", offer=other["offer"])
        self.facing("confirm", offer=other["offer"])
        third = self.facing("Save a note that the sample is blue.")
        with patch("room_service.monotonic", return_value=float("inf")):
            self.facing("confirm", offer=third["offer"])
        self.assertEqual(len(inbox()), 1)

    def test_existing_missing_evidence_becomes_actionable_near_deadline(self):
        deadline = datetime.now(UTC) + timedelta(days=3)
        goal = self.post(
            "goals/save",
            id=str(uuid4()),
            outcome="Prepare review",
            topic="Studio",
            lead=0,
            folder=str(self.root),
            required_doc="estimate",
            deadline=deadline.isoformat(),
            preference="risk",
        )
        self.assertEqual(self.room.route("GET", "/v1/inbox", {})[1]["items"], [])
        for _ in range(2):
            with closing(open_memory(self.db)) as db, db:
                goal_store.refresh(db, goal["id"], deadline - timedelta(hours=5))
        (alert,) = self.room.route("GET", "/v1/inbox", {})[1]["items"]
        self.assertIn("within a day", alert["text"])
        self.assertIn("estimate", alert["text"])

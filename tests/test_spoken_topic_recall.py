import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.memory.agent_memory import open_memory
from talos_core.room.room_dialogue import decision, immediate_recall
from talos_core.room.room_recollection import RECALL_MESSAGES
from talos_core.room.room_service import Room


class SpokenRecallTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "room.sqlite"
        self.generate = Mock(return_value=json.dumps({"talos_says": "Put it in the top drawer."}))
        self.room = Room(self.path, "fixture", model_state=lambda: "ready", voice_generate=self.generate, online=False)

    def ask(self, text, context=None):
        status, response = self.room.route("POST", "/v1/voice/facing", {"text": text, "context": context})
        self.assertEqual(status, 200)
        return response

    def test_recall_early_topic_preserves_attribution_without_enlarging_model_prompts(self):
        response = self.ask("Where should I put the red notebook?")
        for i in range(8):
            self.generate.return_value = json.dumps({"talos_says": f"The new suggestion is number {i}."})
            response = self.ask(f"Give me a different suggestion for item {i}.", response["context"])
        model_input = self.generate.call_args.args[0]
        self.assertEqual(len(model_input), 14)  # system, twelve recent messages, new question
        self.assertNotIn("red notebook", str(model_input))
        self.generate.reset_mock()
        response = self.ask("What did we say about the red notebook?", response["context"])
        self.assertEqual(response["reply"], 'You said, “Where should I put the red notebook?” I said, “Put it in the top drawer.”')
        response = self.ask("What did you suggest about my red notebook?", response["context"])
        self.assertEqual(response["reply"], 'I said, “Put it in the top drawer.”')
        self.generate.assert_not_called()
        with closing(open_memory(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 0)

    def test_addressed_voice_recall_uses_its_own_window_and_expires(self):
        def say(words):
            return self.room.route("POST", "/v1/turn", {"source": "voice", "text": words})[1]["reply"]
        with patch("talos_core.room.room_service.plan", return_value=decision("reply", "Understood.")):
            say("I left my notebook near the door.")
            for i in range(5):
                say(f"Now considering topic {i}.")
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("recall must be local")):
            self.assertIn("near the door", say("What did I say about my notebook?"))
            self.room.voice_window_updated = float("-inf")
            self.assertIn("couldn’t find", say("What did I say about my notebook?"))

    def test_missing_token_expiry_and_restart_clear_recall(self):
        for how in ("missing", "expired", "restart"):
            with self.subTest(how=how):
                response = self.ask("Where should I put the red notebook?")
                token = response["context"]
                if how == "missing":
                    token = None
                elif how == "expired":
                    key, _, rows, profile = self.room.facing_exchange
                    self.room.facing_exchange = key, 0, rows, profile
                else:
                    self.room = Room(self.path, "fixture", online=False)
                answer = self.ask("What did we say about the red notebook?", token)["reply"]
                self.assertIn("couldn’t find", answer)
                self.assertNotIn("drawer", answer)

    def test_user_and_assistant_words_remain_distinct_and_older_topic_is_evicted(self):
        rows = [{"role": "user", "content": "Do you have a theme?"}, {"role": "assistant", "content": "A blue notebook."}]
        self.assertIn("couldn’t find", immediate_recall("What did I say about a blue notebook?", rows))
        self.assertIn("A blue notebook", immediate_recall("What did you say about blue notebook?", rows))
        rows += [{"role": "user", "content": "Next"}, {"role": "assistant", "content": "All right."}] * (RECALL_MESSAGES // 2)
        self.assertIn("couldn’t find", immediate_recall("What did we say about blue notebook?", rows))
        self.assertEqual(immediate_recall("Can you repeat that?", rows), 'I said, “All right.”')

    def test_dictation_compounds_in_either_direction(self):
        for recorded, query in (("notebook", "note book"), ("note book", "notebook")):
            rows = [{"role": "user", "content": f"The {recorded} is stored on shelf three."}]
            self.assertIn("shelf three", immediate_recall("What did I say about the "+query+"?", rows))
            self.assertIn("couldn’t find", immediate_recall("What did I say about the workbook?", rows))

    def test_late_subject_in_long_dictation_has_a_bounded_audible_excerpt(self):
        rows = [{"role": "user", "content": "Some background. " * 40 + "The notebook has a violet ribbon."}]
        answer = immediate_recall("What did I mention about the notebook?", rows)
        self.assertIn("violet ribbon", answer)
        self.assertLessEqual(len(answer), 400)
        self.assertIsNone(immediate_recall("Do you recommend this notebook?", rows))

    @unittest.skipUnless(os.getenv("TALOS_TEST_SPOKEN_RECALL") == "1", "requires installed Whisper, Kokoro and PyAV")
    def test_real_speech_replay_through_recognition_room_and_playback(self):
        from talos_core.interfaces.telegram_voice import render_note, transcribe_note

        spoken = transcribe_note(render_note("My red notebook is on the table."))
        question = transcribe_note(render_note("What did I say about the red notebook?"))
        first = self.ask(spoken)
        answer = self.ask(question, first["context"])["reply"]
        heard = transcribe_note(render_note(answer)).lower()
        self.assertIn("red notebook", heard.replace("note book", "notebook"))
        self.assertIn("on the table", heard)

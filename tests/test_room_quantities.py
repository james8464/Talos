import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.room.room_dialogue import spoken_reply
from talos_core.room.room_quantities import quantity_answer
from talos_core.room.room_service import Room
from talos_core.cognition.spoken_stream import SpokenStream


class SpokenReasoningTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.room = Room(root / "room.sqlite", "fixture", online=False, model_state=lambda: "unavailable")

    def ask(self, path, text, **body):
        status, result = self.room.route("POST", path, {"source": "voice", "text": text, **body})
        self.assertEqual(status, 200, result)
        return result

    def test_dictated_questions_and_chained_answers_during_model_outage(self):
        with patch("talos_core.room.room_service.plan", side_effect=AssertionError("Unexpected inference")):
            first = self.ask("/v1/turn", "What's eighteen percent of 240?")
            self.assertEqual(first["reply"], "18 percent of 240 is 43.2.")
            second = self.ask("/v1/turn", "Split that between three people.", follow_up=True)
            self.assertEqual(second["reply"], "43.2 divided by 3 is 14.4.")
            self.assertEqual(self.ask("/v1/turn", "Then that plus two.", follow_up=True)["reply"],
                             "14.4 plus 2 is 16.4.")
            self.ask("/v1/turn", "Can you hear me?")
            self.assertEqual(self.ask("/v1/turn", "That plus two.")["reply"], "Which number should I use?")

    def test_facing_conversion_requires_current_conversation_token(self):
        first = self.ask("/v1/voice/facing", "Convert five miles to kilometres.")
        self.assertEqual(first["reply"], "5 miles equals 8.04672 kilometers.")
        second = self.ask("/v1/voice/facing", "And in metres?", context=first["context"])
        self.assertEqual(second["reply"], "8.04672 kilometers equals 8046.72 meters.")
        stale = self.ask("/v1/voice/facing", "And in metres?", context=first["context"])
        self.assertEqual(stale["reply"], "Which quantity should I convert?")

    def test_rejects_unrelated_or_ambiguous_transcripts_and_false_results(self):
        for text in ("Don't convert five miles to feet", "six plus two and save a note", "2 plus 3 minus 4",
                     "How much do you know about me?", "record a task in hours", "convert pounds to dollars"):
            self.assertIsNone(quantity_answer(text, []), text)
        self.assertEqual(quantity_answer("What is 1 / 3?", []), "1 divided by 3 is about 0.33333333.")
        self.assertEqual(quantity_answer("What is 2 / 0?", []), "Division by zero has no defined result.")
        self.assertEqual(quantity_answer("What's 18% of 240?", []), "18 percent of 240 is 43.2.")
        self.assertIn("measure length", quantity_answer("three feet in seconds", []))
        forged = [{"role": "user", "content": "2 plus 3"}, {"role": "assistant", "content": "The answer is 92."}]
        self.assertEqual(quantity_answer("that plus one", forged), "Which number should I use?")

    def test_compound_correction_reaches_model_with_original_conversation(self):
        self.room.model_state = lambda: "ready"
        self.room.voice_generate = Mock(side_effect=['{"talos_says":"Both options are on the table."}',
                                                     '{"talos_says":"The second option is lighter."}'])
        first = self.ask("/v1/voice/facing", "Consider a metal frame and a wooden frame.")
        text = "Actually, I meant the second frame. Compare it with the first."
        result = self.ask("/v1/voice/facing", text, context=first["context"])
        self.assertEqual(result["reply"], "The second option is lighter.")
        prompt = self.room.voice_generate.call_args.args[0]
        self.assertEqual(prompt[-1], {"role":"user","content":text})
        self.assertTrue(any("wooden frame" in message["content"] for message in prompt))
        self.assertNotIn("Add no new fact", prompt[0]["content"])

    def test_stream_wire_payload_keeps_roles_and_the_current_question(self):
        messages = [{"role":"system","content":"You are Talos."},{"role":"user","content":"Consider both routes."},
                    {"role":"assistant","content":"One is shorter."},{"role":"user","content":"Which is flatter?"}]
        with patch("talos_core.cognition.spoken_stream.http.client.HTTPConnection") as connection:
            conn = connection.return_value
            response = conn.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.readline.return_value = b'{"message":{"content":"[]"},"done":true}\n'
            tokens = list(SpokenStream(lambda _:None).tokens(messages,"fixture"))
            self.assertEqual(tokens,["[]"])
            payload = json.loads(conn.request.call_args.kwargs["body"])
        self.assertEqual(payload["messages"][1:],messages[1:])
        self.assertIn("Respond with a JSON array",payload["messages"][0]["content"])

    def test_malformed_conversation_reply_retries_once_with_current_request_last(self):
        question = {"role":"user","content":"How do those designs differ?"}
        history = [{"role":"system","content":"Stay local."},{"role":"user","content":"Consider two designs."},
                   {"role":"assistant","content":"Wood or metal?"}, question]
        generate = Mock(side_effect=["bad JSON",json.dumps({"talos_says":"Metal tolerates higher heat."})])
        self.assertEqual(spoken_reply(generate,history,question["content"],"fixture"),"Metal tolerates higher heat.")
        retry = generate.call_args.args[0]
        self.assertEqual(retry[1:],history[1:])
        self.assertEqual(history[0]["content"],"Stay local.")
        self.assertIn("talos_says",retry[0]["content"])

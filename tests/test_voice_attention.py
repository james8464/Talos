import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.room.room_service import Room


class VoiceAttentionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.room = Room(self.state / "room.sqlite", "fixture", online=False)

    def turn(self, text, **body):
        return self.room.route("POST", "/v1/turn", {"text": text, "source": "voice", **body})[1]

    def test_other_speaker_is_ignored(self):
        result = self.turn("I told him the door is open.", speaker="other")
        self.assertEqual(result["reply"], "")
        self.assertEqual(result["action"], "ignored")
        self.assertIn("speaker", result["attention"])

    def test_crowd_or_facing_away_is_ignored_without_explicit_address(self):
        for body, reason in (
            ({"frame_people": 2, "facing": "toward"}, "multiple people"),
            ({"frame_people": 1, "facing": "away"}, "facing away"),
        ):
            with self.subTest(body=body):
                result = self.turn("I left the door open.", **body)
                self.assertEqual(result["reply"], "")
                self.assertIn(reason, result["attention"])

    def test_single_facing_person_allows_conversation(self):
        reply = {"disposition": "reply", "tool": "none", "goal": "",
                 "reason": "answered", "detail": "The door is open."}
        with patch("talos_core.room.room_service.plan", return_value=reply):
            result = self.turn("I left the door open.", frame_people=1, facing="toward")
        self.assertEqual(result["reply"], "The door is open.")

    def test_barge_in_stops_agent_speech_and_returns_to_listening(self):
        now = time.monotonic() - 5
        self.room.dictation.start(now)
        self.room.dictation.speech_started(now + 1, text="Tell me a story")
        self.room.dictation.speech_ended(now + 2)
        self.room.dictation.response_started(now + 3)
        event = self.room.observe_voice({"text": "Wait, stop.", "speaker": "user"})
        self.assertTrue(event.barged_in)
        self.assertEqual(self.room.dictation.state, "listening")

    def test_background_speech_does_not_barge_in(self):
        now = time.monotonic() - 5
        self.room.dictation.start(now)
        self.room.dictation.speech_started(now + 1, text="Tell me a story")
        self.room.dictation.speech_ended(now + 2)
        self.room.dictation.response_started(now + 3)
        event = self.room.observe_voice({"text": "He said to leave it.", "speaker": "other"})
        self.assertFalse(event.barged_in)
        self.assertEqual(self.room.dictation.state, "agent_speaking")


if __name__ == "__main__":
    unittest.main()

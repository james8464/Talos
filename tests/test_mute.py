"""Silent-mode guarantees: Talos must never synthesize or play audio.

The laptop cannot produce sound, so ``TALOS_MUTE=1`` (or ``--mute``) must
disable speech synthesis, proactive speech, and the ``/v1/voice`` endpoint
while leaving typed turns fully functional.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.room.room_dialogue import decision
from talos_core.room.room_service import Room
from tools import talos_service


class MutedVoice:
    name = "should-never-run"

    def speak(self, text):
        raise AssertionError("muted room must not synthesize audio")


class SilentModeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"TALOS_MUTE": "1"}))

    def room(self, **kwargs):
        return Room(self.root / "room.sqlite", "local", voice=MutedVoice(), **kwargs)

    def test_mute_disables_speech_and_drops_the_voice(self):
        room = self.room(speak=True)
        self.assertFalse(room.speak)
        self.assertIsNone(room.voice)

    def test_voice_endpoint_refuses_to_synthesize(self):
        status, body = self.room().route("POST", "/v1/voice", {"text": "This must stay silent."})
        self.assertEqual(status, 503)
        self.assertNotIn("wav", body)

    def test_typed_turns_still_work(self):
        room = self.room()
        with patch("talos_core.room.room_service.plan",
                   return_value=decision("reply", "Text only, no audio.")):
            status, body = room.route("POST", "/v1/turn", {"text": "Tell me something.", "source": "text"})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "Text only, no audio.")
        self.assertNotIn("wav", body)

    def test_shadow_mode_stays_silent_too(self):
        self.assertFalse(self.room(apply=False).speak)

    def test_service_is_installed_muted(self):
        configuration = talos_service.configuration(self.root)
        self.assertIn("--mute", configuration["ProgramArguments"])
        self.assertNotIn("--system-voice", configuration["ProgramArguments"])
        self.assertEqual(configuration["EnvironmentVariables"]["TALOS_MUTE"], "1")

    def test_room_launcher_is_silent_by_default(self):
        from run_talos import muted_mode
        self.assertTrue(muted_mode(False, False, {}))
        self.assertTrue(muted_mode(True, True, {}))
        self.assertTrue(muted_mode(False, True, {"TALOS_MUTE": "1"}))
        self.assertFalse(muted_mode(False, True, {}))

    def test_telegram_voice_rendering_is_blocked_before_model_loading(self):
        from talos_core.interfaces.telegram_voice import render_note
        with self.assertRaises(RuntimeError):
            render_note("This must stay silent.")

    def test_environment_flag_is_required_for_silence(self):
        with patch.dict(os.environ, {"TALOS_MUTE": "0"}):
            room = self.room(speak=True)
        self.assertTrue(room.speak)


if __name__ == "__main__":
    unittest.main()

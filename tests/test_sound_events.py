"""Non-word sound classification wrapper."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import sound_events


class SoundEventTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.audio = Path(self.enterContext(tempfile.TemporaryDirectory())) / "clip.wav"
        self.audio.write_bytes(b"RIFF....WAVE")

    def test_classify_parses_and_ranks_events(self):
        result = subprocess_result([{"label": "speech", "confidence": 0.9},
                                    {"label": "music", "confidence": 0.2}])
        with patch.object(sound_events, "available", return_value=True), \
             patch.object(sound_events, "_binary", return_value=Path("/bin/true")), \
             patch("talos_core.mac.sound_events.subprocess.run", return_value=result):
            events = sound_events.classify(self.state, self.audio)
        self.assertEqual(events[0], {"label": "speech", "confidence": 0.9})
        self.assertIn("speech 90%", sound_events.describe(events))

    def test_bad_inputs_fail_cleanly(self):
        with patch.object(sound_events, "available", return_value=True), \
             patch.object(sound_events, "_binary", return_value=Path("/bin/true")):
            with self.assertRaises(sound_events.SoundEventError):
                sound_events.classify(self.state, self.audio.with_suffix(".txt"))
            empty = self.audio.with_name("empty.wav")
            empty.write_bytes(b"")
            with self.assertRaises(sound_events.SoundEventError):
                sound_events.classify(self.state, empty)
        with patch.object(sound_events, "available", return_value=False):
            with self.assertRaises(sound_events.SoundEventError):
                sound_events.classify(self.state, self.audio)

    def test_helper_failure_is_reported(self):
        result = subprocess_result([], returncode=1, stderr="no model")
        with patch.object(sound_events, "available", return_value=True), \
             patch.object(sound_events, "_binary", return_value=Path("/bin/true")), \
             patch("talos_core.mac.sound_events.subprocess.run", return_value=result):
            with self.assertRaises(sound_events.SoundEventError) as caught:
                sound_events.classify(self.state, self.audio)
        self.assertIn("no model", str(caught.exception))


def subprocess_result(payload, returncode=0, stderr=""):
    from types import SimpleNamespace
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)


if __name__ == "__main__":
    unittest.main()

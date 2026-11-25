"""Consent-gated capture verification with stubbed native capture."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from talos_core import cli
from talos_core.mac import capture_check

PAYLOAD = {"camera": True, "audio": True, "seconds": 1.0,
           "camera_permission": "authorized", "microphone_permission": "authorized"}


class CaptureCheckTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.state / "room.token").write_text("test-token\n")

    def fake_capture(self, calls):
        def capture(frame, clip, seconds, timeout):
            calls.append((frame, clip))
            frame.write_bytes(b"\xff\xd8\xffjpeg")
            clip.write_bytes(b"RIFF0000WAVE")
            return PAYLOAD
        return capture

    def test_refuses_without_consent_and_creates_nothing(self):
        with self.assertRaises(capture_check.ConsentRequired):
            capture_check.check(self.state, consent=False)
        self.assertEqual(list(self.state.glob(".capture-*")), [])

    def test_capture_verifies_endpoints_and_deletes_media(self):
        calls = []
        seen = {}

        def post(path, body, token, timeout=120):
            seen[path] = body
            if path == "/v1/vision/scene":
                return 200, {"scene": {"empty": True, "people": 0},
                             "description": "The scene looks empty."}
            return 200, {"events": [], "description": "No events."}

        with patch.object(capture_check, "_binary", return_value=Path("/bin/true")), \
                patch.object(capture_check, "_capture", self.fake_capture(calls)), \
                patch.object(capture_check, "_post", side_effect=post):
            result = capture_check.check(self.state, consent=True)
        self.assertTrue(result["captured"])
        self.assertTrue(result["media_deleted"])
        self.assertEqual(result["scene_description"], "The scene looks empty.")
        self.assertEqual(result["sound_event_count"], 0)
        self.assertIn("image", seen["/v1/vision/scene"])
        self.assertIn("audio", seen["/v1/sound"])
        self.assertEqual(list(self.state.glob(".capture-*")), [])

    def test_endpoint_failure_still_deletes_media(self):
        with patch.object(capture_check, "_binary", return_value=Path("/bin/true")), \
                patch.object(capture_check, "_capture", self.fake_capture([])), \
                patch.object(capture_check, "_post", return_value=(500, {"error": "nope"})):
            with self.assertRaises(capture_check.CaptureError):
                capture_check.check(self.state, consent=True)
        self.assertEqual(list(self.state.glob(".capture-*")), [])

    def test_cli_requires_the_consent_flag(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = cli.main(["capture-check", "--state", str(self.state)])
        self.assertEqual(code, 2)
        self.assertIn("refusing without --consent-capture", error.getvalue())

    def test_cli_prints_receipt(self):
        output = io.StringIO()
        with patch("talos_core.mac.capture_check.check",
                   return_value={**PAYLOAD, "seconds": 1.0, "media_deleted": True,
                                 "scene_description": "empty", "sound_event_count": 0}), \
                redirect_stdout(output):
            code = cli.main(["capture-check", "--consent-capture", "--json",
                             "--state", str(self.state)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["media_deleted"], True)


if __name__ == "__main__":
    unittest.main()

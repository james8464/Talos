"""Room endpoints for image and sound classification."""

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.room.room_service import Room


class RoomSenseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.room = Room(self.root / "room.sqlite", "local", speak=False, online=False)

    def test_scene_endpoint_classifies_a_png(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with patch("talos_core.mac.scene.detect",
                   return_value={"people": 0, "faces": 0, "window": 0, "empty": True}) as detect:
            status, body = self.room.route(
                "POST", "/v1/vision/scene",
                {"image": base64.b64encode(png).decode()},
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["scene"]["empty"])
        self.assertIn("empty", body["description"])
        detect.assert_called_once()
        self.assertEqual(list((self.root / "senses").glob("*")), [])

    def test_sound_endpoint_classifies_wav(self):
        wav = b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 32
        events = [{"label": "speech", "confidence": 0.9}]
        with patch("talos_core.mac.sound_events.classify", return_value=events):
            status, body = self.room.route(
                "POST", "/v1/sound", {"audio": base64.b64encode(wav).decode()},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["events"], events)
        self.assertEqual(list((self.root / "senses").glob("*")), [])

    def test_invalid_payloads_are_refused(self):
        bad = (
            ("POST", "/v1/vision/scene", {}),
            ("POST", "/v1/vision/scene", {"image": "not-base64"}),
            ("POST", "/v1/vision/scene",
             {"image": base64.b64encode(b"nope").decode()}),
            ("POST", "/v1/sound", {}),
            ("POST", "/v1/sound", {"audio": base64.b64encode(b"nope").decode()}),
        )
        for method, path, body in bad:
            with self.assertRaises(ValueError):
                self.room.route(method, path, body)


if __name__ == "__main__":
    unittest.main()

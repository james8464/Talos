import base64
import io
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from room_service import Room
from room_voice import STARTUP_LINE, prepare_assets, wav_base64


class RoomVoiceTests(unittest.TestCase):
    def test_wav_transport_contains_playable_pcm(self):
        samples = Mock()
        clipped = samples.clip.return_value
        amplified = Mock()
        clipped.__mul__ = Mock(return_value=amplified)
        amplified.astype.return_value.tobytes.return_value = b"\x00\x00\xff\x7f"
        with wave.open(io.BytesIO(base64.b64decode(wav_base64(samples, 24000))), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 24000, 2))
            self.assertEqual(wav.readframes(2), b"\x00\x00\xff\x7f")

    def test_voice_route_and_visible_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "room.sqlite"
            fallback = Room(path, "local")
            self.assertEqual(fallback.route("POST", "/v1/voice", {"text": STARTUP_LINE})[0], 503)
            self.assertIsNone(fallback.route("GET", "/v1/status", {})[1]["voice"])
            synth = Mock(return_value="sample-wav")
            room = Room(path, "local", voice=SimpleNamespace(name="bm_george", speak=synth))
            self.assertEqual(room.route("GET", "/v1/status", {})[1]["voice"], "bm_george")
            status, payload = room.route("POST", "/v1/voice", {"text": STARTUP_LINE})
            self.assertEqual((status, payload["wav"], payload["voice"]), (200, "sample-wav", "bm_george"))
            synth.assert_called_once_with(STARTUP_LINE)

    def test_setup_rejects_corrupt_assets_without_leaving_partial_files(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("room_voice.urllib.request.urlopen", return_value=io.BytesIO(b"corrupt")),
        ):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                prepare_assets(Path(directory), offline=False)
            self.assertEqual(list(Path(directory).iterdir()), [])

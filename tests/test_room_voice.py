import base64
import io
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import sys
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from talos_core.room.room_service import Room
from talos_core.room.room_voice import STARTUP_LINE, prepare_assets, wav_base64


class RoomVoiceTests(unittest.TestCase):
    def test_voice_starts_without_prerender_and_only_reuses_standard_responses(self):
        from talos_core.room.room_voice import KokoroVoice
        model = Mock()
        model.create.return_value = (object(), 24000)
        with patch.dict(sys.modules, {"kokoro_onnx": SimpleNamespace(Kokoro=lambda *args: model)}), patch("talos_core.room.room_voice.wav_base64", return_value="fixture"):
            voice = KokoroVoice(Path("."), "bm_george")
            model.create.assert_not_called()
            for _ in range(2):
                voice.speak("How long from now should I remind you?")
                voice.speak("Synthetic private reply.")
            self.assertEqual(model.create.call_count, 3)
            self.assertNotIn("Synthetic private reply.", voice.fixed_lines)

    def test_cached_sensor_reply_is_available_during_other_synthesis(self):
        from talos_core.room.room_voice import KokoroVoice
        started, release = threading.Event(), threading.Event()
        def synthesize(text, **options):
            if text == "Fixture private reply.":
                started.set()
                if not release.wait(5):
                    raise AssertionError("Fixture synthesis was never released")
            return object(), 24000

        model = Mock()
        model.create.side_effect = synthesize
        with patch.dict(sys.modules, {"kokoro_onnx": SimpleNamespace(Kokoro=lambda *args: model)}), patch(
            "talos_core.room.room_voice.wav_base64", return_value="fixture"
        ), ThreadPoolExecutor(max_workers=2) as workers:
            voice = KokoroVoice(Path("."), "bm_george")
            standard = "Yes, I can hear you."
            cached = voice.speak(standard)
            private = workers.submit(voice.speak, "Fixture private reply.")
            try:
                self.assertTrue(started.wait(2))
                reply = workers.submit(voice.speak, standard)
                self.assertEqual(reply.result(timeout=1), cached)
                self.assertFalse(private.done())
            finally:
                release.set()
            private.result(timeout=2)
            self.assertEqual(model.create.call_count, 2)
            self.assertNotIn("Fixture private reply.", voice.fixed_lines)

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
            patch("talos_core.room.room_voice.urllib.request.urlopen", return_value=io.BytesIO(b"corrupt")),
        ):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                prepare_assets(Path(directory), offline=False)
            self.assertEqual(list(Path(directory).iterdir()), [])

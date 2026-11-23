import io
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.interfaces import telegram_gateway as gateway
from talos_core.interfaces import telegram_voice as voice


class VoiceGatewayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "channel.db"
        self.download = Mock(return_value=b"audio")
        self.transcribe = self.enterContext(patch.object(gateway, "transcribe_note", return_value="Where is it?"))
        self.directory = self.enterContext(patch.object(gateway, "model_directory"))
        self.enterContext(patch.object(gateway, "check_ollama_model"))
        self.reply = self.enterContext(patch.object(gateway, "model_text", return_value='{"reply":"On the table."}'))
        self.updates, self.sent, self.audio = [], [], []
        self.file_size = 40

    def add(self, number, *, user=7, group=False, **fields):
        self.updates.append({"update_id": number, "message": {
            "from": {"id": user}, "chat": {"type": "group" if group else "private", "id": user}, **fields}})

    def note(self, number, *, duration=3, size=40, **opts):
        self.add(number, voice={"file_id": "note", "duration": duration, "file_size": size}, **opts)

    def api(self, method, **params):
        if method == "getUpdates":
            return self.updates
        if method == "getFile":
            return {"file_size": self.file_size, "file_path": "voice/file.oga"}
        if method == "sendVoice":
            self.audio.append(params["voice"])
            return {"message_id": len(self.audio)}
        self.assertEqual(method, "sendMessage")
        self.sent.append(params["text"])
        return {"message_id": len(self.sent)}

    def poll(self):
        with closing(gateway.open_channel(self.path)) as db:
            gateway.poll(db, self.api, 42, 7, "fixture", download_voice=self.download)
            return db.execute("SELECT question FROM turns ORDER BY seq").fetchall()

    def test_private_dictation_in_context_and_replay_after_restart(self):
        self.add(1, text="I left my notebook on the table.")
        self.note(2, user=8)
        self.note(3, group=True)
        self.note(4)
        self.assertEqual(self.poll(), [("I left my notebook on the table.",), ("Where is it?",)])
        last = self.reply.call_args.args[0]
        self.assertEqual(last[1]["content"], "I left my notebook on the table.")
        self.assertEqual(last[-1]["content"], "Where is it?")
        self.poll()
        self.assertEqual(len(self.sent), 2)
        self.download.assert_called_once()

    def test_speech_never_executes_a_slash_command(self):
        self.add(1, text="My previous turn.")
        self.transcribe.return_value = "/reset"
        self.note(2)
        self.assertEqual(self.poll(), [("My previous turn.",), ("/reset",)])
        self.assertEqual(self.reply.call_count, 2)

    def test_size_limits_unavailable_model_and_silence(self):
        self.note(1, duration=91)
        self.note(2, size=voice.MAX_VOICE_BYTES + 1)
        self.assertEqual(self.poll(), [])
        self.download.assert_not_called()
        self.file_size = voice.MAX_VOICE_BYTES + 1
        self.note(3)
        self.poll()
        self.download.assert_not_called()
        self.file_size = 40
        self.download.return_value = bytes(voice.MAX_VOICE_BYTES + 1)
        self.note(4)
        self.poll()
        self.transcribe.assert_not_called()
        self.directory.side_effect = voice.VoiceUnavailable()
        self.note(5)
        self.poll()
        self.assertIn("unavailable", self.sent[-1])
        self.directory.side_effect = None
        self.download.return_value = b"audio"
        self.transcribe.return_value = ""
        self.note(6)
        self.assertEqual(self.poll(), [])
        self.assertIn("make out speech", self.sent[-1])
        self.reply.assert_not_called()

    def test_preferences_and_synthesis_fallback_survive_restarts(self):
        self.add(1, text="/voice voice_only")
        self.add(2, text="First turn")
        self.poll()
        self.note(3)
        with patch.object(gateway, "render_note", return_value=b"OggSfixture") as render:
            self.poll()
            self.assertEqual(self.audio, [b"OggSfixture"])
            self.add(4, text="/voice all")
            self.add(5, text="Read this answer")
            self.poll()
            self.assertEqual(len(self.audio), 2)
            render.side_effect = RuntimeError("missing local voice")
            self.add(6, text="Read this too")
            self.poll()
            self.assertEqual(self.sent[-1], "On the table.")
            self.add(7, text="/voice off")
            self.poll()
            self.note(8)
            self.poll()
            self.assertEqual(render.call_count, 3)

    def test_rate_limited_voice_restarts_but_uncertain_delivery_is_not_repeated(self):
        self.add(1, text="/voice all")
        self.poll()
        self.add(2, text="first")
        with patch.object(gateway, "render_note", return_value=b"OggS") as synth:
            original_api = self.api
            def failing(method, **params):
                if method == "sendVoice":
                    raise gateway.ApiError(429)
                return original_api(method, **params)
            with patch.object(self, "api", side_effect=failing), self.assertRaises(gateway.ApiError):
                self.poll()
            self.assertEqual(self.poll(), [("first",)])
            self.assertEqual(self.audio, [b"OggS"])
            self.add(3, text="second")
            def timeout(method, **params):
                if method == "sendVoice":
                    raise TimeoutError()
                return original_api(method, **params)
            with patch.object(self, "api", side_effect=timeout), self.assertRaises(TimeoutError):
                self.poll()
            self.assertEqual(self.poll(), [("first",)])
            self.assertEqual(synth.call_count, 3)

    def test_upload_encodes_one_ogg_part_and_destination(self):
        import email
        with patch("talos_core.interfaces.telegram_gateway.http.client.HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.read.return_value = b'{"ok":true,"result":{"message_id":9}}'
            result = gateway.api_client("42:fixture")("sendVoice", chat_id=7, voice=b"OggS\x00data")
            self.assertEqual(result["message_id"], 9)
            method, path, body, headers = connection.return_value.request.call_args.args
            self.assertEqual((method, path), ("POST", "/bot42:fixture/sendVoice"))
            parsed = email.message_from_bytes(b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = parsed.get_payload()
            self.assertEqual([part.get_param("name", header="content-disposition") for part in parts], ["chat_id", "voice"])
            self.assertEqual([part.get_payload(decode=True) for part in parts], [b"7", b"OggS\x00data"])

    def test_default_setup_validates_all_files_before_publication(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"incomplete")), \
                patch.object(voice, "DEFAULT_MODEL", self.root / "default"), patch.object(voice, "load_model") as load:
            with self.assertRaisesRegex(voice.VoiceUnavailable, "verification failed"):
                voice.setup_voice()
            load.assert_not_called()
            self.assertFalse(voice.DEFAULT_MODEL.exists())


@unittest.skipUnless(os.getenv("TALOS_TEST_TELEGRAM_WHISPER"), "requires local Whisper and PyAV")
class RealSpeechTests(unittest.TestCase):
    def test_generated_speech_and_silence(self):
        import av
        import numpy as np
        with patch.dict(os.environ, {"TALOS_TELEGRAM_WHISPER_MODEL": os.environ["TALOS_TEST_TELEGRAM_WHISPER"]}):
            data = (Path(__file__).parent / "fixtures/synthetic-notebook.ogg").read_bytes()
            text = voice.transcribe_note(data)
            self.assertEqual(" ".join(text.casefold().strip(".").split()), "the blue notebook is on the kitchen table")
            buf = io.BytesIO()
            with av.open(buf, mode="w", format="ogg") as out:
                stream = out.add_stream("libopus", rate=48000)
                stream.layout = "mono"
                for _ in range(100):
                    frame = av.AudioFrame.from_ndarray(np.zeros((1, 960), dtype=np.int16), format="s16", layout="mono")
                    frame.sample_rate = 48000
                    for packet in stream.encode(frame):
                        out.mux(packet)
                for packet in stream.encode():
                    out.mux(packet)
            self.assertEqual(voice.transcribe_note(buf.getvalue()), "")
            with patch.object(voice, "MAX_VOICE_SECONDS", 1), self.assertRaisesRegex(ValueError, "duration limit"):
                voice.decode_note(buf.getvalue())


@unittest.skipUnless(os.getenv("TALOS_TEST_TELEGRAM_WHISPER") and os.getenv("TALOS_TEST_OLLAMA") == "1"
                     and os.getenv("TALOS_TEST_KOKORO") == "1", "requires the isolated local speech and chat models")
class SpokenConversationReplay(unittest.TestCase):
    def test_spoken_correction_survives_restart_and_outbound_audio_is_intelligible(self):
        notes = [(Path(__file__).parent / "fixtures/synthetic-notebook.ogg").read_bytes(),
                 voice.render_note("Actually, I moved the notebook to the hallway."),
                 voice.render_note("Where is the notebook now?")]
        sent = []
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                "TALOS_TELEGRAM_WHISPER_MODEL": os.environ["TALOS_TEST_TELEGRAM_WHISPER"]}):
            path = Path(tmp) / "channel.db"
            for number in range(len(notes) + 1):
                media = {"voice": {"file_id": "sample", "duration": 5}} if number else {"text": "/voice voice_only"}
                def call(method, number=number, media=media, **params):
                    if method == "getUpdates":
                        return [{"update_id": number + 1, "message": {"chat": {"id": 7, "type": "private"},
                                 "from": {"id": 7}, **media}}]
                    if method == "getFile":
                        return {"file_path": "voice/sample.oga"}
                    if method == "sendVoice":
                        sent.append(params["voice"])
                    return {"message_id": number + 10}
                with closing(gateway.open_channel(path)) as db:
                    gateway.poll(db, call, 42, 7, "qwen3:4b", download_voice=lambda _, number=number: notes[number - 1])
            with closing(gateway.open_channel(path)) as db:
                last = db.execute("SELECT answer FROM turns ORDER BY seq DESC LIMIT 1").fetchone()[0]
            self.assertEqual(len(sent), 3)
            for answer in (last, voice.transcribe_note(sent[-1])):
                self.assertIn("hallway", answer.casefold())
                self.assertNotIn("kitchen", answer.casefold())

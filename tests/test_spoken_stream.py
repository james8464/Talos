from contextlib import closing, ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
import wave

from talos_core.memory.agent_memory import open_memory
from talos_core.room.room_service import Handler, Room
from talos_core.cognition.spoken_stream import SpokenStream


def packet(content, done=False):
    return (json.dumps({"message": {"content": content}, "done": done}) + "\n").encode()


class LocalModel(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models":[{"name":"fixture"}]}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(payload)
        self.send_response(200)
        self.end_headers()
        if not payload["stream"]:
            self.wfile.write(json.dumps({"done": True, "message": {"content": '{"talos_says":"Spring follows winter."}'}}).encode())
            return
        first = self.server.first if len(self.server.requests) == 1 else self.server.retry_first
        for chunk in first:
            self.wfile.write(packet(chunk))
        self.wfile.flush()
        # Wait for a new prompt to be spoken, or for cancellation to close the upstream socket.
        self.request.setblocking(False)
        try:
            for _ in range(1500):
                if self.server.release.wait(0.005):
                    self.wfile.write(packet(',"We welcome the blooms."]', True))
                    return
                try:
                    if self.request.recv(1, socket.MSG_PEEK) == b"":
                        self.server.disconnected.set()
                        return
                except BlockingIOError:
                    pass
        finally:
            self.server.ended.set()

    def log_message(self, *_):
        pass


class FixtureVoice:
    name = "fixture"
    def speak(self, text):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((1, 2, 24_000, 0, "NONE", ""))
            wav.writeframes(b"\x00\x10" * 480)
        return base64.b64encode(stream.getvalue()).decode()


class SpokenStreamFlow(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.model = self.server(LocalModel)
        self.model.first = ['[', '"Spring fol', 'lows winter."']
        self.model.retry_first = ['["Spring follows winter."']
        self.model.release = threading.Event()
        self.model.disconnected = threading.Event()
        self.model.ended = threading.Event()
        self.model.requests = []
        self.stack.callback(self.model.release.set)
        self.stack.enter_context(patch.dict(os.environ, {"TALOS_OLLAMA_PORT": str(self.model.server_port)}))
        self.room = self.server(Handler)
        self.room.room = Room(self.root / "room.db", "fixture", online=False, model_state=lambda: "ready",voice=FixtureVoice())
        self.room.token = "synthetic"
        self.request_id = str(uuid4())

    def server(self, handler):
        server = self.stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), handler))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.stack.callback(worker.join)
        self.stack.callback(server.shutdown)
        return server

    def post(self, path, data):
        conn = self.stack.enter_context(closing(HTTPConnection("127.0.0.1", self.room.server_port, timeout=4)))
        conn.request("POST", "/v1/" + path, body=json.dumps(data), headers={"Authorization":"Bearer synthetic"})
        response = self.stack.enter_context(closing(conn.getresponse()))
        self.assertEqual(response.status, 200)
        return response

    def start(self, text="Tell me about spring."):
        return self.post("voice/facing/stream", {"source":"voice", "text":text, "remember":True, "request_id":self.request_id})

    def test_supervised_stream_defers_maintenance_without_an_inventory_call(self):
        from unittest.mock import Mock
        from talos_core.cognition.conversation_activity import conversation_busy
        from run_talos import ProjectModel

        supervised = ProjectModel("fixture", self.model.server_port, True)
        supervised.ready_server = Mock()
        supervised.ready_server.poll.return_value = None
        supervised.voice_verified = True
        self.room.room.voice_generate = supervised.generate_voice
        with patch("run_talos.check_ollama_model", side_effect=AssertionError), \
                patch("talos_core.room.room_service.check_ollama_model", side_effect=AssertionError):
            response = self.start()
            self.assertIn("started", json.loads(response.readline()))
            self.assertIn("sentence", json.loads(response.readline()))
            self.assertTrue(conversation_busy(self.root, grace=0))
            self.model.release.set()
            events = [json.loads(line) for line in response]
            self.assertIn("result", events[-1])
            self.assertFalse(conversation_busy(self.root, grace=0))

    def test_first_synthesized_clip_before_model_eos_and_final_context(self):
        response = self.start()
        self.assertEqual(json.loads(response.readline()), {"started": True})
        first = json.loads(response.readline())["sentence"]
        clip = json.load(self.post("voice", {"text": first}))
        with wave.open(io.BytesIO(base64.b64decode(clip["wav"]))) as wav:
            self.assertTrue(any(wav.readframes(wav.getnframes())))
        self.assertFalse(self.model.ended.is_set())
        self.model.release.set()
        self.assertEqual(json.loads(response.readline())["sentence"], "We welcome the blooms.")
        result = json.loads(response.readline())["result"]
        self.assertEqual(result["reply"], "Spring follows winter. We welcome the blooms.")
        self.assertTrue(result["context"])
        self.assertEqual(response.read(), b"")
        with closing(open_memory(self.room.room.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM transcripts").fetchone()[0], 2)

    def test_interrupt_unblocks_stalled_generation_and_leaves_no_unheard_exchange(self):
        response = self.start()
        self.assertTrue(json.loads(response.readline())["started"])
        self.assertIn("sentence", json.loads(response.readline()))
        receipt = json.load(self.post("voice/stream/cancel", {"request_id": self.request_id}))
        self.assertTrue(receipt["cancelled"])
        self.assertTrue(self.model.disconnected.wait(1))
        self.assertEqual(response.read(), b"")
        self.assertIsNone(self.room.room.facing_exchange)
        with closing(open_memory(self.room.room.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM transcripts").fetchone()[0], 0)

    def test_cancel_during_retried_generation_does_not_publish_a_late_answer(self):
        self.model.first = ['["I sent your message."']
        response = self.start()
        self.assertTrue(json.loads(response.readline())["started"])
        self.assertEqual(json.loads(response.readline())["sentence"], "Spring follows winter.")
        self.assertEqual(len(self.model.requests), 2)
        self.assertTrue(json.load(self.post("voice/stream/cancel", {"request_id": self.request_id}))["cancelled"])
        self.assertEqual(response.read(), b"")
        self.assertIsNone(self.room.room.facing_exchange)

    def test_action_offer_stays_atomic_and_requires_confirmation(self):
        response = self.start("Remind me in five minutes to water the plants")
        result = json.loads(response.readline())["result"]
        self.assertIn("offer", result)
        self.assertEqual(response.read(), b"")
        self.assertEqual(self.model.requests, [])

    def test_invalid_first_sentence_retries_before_emitting_audio(self):
        self.model.first = ['["I picked up the umbrella."']
        self.model.release.set()
        response = self.start()
        self.assertTrue(json.loads(response.readline())["started"])
        self.assertEqual(json.loads(response.readline()), {"sentence": "Spring follows winter."})
        self.assertIn("sentence", json.loads(response.readline()))
        self.assertIn("result", json.loads(response.readline()))
        self.assertEqual(len(self.model.requests), 2)

    def test_unverified_message_receipt_is_never_spoken(self):
        self.model.first = ['["I sent your message."']
        self.model.release.set()
        response = self.start()
        self.assertTrue(json.loads(response.readline())["started"])
        self.assertEqual(json.loads(response.readline()), {"sentence": "Spring follows winter."})
        self.assertIn("sentence", json.loads(response.readline()))
        self.assertIn("result", json.loads(response.readline()))

    def test_complete_sentences_and_escapes(self):
        events = []
        stream = SpokenStream(events.append)
        chunks = iter(['["The example', ' is",', '"a quoted ', '\\"word\\".",', '"So it ends"]'])
        def tokens(*args):
            yield from chunks
        with patch.object(stream,"tokens",tokens):
            result = stream.reply([], "question", "fixture", lambda _: True)
        self.assertEqual(result, 'The example is a quoted "word". So it ends')
        self.assertEqual(events, [{"started":True},{"sentence":'The example is a quoted "word".'},{"sentence":"So it ends"}])

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS native controller")
    def test_native_first_clip_interrupt_and_replacement_turn(self):
        root = Path(__file__).resolve().parents[1]
        binary = self.root / "spoken-replay"
        subprocess.run(["swiftc", "-swift-version", "5", "-D", "TALOS_CONTROLLER_REPLAY", "-parse-as-library",
                        "-module-cache-path", str(self.root / "swift-cache"), *sorted((root / "mac").glob("*.swift")),
                        root / "tests/NativeSpokenReplay.swift", "-o", binary], check=True, timeout=120)
        token = self.root / "token"
        token.write_text(self.room.token)
        subprocess.run([binary, "--room-trial", "--port", str(self.room.server_port),
                        "--token-file", token, self.root], check=True, timeout=20)
        self.assertTrue(self.model.disconnected.wait(1))
        self.assertFalse(self.model.release.is_set())

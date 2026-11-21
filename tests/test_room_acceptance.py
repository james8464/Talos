"""Exercise the production HTTP handler over connected sockets and a fresh room DB."""

import base64
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from room_service import Handler, Room
from room_voice import DEFAULT_VOICE, KokoroVoice


class RoomAcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "room.sqlite3"
        self.room = Room(self.path, "qwen3:4b", online=False)

    def request(self, path, body=None, *, expected_error=None):
        client, server = socket.socketpair()
        def serve():
            with server:
                Handler(server, ("local", 0), SimpleNamespace(room=self.room, token="fixture"))
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        with closing(http.client.HTTPConnection("localhost")) as connection:
            connection.sock = client
            client.settimeout(145)
            connection.request("GET" if body is None else "POST", path,
                         body=None if body is None else json.dumps(body).encode(),
                         headers={"Authorization": "Bearer fixture", "Content-Type": "application/json"})
            with connection.getresponse() as response:
                payload = json.load(response)
                if expected_error:
                    self.assertEqual(response.status, expected_error, payload)
                else:
                    self.assertLess(response.status, 300, payload)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        return payload

    def test_reminder_survives_restart_and_waits_for_deadline(self):
        start = datetime.now(UTC)
        receipt = self.request("/v1/turn", {"text": "Talos, remind me in one minute to stretch.", "source": "voice"})
        self.assertEqual((receipt["tool"], receipt["action"]), ("set_reminder", "applied"))
        self.room = Room(self.path, "qwen3:4b", online=False)
        item, = self.request("/v1/inbox")["items"]
        self.assertEqual((item["id"], item["text"]), (receipt["id"], "stretch"))
        seconds = (datetime.fromisoformat(item["due_at"]) - start).total_seconds()
        self.assertAlmostEqual(seconds, 60, delta=1)
        self.assertIsNone(self.request("/v1/speech")["item"])

    def test_direct_task_and_reminder_clarification_without_model(self):
        self.room.model_state = lambda: "warming up"
        def speak(text):
            return self.request("/v1/turn", {"text": text, "source": "voice"})
        task = speak("Could you add buy paper to my to-do list?")
        self.assertEqual((task["tool"], task["action"]), ("create_task", "applied"))
        self.assertIn("message field", speak("What is on my to-do list?")["reply"])
        listing = self.request("/v1/turn", {"text": "List my tasks."})
        self.assertIn("buy paper", listing["reply"])
        clarification = speak("Please remind me to take out the recycling.")
        self.assertIn("How long", clarification["reply"])
        self.assertEqual(len(self.request("/v1/inbox")["items"]), 1)
        self.request("/v1/turn", {"text": "in five minutes"}, expected_error=503)
        self.assertIn("whole number", speak("In zero minutes.")["reply"])
        self.assertEqual(speak("In fifteen minutes.")["tool"], "set_reminder")
        speak("Remind me to remove the label.")
        self.assertIn("haven't set", speak("Never mind.")["reply"])
        items = self.request("/v1/inbox")["items"]
        self.assertEqual([(i["kind"], i["text"]) for i in items],
                         [("task", "buy paper"), ("reminder", "take out the recycling")])
        self.assertAlmostEqual((datetime.fromisoformat(items[1]["due_at"]) -
                                datetime.now(UTC)).total_seconds(), 900, delta=2)

    def test_service_works_during_model_recovery(self):
        self.room.model_state = lambda: "warming up"
        meta = self.request("/v1/status")
        self.assertEqual(meta["model_state"], "warming up")
        turn = self.request("/v1/turn", {"source": "voice", "text": "Can you hear me?"})
        self.assertIn("hear you", turn["reply"])
        receipt = self.request("/v1/turn", {"source": "voice", "text": "Remind me in 3 hours to stretch."})
        self.assertEqual(receipt["action"], "applied")
        error = self.request("/v1/turn", {"text": "How do batteries work?"}, expected_error=503)
        self.assertIn("warming up", error["error"])
        event = self.request("/v1/events", {"kind": "task_update", "summary": "A task is overdue."})
        self.assertFalse(self.room.step())
        self.assertEqual(self.request("/v1/events/" + str(event["event_id"]))["attempts"], 0)

    @unittest.skipUnless(os.environ.get("TALOS_TEST_REAL_MINUTE") == "1", "one-minute real-time delivery test")
    def test_due_reminder_defers_in_empty_room_then_delivers_once(self):
        self.request("/v1/presence", {"occupied": False})
        receipt = self.request("/v1/turn", {"text": "Remind me in 1 minute to stretch.", "source": "voice"})
        self.room = Room(self.path, "qwen3:4b", online=False)
        item, = self.request("/v1/inbox")["items"]
        due = datetime.fromisoformat(item["due_at"])
        while datetime.now(UTC) < due:
            time.sleep(min(1.0, max(0, (due - datetime.now(UTC)).total_seconds())))
        self.assertIsNone(self.request("/v1/speech")["item"])
        self.request("/v1/presence", {"occupied": True})
        self.assertEqual(self.request("/v1/speech")["item"]["id"], receipt["id"])
        for _ in range(2):
            self.request("/v1/speech/ack", {"id": receipt["id"]})
        self.assertIsNone(self.request("/v1/speech")["item"])

    def check_native_endpoint(self, *files):
        root = Path(__file__).resolve().parents[1]
        binary = self.path.parent / "check-endpoint"
        subprocess.run(["swiftc", "-module-cache-path", str(self.path.parent / "swift-cache"),
                        str(root / "mac/VoiceEndpoint.swift"), str(root / "mac/MeetingBrief.swift"), str(root / "mac/VisitorMemory.swift"),
                        str(root / "tests/VoiceEndpointChecks.swift"),
                        "-o", str(binary)], check=True, timeout=120)
        subprocess.run([str(binary), *files], check=True, timeout=30)

    @unittest.skipUnless(sys.platform == "darwin", "requires Apple audio frameworks")
    def test_native_speech_boundaries(self):
        self.check_native_endpoint()

    @unittest.skipUnless(os.environ.get("TALOS_TEST_KOKORO") == "1", "requires installed Kokoro environment")
    def test_synthesizes_new_reply_to_playable_pcm(self):
        models = Path(__file__).resolve().parents[1] / "models" / "kokoro-room"
        self.room.voice = KokoroVoice(models, DEFAULT_VOICE)
        result = self.request("/v1/turn", {"text": "Remind me in two minutes to close the window.", "source": "voice"})
        audio = self.request("/v1/voice", {"text": result["reply"]})
        with wave.open(io.BytesIO(base64.b64decode(audio["wav"]))) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (1, 2))
            self.assertGreater(wav.getnframes(), wav.getframerate())
            self.assertTrue(any(wav.readframes(wav.getnframes())))
        if sys.platform == "darwin":
            fixture = self.path.parent / "synthetic-reminder.wav"
            fixture.write_bytes(base64.b64decode(audio["wav"]))
            self.check_native_endpoint(fixture)
        print(f"New Kokoro reminder reply synthesized in {audio['synthesis_ms']} ms.", flush=True)

    @unittest.skipUnless(os.environ.get("TALOS_TEST_OLLAMA") == "1", "requires running project Ollama")
    def test_real_model_answers_a_question_and_uses_conversation_context(self):
        first = self.request("/v1/turn", {"source": "voice", "text": "How many wheels are on a tricycle?"})
        self.assertRegex(first["reply"].lower(), r"\b(three|3)\b")
        second = self.request("/v1/turn", {"source": "voice", "text": "And on two of those combined?"})
        self.assertRegex(second["reply"].lower(), r"\b(six|6)\b")

    @unittest.skipUnless(os.environ.get("TALOS_TEST_OLLAMA") == "1", "requires the local conversational model")
    def test_local_conversation_keeps_a_correction(self):
        if os.environ.get("TALOS_TEST_KOKORO") == "1":
            models = Path(__file__).resolve().parents[1] / "models" / "kokoro-room"
            self.room.voice = KokoroVoice(models, DEFAULT_VOICE)
        context = None
        for words in ("I finally found my blue notebook behind the piano.",
                      "Actually, I meant the green notebook. The blue one is still missing.",
                      "Which notebook did I find, and where?"):
            start = time.monotonic()
            answer = self.request("/v1/voice/facing", {"text":words,"context":context})
            reply, context = answer["reply"], answer["context"]
            self.assertNotEqual(reply.lower().strip(".! "), words.lower().strip(".! "))
            reply_ms = round((time.monotonic() - start) * 1000)
            if self.room.voice:
                audio = self.request("/v1/voice", {"text":reply})
                with wave.open(io.BytesIO(base64.b64decode(audio["wav"]))) as wav:
                    self.assertGreater(wav.getnframes(), 0)
                print(f"Conversation: {reply!r} · text {reply_ms} ms · complete WAV {round((time.monotonic()-start)*1000)} ms", flush=True)
        self.assertIn("green",reply.casefold())
        self.assertIn("piano",reply.casefold())

    def test_latency_samples_are_bounded_and_idempotent(self):
        for elapsed in range(65):
            result = self.request("/v1/turn", {"source": "voice", "text": "Can you hear me?"})
            self.request("/v1/voice/latency", {"id": result["id"], "elapsed_ms": elapsed})
        self.request("/v1/voice/latency", {"id": result["id"], "elapsed_ms": 999})
        stats = self.request("/v1/status")["voice_latency"]
        self.assertEqual(stats, {"samples": 64, "median_ms": 32.5, "p95_ms": 61})
        typed = self.request("/v1/turn", {"text": "Can you see me?"})
        self.request("/v1/voice/latency", {"id": typed["id"], "elapsed_ms": 5}, expected_error=400)

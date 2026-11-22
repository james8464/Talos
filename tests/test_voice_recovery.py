import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from room_service import Room
import voice_recovery


class VoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.state / "room.db"
        self.room = Room(self.path, "fixture", online=False)

    def turn(self, text, path="/v1/turn", source="text"):
        return self.room.route("POST", path, {"text": text, "source": source})[1]

    def test_complaint_and_receipts_survive_restart(self):
        for source, path in (("text", "/v1/turn"), ("voice", "/v1/voice/facing")):
            self.assertIn("no recent", self.turn("Voice status", path, source)["reply"])
            result = self.turn(
                "Hey Talos, you’re having issues talking to me.", path, source
            )
            self.assertEqual(result["voice_recovery"], "test_output")
            self.assertGreater(result["incident_id"], 0)
        for value in ("kokoro_failed", "macos_started", "macos_finished"):
            self.assertEqual(
                self.room.route("POST", "/v1/voice/outcome", {"outcome": value}),
                (200, {"recorded": value}),
            )
        self.room = Room(self.path, "fixture", online=False)
        last = self.turn("Is your voice working?")
        self.assertIn("utterance finished", last["reply"])
        self.assertEqual(
            [r["outcome"] for r in last["voice_receipts"]],
            ["macos_finished", "macos_started", "kokoro_failed"],
        )
        self.assertNotIn("voice_recovery", last)

    def test_shadow_and_unrelated_text_never_trigger_probe(self):
        self.room.apply = False
        self.assertNotIn("voice_recovery", self.turn("I can't hear you"))
        self.assertEqual(
            self.room.route("POST", "/v1/voice/outcome", {"outcome": "kokoro_failed"})[
                0
            ],
            403,
        )
        with closing(sqlite3.connect(":memory:")) as db:
            for text in (
                "Alice can't hear you",
                "I can't hear you because I'm joking",
                "Your voice is lovely",
                "Tell me how to fix my microphone",
            ):
                self.assertIsNone(voice_recovery.handle(db, text, apply=True))
            self.assertEqual(voice_recovery.recent(db), [])
            self.assertEqual(
                db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0
            )

    def test_reports_accept_known_labels_only_and_remain_bounded(self):
        with closing(sqlite3.connect(":memory:")) as db:
            for payload in (
                {"outcome": "unknown"},
                {"outcome": "macos_started", "text": "private"},
                {},
            ):
                with self.assertRaises(ValueError):
                    voice_recovery.report(db, payload)
            for _ in range(40):
                voice_recovery.report(db, {"outcome": "macos_unavailable"})
            self.assertEqual(
                db.execute("SELECT count(*) FROM voice_output_receipts").fetchone()[0],
                32,
            )
            self.assertEqual(len(voice_recovery.recent(db)), 5)
        with patch(
            "self_diagnosis.report", side_effect=sqlite3.OperationalError("busy")
        ):
            self.assertEqual(
                self.turn("Test your voice")["voice_recovery"], "test_output"
            )

    @unittest.skipUnless(
        sys.platform == "darwin", "requires the native Mac audio frameworks"
    )
    def test_native_kokoro_failure_watchdog_and_cancellation(self):
        root = Path(__file__).resolve().parents[1]
        binary = self.state / "voice-recovery"
        subprocess.run(
            [
                "swiftc",
                "-swift-version",
                "5",
                "-D",
                "TALOS_CONTROLLER_REPLAY",
                "-parse-as-library",
                "-module-cache-path",
                str(self.state / "cache"),
                *sorted((root / "mac").glob("*.swift")),
                root / "tests/NativeVoiceRecoveryReplay.swift",
                "-o",
                binary,
            ],
            check=True,
            timeout=150,
        )
        subprocess.run([binary, "--room-trial"], check=True, timeout=20)

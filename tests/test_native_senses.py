"""Native RoomClient scene/sound methods against an isolated room."""

import base64
import io
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from talos_core.room.room_service import Handler, Room, create_token


@unittest.skipUnless(sys.platform == "darwin", "requires the native Mac app frameworks")
class NativeSenseReplay(unittest.TestCase):
    def test_native_client_scene_and_sound(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            binary = folder / "sense-replay"
            subprocess.run(
                [
                    "swiftc", "-swift-version", "5", "-D", "TALOS_CONTROLLER_REPLAY",
                    "-parse-as-library", "-module-cache-path", str(folder / "swift-cache"),
                    *sorted((root / "mac").glob("*.swift")),
                    root / "tests/NativeSenseReplay.swift", "-o", binary,
                ],
                check=True, timeout=120,
            )
            room = Room(folder / "room.sqlite", "local", speak=False, online=False)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.room = room
            server.token = create_token(folder / "room.token")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                frame = folder / "blank.png"
                Image.new("RGB", (320, 240), (20, 30, 40)).save(frame)
                audio = io.BytesIO()
                with wave.open(audio, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(24000)
                    wav.writeframes(b"\x00\x00" * 2400)
                result = subprocess.run(
                    [str(binary), str(server.server_port),
                     (folder / "room.token").read_text().strip(),
                     base64.b64encode(frame.read_bytes()).decode(),
                     base64.b64encode(audio.getvalue()).decode()],
                    capture_output=True, text=True, timeout=60,
                )
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("scene=The scene looks empty.", result.stdout)
            self.assertIn("sound=", result.stdout)


if __name__ == "__main__":
    unittest.main()

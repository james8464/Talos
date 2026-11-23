import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from talos_core.room.room_service import Handler, Room, create_token


@unittest.skipUnless(sys.platform == "darwin", "requires the native Mac app frameworks")
class NativeRoomFlow(unittest.TestCase):
    def test_native_controller_with_isolated_http_service(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            binary = folder / "replay"
            subprocess.run(
                [
                    "swiftc",
                    "-swift-version",
                    "5",
                    "-D",
                    "TALOS_CONTROLLER_REPLAY",
                    "-parse-as-library",
                    "-module-cache-path",
                    str(folder / "swift-cache"),
                    *sorted((root / "mac").glob("*.swift")),
                    root / "tests/NativeRoomReplay.swift",
                    "-o",
                    binary,
                ],
                check=True,
                timeout=120,
            )
            source = folder / "fixture.csv"
            source.write_text(
                "date,sender,text\n2026-11-18T10:00:00+00:00,James,I will send the forecast Thursday.\n"
            )
            with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                server.room = Room(
                    folder / "room.db",
                    "fixture",
                    online=False,
                    model_state=lambda: "warming",
                )
                server.token = create_token(folder / "room.token")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    subprocess.run(
                        [
                            binary,
                            "--room-trial",
                            "--port",
                            str(server.server_port),
                            "--token-file",
                            folder / "room.token",
                            source,
                        ],
                        check=True,
                        timeout=35,
                    )
                finally:
                    server.shutdown()
                    thread.join()

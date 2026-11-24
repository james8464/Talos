"""The vendored GUI bridge serves files and proxies chat to the room."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from talos_core.interfaces import gui_bridge


class GuiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "index.html").write_text("<title>Talos GUI</title>", encoding="utf-8")
        (self.root / "app.js").write_text("console.log('ok')", encoding="utf-8")
        self.server = gui_bridge.build_server(self.state, root=self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def post(self, payload):
        request = urllib.request.Request(
            self.base + "/api/turn", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_serves_index_and_assets(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Talos GUI", body)
        status, body = self.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("console.log", body)

    def test_health_and_spa_fallback(self):
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, body = self.get("/missing/route")
        self.assertEqual(status, 200)
        self.assertIn("Talos GUI", body)

    def test_path_traversal_is_refused(self):
        status, body = self.get("/..%2f..%2fetc%2fpasswd")
        self.assertEqual(status, 404)
        self.assertNotIn("root:", body)

    def test_turn_is_proxied_without_exposing_the_token(self):
        with patch.object(gui_bridge, "_room_turn",
                          return_value={"reply": "hello", "action": None, "tool": "none"}) as turn:
            status, body = self.post({"text": "hi"})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "hello")
        turn.assert_called_once_with("hi", self.state)

    def test_invalid_turn_requests_fail_closed(self):
        self.assertEqual(self.post({})[0], 400)
        self.assertEqual(self.post({"text": " "})[0], 400)
        self.assertEqual(self.post({"text": "x" * (gui_bridge.MAX_TEXT + 1)})[0], 400)

    def test_room_failure_is_reported(self):
        with patch.object(gui_bridge, "_room_turn",
                          side_effect=gui_bridge.GuiBridgeError("room down")):
            status, body = self.post({"text": "hi"})
        self.assertEqual(status, 502)
        self.assertIn("room down", body["error"])

    def test_missing_bundle_reports_a_build_hint(self):
        empty = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(gui_bridge, "dist", return_value=empty):
            with self.assertRaises(gui_bridge.GuiBridgeError) as caught:
                gui_bridge.serve(self.state, open_browser=False)
        self.assertIn("npm run build", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

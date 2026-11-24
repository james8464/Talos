"""One-shot room client used by `talos ask`."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from talos_core.interfaces import room_client


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload


class RoomClientTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.state / "room.token").write_text("secret\n", encoding="utf-8")

    def test_turn_sends_the_question_and_returns_the_payload(self):
        with patch("talos_core.interfaces.room_client.urllib.request.urlopen",
                   return_value=FakeResponse({"reply": "hello"})) as open_url:
            result = room_client.turn(self.state, "hi")
        self.assertEqual(result, {"reply": "hello"})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8765/v1/turn")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    def test_missing_token_is_reported_without_leaking_anything(self):
        (self.state / "room.token").unlink()
        with self.assertRaises(room_client.RoomClientError) as caught:
            room_client.turn(self.state, "hi")
        self.assertIn("No room token", str(caught.exception))

    def test_empty_and_oversized_questions_are_refused(self):
        with self.assertRaises(room_client.RoomClientError):
            room_client.turn(self.state, "   ")
        with self.assertRaises(room_client.RoomClientError):
            room_client.turn(self.state, "x" * (room_client.MAX_TEXT + 1))

    def test_http_and_connection_failures_are_clear(self):
        error = urllib.error.HTTPError("http://x", 500, "boom", {}, None)
        with patch("talos_core.interfaces.room_client.urllib.request.urlopen",
                   side_effect=error):
            with self.assertRaises(room_client.RoomClientError) as caught:
                room_client.turn(self.state, "hi")
        self.assertIn("HTTP 500", str(caught.exception))
        with patch("talos_core.interfaces.room_client.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("down")):
            with self.assertRaises(room_client.RoomClientError):
                room_client.turn(self.state, "hi")


if __name__ == "__main__":
    unittest.main()

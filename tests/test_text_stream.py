"""Silent token streaming."""

import json
import unittest
from unittest.mock import patch

from talos_core.runtime import text_stream


class FakeResponse:
    status = 200

    def __init__(self, lines):
        self.lines = [line.encode() for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def readline(self, limit):
        return self.lines.pop(0) if self.lines else b""


class FakeConnection:
    def __init__(self, *args, **kwargs):
        self.response = FakeResponse([
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ])
        self.payload = None

    def request(self, method, path, body=None, headers=None):
        self.payload = json.loads(body)

    def getresponse(self):
        return self.response

    def close(self):
        pass


class TextStreamTests(unittest.TestCase):
    def test_streams_deltas_from_ollama(self):
        connection = FakeConnection()
        with patch("talos_core.runtime.text_stream.http.client.HTTPConnection",
                   return_value=connection):
            chunks = list(text_stream.stream_reply("hi"))
        self.assertEqual(chunks, ["Hello", " world"])
        self.assertEqual(connection.payload["model"], text_stream.DEFAULT_MODEL)
        self.assertTrue(connection.payload["stream"])
        self.assertFalse(connection.payload["think"])

    def test_empty_prompt_is_refused(self):
        with self.assertRaises(ValueError):
            list(text_stream.stream_reply("  "))

    def test_error_chunk_is_clean(self):
        class ErrorConnection(FakeConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.response = FakeResponse([json.dumps({"error": "model missing"})])
        with patch("talos_core.runtime.text_stream.http.client.HTTPConnection",
                   ErrorConnection):
            with self.assertRaises(RuntimeError) as caught:
                list(text_stream.stream_reply("hi"))
        self.assertIn("model missing", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

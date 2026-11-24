"""Tests for the optional OpenCode Go bridge. No real key is needed."""

import json
import unittest
from unittest.mock import patch

from talos_core.cognition import cloud_model


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class CloudModelTests(unittest.TestCase):
    def test_complete_sends_only_prompt_and_no_private_context(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"choices": [{"message": {"content": "Cloud answer."}}]})

        with patch.object(cloud_model, "api_key", return_value="test-key"):
            reply = cloud_model.complete(
                "Explain gravity.", system="Be brief.", max_tokens=120, opener=opener,
            )
        self.assertEqual(reply, "Cloud answer.")
        request = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "deepseek-v4.1-flash")
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Explain gravity."},
        ])
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertIn("opencode/", request.get_header("User-agent"))
        self.assertTrue(request.get_header("X-opencode-session"))

    def test_missing_key_fails_before_network(self):
        with patch.object(cloud_model, "api_key", return_value=""):
            with self.assertRaises(cloud_model.CloudModelError):
                cloud_model.complete("hello")

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(ValueError):
            cloud_model.complete("  ")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core import codex_runtime, connectors


class ConnectorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_slack_and_discord_configuration_and_send(self):
        connectors.configure(self.state, "slack", "https://hooks.slack.test/x")
        connectors.configure(self.state, "discord", "https://discord.test/x")
        calls = []
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response()
        self.assertEqual(connectors.send(self.state, "slack", "hello", opener=opener)["status"], 204)
        self.assertEqual(connectors.send(self.state, "discord", "hello", opener=opener)["status"], 204)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(ValueError):
            connectors.configure(self.state, "slack", "http://insecure.test")
        with self.assertRaises(ValueError):
            connectors.send(self.state, "other", "x", opener=opener)

    def test_codex_runtime_requires_confirmation_and_uses_read_only(self):
        calls = []
        def runner(argv, **options):
            calls.append((argv, options))
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with patch("talos_core.codex_runtime.shutil.which", return_value="/usr/bin/codex"), \
             self.assertRaises(codex_runtime.RuntimeRefused):
            codex_runtime.run("review", confirm=False, runner=runner)
        with patch("talos_core.codex_runtime.shutil.which", return_value="/usr/bin/codex"):
            result = codex_runtime.run("review", confirm=True, runner=runner)
        self.assertTrue(result["ok"])
        self.assertIn("--sandbox", calls[0][0])
        self.assertIn("read-only", calls[0][0])


if __name__ == "__main__":
    unittest.main()

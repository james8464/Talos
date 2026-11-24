import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from talos_core import cli, commands, moa, session_auth, webhook


class HermesRemainderTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_login_logout_and_command_import(self):
        token = session_auth.login(self.state)
        self.assertGreater(len(token), 20)
        self.assertEqual((self.state / session_auth.FILE).stat().st_mode & 0o777, 0o600)
        self.assertTrue(session_auth.logout(self.state))
        self.assertFalse(session_auth.logout(self.state))
        commands.import_command(self.state, "review", ["/usr/bin/echo", "review"])
        self.assertEqual(commands.resolve(self.state, "review", "x"), ["/usr/bin/echo", "review", "x"])
        with self.assertRaises(ValueError):
            commands.resolve(self.state, "missing")

    def test_webhook_signing_and_moa_choice(self):
        webhook.configure(self.state, "https://example.test/hook", "0123456789abcdef")
        body, signature = webhook.payload({"kind": "test"}, state=self.state)
        self.assertIn(b"test", body)
        self.assertEqual(len(signature), 64)
        result = moa.choose(["short", "the deployment is blocked by a test failure"], keywords=["blocked"])
        self.assertEqual(result["index"], 1)
        with self.assertRaises(ValueError):
            moa.choose([])

    def test_cli_login_import_webhook_moa_and_pets(self):
        for args in (["login"], ["logout"], ["import_cmd"], ["webhook"], ["pets"]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main([*args, "--state", str(self.state)])
            self.assertEqual(code, 0, args)
            self.assertTrue(out.getvalue().strip())
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["moa", "--response", "one", "--response", "two two two two"])
        self.assertEqual(code, 0)
        self.assertIn("index", out.getvalue())


if __name__ == "__main__":
    unittest.main()

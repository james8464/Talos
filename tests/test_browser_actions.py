"""Room-facing browser commands without launching a browser."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import browser_actions


class FakeSnapshot:
    url = "https://example.com/page"
    title = "Example Page"
    text = "A short page body."
    truncated = False


class FakePage:
    def snapshot(self):
        return FakeSnapshot()

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return path


class FakeBrowser:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def open_page(self, url):
        return FakePage()


class BrowserActionsTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        patch.object(browser_actions, "available", return_value=True).start()
        self.addCleanup(patch.stopall)

    def test_reads_and_summarizes_a_page(self):
        with patch.object(browser_actions, "Browser", FakeBrowser):
            reply = browser_actions.command("Read https://example.com/page.", self.state)
        self.assertIn("Example Page", reply)
        self.assertIn("A short page body.", reply)

    def test_screenshot_is_private_and_reported(self):
        with patch.object(browser_actions, "Browser", FakeBrowser):
            reply = browser_actions.command("Screenshot https://example.com/page.", self.state)
        path = Path(reply.split("saved to ", 1)[1])
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.resolve(), (self.state / "screenshots").resolve())

    def test_only_http_urls_are_accepted(self):
        for text in ("Read file:///etc/passwd", "Read example.com", "Open Safari."):
            self.assertIsNone(browser_actions.command(text, self.state))

    def test_unavailable_browser_is_reported(self):
        with patch.object(browser_actions, "available", return_value=False):
            reply = browser_actions.command("Read https://example.com", self.state)
        self.assertIn("unavailable", reply.casefold())


if __name__ == "__main__":
    unittest.main()

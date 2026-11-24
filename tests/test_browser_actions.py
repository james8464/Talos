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
    def __init__(self):
        self.clicked = False

    def snapshot(self):
        if self.clicked:
            class After:
                url = "https://example.com/page"
                title = "Example Page"
                text = "clicked ok"
                truncated = False
            return After()
        return FakeSnapshot()

    def eval_js(self, script):
        self.clicked = True
        return {"ok": True, "label": "Run check"}

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return path


class FakeBrowser:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.page = FakePage()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def open_page(self, url):
        return self.page


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

    def test_clicks_visible_text_and_returns_the_result(self):
        with patch.object(browser_actions, "Browser", FakeBrowser):
            reply = browser_actions.command("Click Run check on https://example.com/page.",
                                            self.state)
        self.assertIn("Clicked Run check", reply)
        self.assertIn("clicked ok", reply)

    def test_types_into_a_visible_field(self):
        class TypedPage(FakePage):
            def eval_js(self, script):
                return {"ok": True, "label": "search"}

            def snapshot(self):
                class After:
                    url = "https://example.com"
                    title = "Example"
                    text = "typed talos"
                    truncated = False
                return After()

        class TypedBrowser(FakeBrowser):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.page = TypedPage()

        with patch.object(browser_actions, "Browser", TypedBrowser):
            reply = browser_actions.command(
                'Type talos into the search box on https://example.com.', self.state)
        self.assertIn("typed talos", reply)
        self.assertIn("search", reply)

    def test_searches_a_form_and_returns_results(self):
        class SearchPage(FakePage):
            def eval_js(self, script):
                return {"ok": True}

            def snapshot(self):
                class After:
                    url = "https://example.com/results"
                    title = "Results"
                    text = "results for talos"
                    truncated = False
                return After()

        class SearchBrowser(FakeBrowser):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.page = SearchPage()

        with patch.object(browser_actions, "Browser", SearchBrowser):
            reply = browser_actions.command("Search talos on https://example.com.",
                                            self.state)
        self.assertIn("results for talos", reply)
        self.assertIn("Results", reply)

    def test_only_http_urls_are_accepted(self):
        for text in ("Read file:///etc/passwd", "Read example.com", "Open Safari."):
            self.assertIsNone(browser_actions.command(text, self.state))

    def test_unavailable_browser_is_reported(self):
        with patch.object(browser_actions, "available", return_value=False):
            reply = browser_actions.command("Read https://example.com", self.state)
        self.assertIn("unavailable", reply.casefold())


if __name__ == "__main__":
    unittest.main()

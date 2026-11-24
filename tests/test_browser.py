"""Tests for the Chrome DevTools Protocol browser adapter.

Everything runs against a local ``http.server`` fixture on 127.0.0.1; no
external network is used. When no browser transport exists the browser-backed
class skips with the adapter's own explanation instead of failing.
"""

import contextlib
import functools
import http.server
import json
import shutil
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from talos_core.mac import browser as web

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Talos Fixture</title>
  <style>
    body { font-family: sans-serif; margin: 20px; }
    #hidden-btn { display: none; }
    #tall { height: 1600px; }
  </style>
</head>
<body>
  <h1 id="heading">Fixture heading</h1>
  <button id="press" onclick="document.getElementById('output').textContent = 'clicked'">Click me</button>
  <div id="output">idle</div>
  <button id="hidden-btn" onclick="document.getElementById('hidden-output').textContent = 'hidden clicked'">Hidden</button>
  <div id="hidden-output">hidden idle</div>
  <label for="name">Name</label>
  <input id="name" name="name" value="hunter2"
         oninput="document.getElementById('typed').textContent =
                  String(Number(document.getElementById('typed').textContent) + 1)">
  <div id="typed">0</div>
  <div id="inert">not editable</div>
  <a id="next" href="/second.html">Second page</a>
  <div id="tall">spacer</div>
</body>
</html>
"""

SECOND_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Second Page</title></head>
<body><h1 id="second-heading">Second page heading</h1><a id="back" href="/index.html">Back</a></body>
</html>
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def fixture_site():
    root = tempfile.mkdtemp(prefix="talos-browser-fixture-")
    Path(root, "index.html").write_text(INDEX_HTML)
    Path(root, "second.html").write_text(SECOND_HTML)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=root)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(root, ignore_errors=True)


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except web.BrowserError:
            pass
        time.sleep(0.05)
    return False


class ValidationTests(unittest.TestCase):
    """Pure validation; these run with or without a browser."""

    def test_http_and_https_urls_are_accepted_and_trimmed(self):
        self.assertEqual(web.validate_url("http://127.0.0.1:8000/a?b=1"), "http://127.0.0.1:8000/a?b=1")
        self.assertEqual(web.validate_url("  https://example.com/x  "), "https://example.com/x")

    def test_non_http_schemes_are_refused_with_a_reason(self):
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
            "ftp://example.com/x",
            "chrome://settings",
            "about:blank",
        ):
            with self.subTest(url=url), self.assertRaises(web.BrowserPolicyError) as caught:
                web.validate_url(url)
            self.assertIn("only http and https", str(caught.exception))

    def test_malformed_urls_are_refused(self):
        for url in ("", "   ", "http://", "http://user:secret@127.0.0.1/x", "http://127.0.0.1/\nX"):
            with self.subTest(url=url), self.assertRaises(web.BrowserPolicyError):
                web.validate_url(url)
        with self.assertRaises(web.BrowserPolicyError):
            web.validate_url(None)

    def test_selectors_accept_common_css_forms(self):
        for selector in ("#id", ".class", "a[href='/x']", "div > p", "input[name='q']", "li:nth-child(2)"):
            with self.subTest(selector=selector):
                self.assertEqual(web.validate_selector(selector), selector)

    def test_selectors_reject_empty_oversized_and_injectable_input(self):
        for selector in ("", "   ", "a" * (web.SELECTOR_MAX + 1), "div;alert(1)", "a\nb", "a{}", "x\x00y"):
            with self.subTest(selector=selector), self.assertRaises(web.SelectorError):
                web.validate_selector(selector)
        with self.assertRaises(web.SelectorError):
            web.validate_selector(42)

    def test_unavailable_binary_reports_a_clear_message(self):
        missing = "/definitely/not/a/browser"
        status = web.availability(missing)
        self.assertFalse(status.available)
        self.assertEqual(status.channel, "none")
        self.assertIn(missing, status.message)
        with self.assertRaises(web.BrowserUnavailable) as caught:
            web.Browser(executable=missing).open_page("http://127.0.0.1:1/")
        self.assertIn(missing, str(caught.exception))

    def test_available_is_a_bool_and_availability_explains_itself(self):
        status = web.availability()
        self.assertIsInstance(status.available, bool)
        self.assertTrue(status.message)
        self.assertEqual(web.available(), status.available)
        if status.available:
            self.assertIn(status.channel, {"cdp", "safari-applescript"})


class BrowserTests(unittest.TestCase):
    """Live CDP tests against the local fixture."""

    @classmethod
    def setUpClass(cls):
        cls._site = fixture_site()
        cls.base = cls._site.__enter__()
        cls.browser = web.Browser(headless=True, timeout=8.0)
        if not cls.browser.available():
            cls._site.__exit__(None, None, None)
            raise unittest.SkipTest(cls.browser.status.message)
        try:
            cls.page = cls.browser.open_page(cls.base + "/index.html")
        except web.BrowserUnavailable as error:
            cls.browser.close()
            cls._site.__exit__(None, None, None)
            raise unittest.SkipTest(str(error)) from error

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._site.__exit__(None, None, None)

    def open_page(self):
        page = self.browser.open_page(self.base + "/index.html")
        self.addCleanup(page.close)
        return page

    def setUp(self):
        # Every test gets a fresh page so DOM mutations cannot leak between them.
        self.page = self.open_page()

    def test_snapshot_reports_url_title_text_and_elements(self):
        snapshot = self.page.snapshot()
        self.assertTrue(snapshot.url.endswith("/index.html"))
        self.assertEqual(snapshot.title, "Talos Fixture")
        self.assertEqual(snapshot.ready_state, "complete")
        self.assertIn("Fixture heading", snapshot.text)
        self.assertIn("Click me", snapshot.text)
        self.assertFalse(snapshot.truncated)
        inputs = [item for item in snapshot.elements if item["tag"] == "input"]
        self.assertEqual(inputs[0]["name"], "name")
        self.assertTrue(inputs[0]["rendered"])
        self.assertTrue(any(item["tag"] == "a" for item in snapshot.elements))

    def test_snapshot_never_exposes_field_values(self):
        snapshot = self.page.snapshot(include_html=False)
        self.assertNotIn("hunter2", json.dumps(snapshot.to_dict()))

    def test_text_supports_page_and_selector_scope(self):
        self.assertIn("Fixture heading", self.page.text())
        self.assertEqual(self.page.text("#heading"), "Fixture heading")
        self.assertIn("not editable", self.page.text("#inert"))

    def test_click_uses_trusted_mouse_events(self):
        receipt = self.page.click("#press")
        self.assertEqual(receipt, {"selector": "#press", "method": "mouse"})
        self.assertEqual(self.page.eval_js("document.getElementById('output').textContent"), "clicked")
        self.assertIn("clicked", self.page.text("#output"))

    def test_click_falls_back_to_js_for_unrendered_elements(self):
        receipt = self.page.click("#hidden-btn")
        self.assertEqual(receipt["method"], "js")
        self.assertEqual(self.page.text("#hidden-output"), "hidden clicked")

    def test_click_follows_a_link_and_navigates(self):
        page = self.page
        page.click("#next")
        self.assertTrue(wait_until(lambda: page.url.endswith("/second.html")))
        page.wait_for("#second-heading")
        snapshot = page.snapshot()
        self.assertEqual(snapshot.title, "Second Page")
        self.assertIn("Second page heading", snapshot.text)

    def test_type_replaces_existing_value_and_fires_input_events(self):
        receipt = self.page.type("#name", "Ada Lovelace ✓")
        self.assertEqual(receipt, {"selector": "#name", "chars": len("Ada Lovelace ✓"), "cleared": True})
        self.assertNotIn("Ada", json.dumps(receipt))
        self.assertEqual(self.page.eval_js("document.querySelector('#name').value"), "Ada Lovelace ✓")
        self.assertEqual(self.page.text("#typed"), "1")

    def test_type_can_append_without_clearing(self):
        self.page.type("#name", "Ada")
        self.page.type("#name", " Lovelace", clear=False)
        self.assertEqual(self.page.eval_js("document.querySelector('#name').value"), "Ada Lovelace")

    def test_type_refuses_elements_that_cannot_take_focus(self):
        with self.assertRaises(web.BrowserError) as caught:
            self.page.type("#inert", "nope")
        self.assertIn("cannot take focus", str(caught.exception))

    def test_eval_js_returns_json_values(self):
        self.assertEqual(self.page.eval_js("document.title"), "Talos Fixture")
        self.assertEqual(
            self.page.eval_js("({name: 'talos', items: [1, 2, 3], ok: true})"),
            {"name": "talos", "items": [1, 2, 3], "ok": True},
        )
        self.assertIsNone(self.page.eval_js("undefined"))
        self.assertEqual(self.page.eval_js("1 + 1"), 2)

    def test_eval_js_surfaces_page_errors_and_rejects_junk(self):
        with self.assertRaises(web.BrowserScriptError) as caught:
            self.page.eval_js("throw new Error('boom')")
        self.assertIn("boom", str(caught.exception))
        with self.assertRaises(web.BrowserScriptError):
            self.page.eval_js("   ")
        with self.assertRaises(web.BrowserScriptError):
            self.page.eval_js("x" * (web.SCRIPT_MAX + 1))

    def test_screenshot_writes_a_private_png(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = self.page.screenshot(directory / "shot.png")
        data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        width, height = png_size(data)
        self.assertGreaterEqual(width, 320)
        self.assertGreaterEqual(height, 200)

    def test_full_page_screenshot_covers_scrolled_content(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        viewport = self.page.screenshot(directory / "viewport.png")
        full = self.page.screenshot(directory / "full.png", full_page=True)
        _, viewport_height = png_size(viewport.read_bytes())
        _, full_height = png_size(full.read_bytes())
        self.assertGreater(full_height, viewport_height)

    def test_screenshot_defaults_to_a_private_temp_file(self):
        path = self.page.screenshot()
        try:
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        finally:
            path.unlink(missing_ok=True)

    def test_wait_for_times_out_on_missing_elements(self):
        started = time.monotonic()
        with self.assertRaises(web.BrowserTimeout):
            self.page.wait_for("#does-not-exist", timeout=0.4)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_wait_for_rejects_malformed_css(self):
        with self.assertRaises(web.SelectorError) as caught:
            self.page.wait_for("div:::", timeout=1.0)
        self.assertIn("invalid selector", str(caught.exception))

    def test_snapshot_truncates_very_long_text(self):
        self.page.eval_js("document.body.innerText = 'x'.repeat(20000)")
        snapshot = self.page.snapshot(include_elements=False)
        self.assertTrue(snapshot.truncated)
        self.assertEqual(len(snapshot.text), web.TEXT_MAX)

    def test_snapshot_can_include_html(self):
        snapshot = self.page.snapshot(include_html=True, include_elements=False)
        self.assertIn("<h1 id=\"heading\">", snapshot.html)

    def test_closed_page_refuses_further_work(self):
        page = self.page
        page.close()
        page.close()
        self.assertTrue(page.closed)
        with self.assertRaises(web.BrowserError):
            page.text()

    def test_browser_level_convenience_methods_delegate_to_active_page(self):
        self.assertEqual(self.browser.text("#heading"), "Fixture heading")
        self.assertEqual(self.browser.snapshot().title, "Talos Fixture")
        self.browser.goto(self.base + "/second.html")
        self.assertTrue(wait_until(lambda: self.browser.snapshot().title == "Second Page"))
        self.assertIn("Second page heading", self.browser.text())

    def test_session_context_manager_shuts_the_browser_down(self):
        with web.session(headless=True, timeout=8.0) as browser:
            page = browser.open_page(self.base + "/index.html")
            self.assertEqual(page.snapshot().title, "Talos Fixture")
        self.assertIsNone(browser._transport)
        with self.assertRaises(web.BrowserError):
            browser.open_page(self.base + "/index.html")

    def test_close_removes_the_throwaway_profile(self):
        browser = web.Browser(headless=True, timeout=8.0)
        page = browser.open_page(self.base + "/index.html")
        self.assertEqual(page.snapshot().title, "Talos Fixture")
        profile = browser._transport._profile
        self.assertIsNotNone(profile)
        self.assertTrue(Path(profile).is_dir())
        browser.close()
        self.assertFalse(Path(profile).exists())
        self.assertIsNone(browser._transport)
        with self.assertRaises(web.BrowserError):
            browser.open_page(self.base + "/index.html")


if __name__ == "__main__":
    unittest.main()

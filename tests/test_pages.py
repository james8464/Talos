"""Native Pages document creation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import pages


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_script_escapes_and_contains_the_document_text(self):
        body = pages.script(Path("/tmp/a.pages"), 'Title "quoted"', "Line one\nLine two\\")
        self.assertIn('Title \\"quoted\\"', body)
        self.assertIn("Line two\\\\", body)
        self.assertIn('POSIX file "/tmp/a.pages"', body)

    def test_create_saves_a_private_document(self):
        target = None

        def fake_script(text, timeout=30):
            nonlocal target
            marker = 'POSIX file "'
            start = text.index(marker) + len(marker)
            end = text.index('"', start)
            target = Path(text[start:end])
            target.write_bytes(b"pages")

        with patch.object(pages, "available", return_value=True), \
             patch.object(pages, "_osascript", side_effect=fake_script):
            result = pages.create(self.state, "Title", "Body")
        path = Path(result["path"])
        self.assertEqual(path.suffix, ".pages")
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_pages_is_reported(self):
        with patch.object(pages, "available", return_value=False):
            with self.assertRaises(pages.PagesError):
                pages.create(self.state, "Title")

    def test_osascript_failure_is_reported(self):
        with patch.object(pages, "available", return_value=True), \
             patch.object(pages, "_osascript",
                          side_effect=pages.PagesError("not permitted")):
            with self.assertRaises(pages.PagesError):
                pages.create(self.state, "Title")


if __name__ == "__main__":
    unittest.main()

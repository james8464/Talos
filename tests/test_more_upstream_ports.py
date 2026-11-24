import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core import plugins
from talos_core.interfaces import theme
from talos_core.knowledge import pdf
from talos_core.security import redact


class UpstreamPortTests(unittest.TestCase):
    def test_theme_names_and_ansi_behavior(self):
        self.assertIn("cyan", theme.names())
        self.assertIn("Mono", theme.color("Mono", theme="mono"))
        self.assertEqual(theme.color("x", theme="mono"), "x")
        with self.assertRaises(ValueError):
            theme.color("x", theme="missing")

    def test_secret_redaction_finds_tokens_without_leaking_them(self):
        text = "Authorization: Bearer abcdefghijklmnop and key sk-abcdefghijklmnop"
        cleaned = redact.redact(text)
        self.assertNotIn("abcdefghijklmnop", cleaned)
        self.assertIn(redact.MARK, cleaned)
        self.assertIn("bearer", redact.findings(text))

    def test_pdf_extraction_is_bounded(self):
        source = Path(self.enterContext(tempfile.TemporaryDirectory())) / "doc.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        completed = SimpleNamespace(returncode=0, stdout="Page one\nPage two\n", stderr="")
        with patch.object(pdf.shutil, "which", return_value="/usr/bin/pdftotext"):
            self.assertIn("Page one", pdf.extract(source, runner=lambda *a, **k: completed))
        with self.assertRaises(ValueError):
            pdf.extract(source.with_suffix(".txt"))

    def test_plugin_manifests_are_discovered_but_never_executed(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        folder = state / "plugins"
        folder.mkdir()
        (folder / "one.json").write_text(json.dumps({
            "name": "Example", "command": "/usr/bin/true", "args": ["--safe"], "enabled": True,
        }))
        (folder / "bad.json").write_text("{not json")
        found = plugins.discover(state)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Example")
        self.assertEqual(found[0]["args"], ["--safe"])


if __name__ == "__main__":
    unittest.main()

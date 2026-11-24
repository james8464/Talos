"""Static guards for the no-fallback, silent-by-default native surface."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativeSilenceTests(unittest.TestCase):
    def test_native_app_has_no_system_voice_fallback(self):
        source = (ROOT / "mac" / "Talos.swift").read_text(encoding="utf-8")
        self.assertIn("silentMode", source)
        self.assertNotIn("speaker.speak", source)
        self.assertNotIn("macOS fallback", source)
        self.assertNotIn("Trying the macOS voice", source)
        self.assertIn("No fallback voice will play", source)

    def test_launcher_and_service_have_no_system_voice(self):
        self.assertNotIn("--system-voice", (ROOT / "run_talos.py").read_text(encoding="utf-8"))
        self.assertNotIn(
            "--system-voice", (ROOT / "tools" / "talos_service.py").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()

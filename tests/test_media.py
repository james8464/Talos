import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core import media


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_generation_requires_confirmation_and_command(self):
        with self.assertRaises(media.MediaRefused):
            media.generate("music", "calm piano", self.state / "a.wav")
        with self.assertRaises(media.MediaUnavailable):
            media.generate("music", "calm piano", self.state / "a.wav", confirm=True)
        with self.assertRaises(ValueError):
            media.generate("image", "x", self.state / "a.wav", confirm=True)

    def test_music_generate_and_video_generate_use_configured_commands(self):
        out = self.state / "clip.mp4"
        def runner(argv, **options):
            Path(argv[-1]).write_bytes(b"video")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.dict(os.environ, {"TALOS_VIDEO_COMMAND": "ffmpeg -i {prompt} {out}"}):
            result = media.generate("video", "input.png", out, confirm=True, runner=runner)
        self.assertTrue(result["exists"])
        self.assertEqual(result["bytes"], 5)

    def test_failed_command_is_reported(self):
        with patch.dict(os.environ, {"TALOS_MUSIC_COMMAND": "false {out}"}):
            with self.assertRaises(media.MediaUnavailable):
                media.generate("music", "x", self.state / "x.wav", confirm=True,
                               runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="bad"))


if __name__ == "__main__":
    unittest.main()

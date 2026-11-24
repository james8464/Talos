"""Offline scene detection wrapper."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talos_core.mac import scene


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.image = Path(self.enterContext(tempfile.TemporaryDirectory())) / "frame.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    def test_detect_parses_counts_and_describes_empty(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps(
            {"people": 0, "faces": 0, "window": 0, "empty": True}), stderr="")
        with patch.object(scene, "available", return_value=True), \
             patch.object(scene, "_binary", return_value=Path("/bin/true")), \
             patch("talos_core.mac.scene.subprocess.run", return_value=result):
            found = scene.detect(self.state, self.image)
        self.assertTrue(found["empty"])
        self.assertEqual(scene.describe(found), "The scene looks empty.")

    def test_people_are_counted(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps(
            {"people": 2, "faces": 1, "window": 2}), stderr="")
        with patch.object(scene, "available", return_value=True), \
             patch.object(scene, "_binary", return_value=Path("/bin/true")), \
             patch("talos_core.mac.scene.subprocess.run", return_value=result):
            found = scene.detect(self.state, self.image)
        self.assertEqual(scene.describe(found), "I see 2 people.")

    def test_bad_inputs_fail_cleanly(self):
        with patch.object(scene, "available", return_value=True), \
             patch.object(scene, "_binary", return_value=Path("/bin/true")):
            with self.assertRaises(scene.SceneError):
                scene.detect(self.state, self.image.with_suffix(".txt"))
        with patch.object(scene, "available", return_value=False):
            with self.assertRaises(scene.SceneError):
                scene.detect(self.state, self.image)

    def test_vision_failure_is_reported(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="no image")
        with patch.object(scene, "available", return_value=True), \
             patch.object(scene, "_binary", return_value=Path("/bin/true")), \
             patch("talos_core.mac.scene.subprocess.run", return_value=result):
            with self.assertRaises(scene.SceneError) as caught:
                scene.detect(self.state, self.image)
        self.assertIn("no image", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

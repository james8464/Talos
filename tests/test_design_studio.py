"""Design workflows: vector posters and OpenSCAD nameplates."""

import tempfile
import unittest
from pathlib import Path

from talos_core import design_studio


class DesignStudioTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_poster_is_safe_svg(self):
        result = design_studio.create_poster(self.state, "Talos < & >", "Subtitle")
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("&lt;", text)
        self.assertNotIn("< & >", text)
        self.assertEqual(result["kind"], "poster-svg")

    def test_nameplate_writes_source_and_stl_when_openscad_exists(self):
        result = design_studio.create_nameplate(self.state, "Talos", width=40, height=20)
        source = Path(result["path"])
        self.assertIn('text("Talos"', source.read_text(encoding="utf-8"))
        if design_studio.OPENSCAD.is_file():
            self.assertIsNotNone(result["render"])
            self.assertTrue(Path(result["render"]).is_file())

    def test_command_routes_poster_and_nameplate(self):
        poster = design_studio.command("Design a poster titled Library Hours.", self.state)
        self.assertIn("Poster created", poster)
        plate = design_studio.command("Make a nameplate that says Talos.", self.state)
        self.assertIn("Nameplate source created", plate)
        svg = Path(poster.split(": ", 1)[1]).read_text(encoding="utf-8")
        self.assertIn("Library Hours", svg)
        self.assertIsNone(design_studio.command("Open Safari.", self.state))

    def test_only_design_requests_are_matched(self):
        for text in ("What's on my calendar?", "Play music.", "Write a function."):
            self.assertIsNone(design_studio.command(text, self.state))


if __name__ == "__main__":
    unittest.main()

"""Design workflows: vector posters and OpenSCAD nameplates."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_document_is_pages_compatible(self):
        result = design_studio.create_document(
            self.state, "Library Notes", "Stay focused.\nNo audio.",
        )
        target = Path(result["path"])
        self.assertEqual(target.suffix, ".docx")
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 500)
        import zipfile
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("Library Notes", xml)
        self.assertIn("Stay focused", xml)

    def test_command_routes_document_and_blender_render(self):
        document = design_studio.command(
            "Create a document titled Library Notes with body Stay focused.", self.state,
        )
        self.assertIn("Pages-compatible document created", document)
        plate = design_studio.command("Design a 3D nameplate that says Talos.", self.state)
        self.assertIn("Nameplate source created", plate)
        if design_studio.available()["blender"]:
            render = design_studio.command("Render the last design in Blender.", self.state)
            self.assertIn("Blender render created", render)
            self.assertTrue(Path(render.split(": ", 1)[1]).is_file())

    def test_pages_document_uses_the_native_app_when_available(self):
        with patch("talos_core.mac.pages.available", return_value=True), \
             patch("talos_core.mac.pages.create",
                   return_value={"path": "/tmp/example.pages", "kind": "pages",
                                 "opened": False}):
            reply = design_studio.command(
                "Create a Pages document titled Native Pages.", self.state)
        self.assertIn("Pages document created: /tmp/example.pages", reply)

    def test_parametric_box_renders_when_openscad_exists(self):
        source = design_studio.box_scad(40, 30, 20, 2)
        self.assertIn("difference()", source)
        self.assertIn("w = 40", source)
        result = design_studio.create_box(self.state, 40, 30, 20, 2)
        self.assertTrue(Path(result["path"]).is_file())
        if design_studio.OPENSCAD.is_file():
            self.assertIsNotNone(result["render"])
            self.assertTrue(Path(result["render"]).is_file())

    def test_box_command_parses_dimensions(self):
        reply = design_studio.command(
            "Design a 3D box 40x30x20 with 2mm walls.", self.state)
        self.assertIn("Box source created", reply)
        self.assertIn("rendered", reply)

    def test_export_last_pdf_uses_pages(self):
        source = self.state / "designs"
        source.mkdir(mode=0o700)
        document = source / "last.pages"
        document.write_bytes(b"pages")
        with patch.object(design_studio, "_read_last", return_value={"path": str(document)}), \
             patch("talos_core.mac.pages.export_pdf",
                   return_value={"path": str(document.with_suffix(".pdf"))}):
            reply = design_studio.command("Export the last design to PDF.", self.state)
        self.assertIn("PDF exported", reply)

    def test_render_requires_a_model(self):
        self.assertIn("no 3D model", design_studio.command("Render it in Blender.", self.state))

    def test_open_last_design_uses_the_named_app(self):
        from unittest.mock import patch

        design_studio.create_poster(self.state, "Open me")
        with patch("talos_core.design_studio._open_bundle") as opened:
            reply = design_studio.command("Open the last design in Pages.", self.state)
        self.assertIn("Opened", reply)
        self.assertIn("Pages", reply)
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[1], "com.apple.Pages")

    def test_open_without_a_design_is_safe(self):
        self.assertIn("no design", design_studio.command("Open the last design.", self.state))

    def test_only_design_requests_are_matched(self):
        for text in ("What's on my calendar?", "Play music.", "Write a function."):
            self.assertIsNone(design_studio.command(text, self.state))


if __name__ == "__main__":
    unittest.main()

"""Small design workflows built on installed tools (OpenSCAD and macOS preview).

OpenSCAD is used for parametric 3D/nameplate work; SVG posters are plain vector
files that can be opened in Preview, Safari or Pages. No audio is produced.
"""

from __future__ import annotations

import html
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OPENSCAD = Path("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
OPEN = "/usr/bin/open"


class DesignError(RuntimeError):
    pass


def _folder(state):
    folder = Path(state) / "designs"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def poster_svg(title, subtitle="", accent="#5ee7ff"):
    title = " ".join(str(title).split())[:120] or "Talos design"
    subtitle = " ".join(str(subtitle).split())[:180]
    title_escaped = html.escape(title)
    subtitle_escaped = html.escape(subtitle)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600" viewBox="0 0 1200 1600">
  <rect width="1200" height="1600" fill="#070b12"/>
  <circle cx="960" cy="260" r="220" fill="{accent}" opacity="0.18"/>
  <path d="M80 1180 L1120 900 L1120 1040 L80 1320 Z" fill="{accent}" opacity="0.22"/>
  <text x="90" y="760" fill="#f4f8ff" font-family="Helvetica Neue, Arial, sans-serif"
        font-size="92" font-weight="700">{title_escaped}</text>
  <text x="94" y="846" fill="#a9b8c8" font-family="Helvetica Neue, Arial, sans-serif"
        font-size="38">{subtitle_escaped}</text>
  <text x="94" y="1500" fill="{accent}" font-family="Menlo, monospace"
        font-size="28">TALOS · LOCAL DESIGN</text>
</svg>
'''


def create_poster(state, title, subtitle="", *, open_after=False):
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = _write(_folder(state) / f"poster-{stamp}.svg", poster_svg(title, subtitle))
    if open_after:
        _open(path)
    return _record_last(state, {"path": str(path), "kind": "poster-svg", "opened": bool(open_after)})


def _scad_text(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def nameplate_scad(text, width=90, height=32, depth=4):
    label = _scad_text(text or "TALOS")
    return f'''// Talos nameplate — OpenSCAD
w = {int(width)};
h = {int(height)};
d = {int(depth)};
difference() {{
  cube([w, h, d], center=true);
  translate([0, 0, d/2 + 0.01])
    linear_extrude(height=d/2 + 0.02)
      text("{label}", size=min(h*0.45, w*0.14), halign="center", valign="center");
}}
'''


def create_nameplate(state, text, *, width=90, height=32, depth=4, open_after=False):
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    source = _write(_folder(state) / f"nameplate-{stamp}.scad",
                    nameplate_scad(text, width, height, depth))
    result = {"path": str(source), "kind": "openscad", "render": None, "opened": False}
    if OPENSCAD.is_file():
        target = source.with_suffix(".stl")
        try:
            completed = subprocess.run(
                [str(OPENSCAD), "-o", str(target), str(source)],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DesignError(f"OpenSCAD could not render: {error}") from error
        if completed.returncode == 0 and target.is_file():
            target.chmod(0o600)
            result["render"] = str(target)
    if open_after:
        _open(source)
        result["opened"] = True
    return _record_last(state, result)


def _open(path):
    try:
        subprocess.run([OPEN, str(path)], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise DesignError(f"Could not open the design: {error}") from error


def _record_last(state, result):
    _write(_folder(state) / "last.json", __import__("json").dumps(result, indent=2))
    return result


def document_html(title, body=""):
    paragraphs = "".join(
        f"<p>{html.escape(line)}</p>" for line in str(body).splitlines() if line.strip()
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body style="font-family: Helvetica Neue, Helvetica, sans-serif; color:#111;">
<h1 style="font-size:28pt;">{html.escape(title)}</h1>
<div style="font-size:12pt; line-height:1.5;">{paragraphs}</div>
</body></html>
"""


def create_document(state, title, body="", *, fmt="docx", open_after=False):
    """Create a Pages/Word-compatible document with the built-in textutil."""
    title = " ".join(str(title).split())[:140] or "Talos document"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    folder = _folder(state)
    source = _write(folder / f"document-{stamp}.html", document_html(title, body))
    target = folder / f"document-{stamp}.{fmt}"
    completed = subprocess.run(
        ["/usr/bin/textutil", "-convert", fmt, "-output", str(target), str(source)],
        capture_output=True, text=True, timeout=60,
    )
    if completed.returncode != 0 or not target.is_file():
        source.unlink(missing_ok=True)
        raise DesignError(
            "Could not convert the document: " + " ".join(completed.stderr.split())[:200]
        )
    source.unlink(missing_ok=True)
    target.chmod(0o600)
    result = {"path": str(target), "kind": "document", "format": fmt, "opened": False}
    if open_after:
        _open_bundle(target, "com.apple.Pages")
        result["opened"] = True
    return _record_last(state, result)


def box_scad(width=80, depth=50, height=30, wall=3):
    dims = [int(width), int(depth), int(height)]
    thickness = int(wall)
    if not all(10 <= value <= 500 for value in dims):
        raise ValueError("Box dimensions must be 10–500 mm.")
    if not 1 <= thickness <= min(dims) // 3:
        raise ValueError("Wall thickness must fit inside the box.")
    w, d, h = dims
    return f"""// Talos parametric box — OpenSCAD
w = {w}; d = {d}; h = {h}; t = {thickness};
difference() {{
  cube([w, d, h], center=true);
  translate([0, 0, t])
    cube([w - 2*t, d - 2*t, h], center=true);
}}
"""


def create_box(state, width=80, depth=50, height=30, wall=3, *, open_after=False):
    source_text = box_scad(width, depth, height, wall)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    source = _write(_folder(state) / f"box-{stamp}.scad", source_text)
    result = {"path": str(source), "kind": "openscad-box", "render": None, "opened": False}
    if OPENSCAD.is_file():
        target = source.with_suffix(".stl")
        completed = subprocess.run(
            [str(OPENSCAD), "-o", str(target), str(source)],
            capture_output=True, text=True, timeout=120,
        )
        if completed.returncode == 0 and target.is_file():
            target.chmod(0o600)
            result["render"] = str(target)
    if open_after:
        _open(source)
        result["opened"] = True
    return _record_last(state, result)


def create_pages(state, title, body="", *, open_after=False):
    """Create a real Pages document, then remember it as the latest design."""
    from talos_core.mac import pages

    return _record_last(state, pages.create(state, title, body, open_after=open_after))


def append_last_pages(state, text):
    """Append a paragraph to the most recent native Pages document."""
    from talos_core.mac import pages

    last = _read_last(state) or {}
    path = Path(str(last.get("path") or ""))
    if not path.is_file() or path.suffix.casefold() != ".pages":
        return "The last design is not a Pages document."
    pages.append(state, path, text)
    return f"Updated Pages document: {path}"


def export_last_pdf(state):
    """Export the most recent native Pages document to PDF."""
    from talos_core.mac import pages

    last = _read_last(state) or {}
    path = Path(str(last.get("path") or ""))
    if not path.is_file() or path.suffix.casefold() != ".pages":
        return "The last design is not a Pages document."
    result = pages.export_pdf(state, path)
    return f"PDF exported: {result['path']}"


def render_stl(state, stl_path, *, timeout=180):
    """Render an STL to PNG with headless Blender. No window and no audio."""
    blender = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    source = Path(stl_path).resolve()
    designs = _folder(state).resolve()
    if designs not in source.parents:
        raise DesignError("Only designs inside the Talos design folder can be rendered.")
    if not source.is_file():
        raise DesignError("The STL file does not exist.")
    if not blender.is_file():
        raise DesignError("Blender is not installed.")
    target = source.with_suffix(".png")
    script = _write(_folder(state) / "render_stl.py", _BLENDER_SCRIPT)
    completed = subprocess.run(
        [str(blender), "-b", "-P", str(script), "--", str(source), str(target)],
        capture_output=True, text=True, timeout=timeout,
    )
    if completed.returncode != 0 or not target.is_file():
        raise DesignError(
            "Blender could not render: " + " ".join(completed.stderr.split())[-240:]
        )
    target.chmod(0o600)
    return _record_last(state, {"path": str(target), "kind": "render", "source": str(source)})


_BLENDER_SCRIPT = """import bpy, mathutils, sys
stl, out = sys.argv[-2], sys.argv[-1]
bpy.ops.wm.read_factory_settings(use_empty=True)
try:
    bpy.ops.wm.stl_import(filepath=stl)
except Exception:
    bpy.ops.import_mesh.stl(filepath=stl)
obj = bpy.context.selected_objects[0]
points = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
center = sum(points, mathutils.Vector()) / 8
radius = max((point - center).length for point in points) or 1.0
camera_data = bpy.data.cameras.new("Camera")
camera = bpy.data.objects.new("Camera", camera_data)
bpy.context.scene.collection.objects.link(camera)
camera.location = center + mathutils.Vector((radius * 2.2, -radius * 2.2, radius * 1.8))
camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.render.resolution_x = 1200
scene.render.resolution_y = 900
scene.render.filepath = out
scene.render.image_settings.file_format = "PNG"
bpy.ops.render.render(write_still=True)
"""


def _open_bundle(path, bundle):
    try:
        subprocess.run(["/usr/bin/open", "-b", bundle, str(path)], check=False, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise DesignError(f"Could not open the design: {error}") from error


def available():
    return {
        "openscad": OPENSCAD.is_file(),
        "svg": True,
        "blender": Path("/Applications/Blender.app/Contents/MacOS/Blender").is_file(),
        "pages": Path("/Applications/Pages Creator Studio.app").is_dir(),
    }


def is_design_request(text):
    return bool(re.search(r"\b(?:design|poster|nameplate|cad|3d model)\b", text, re.I))


def command(text, state):
    """Route a plain-language design request. Returns a reply or None."""
    words = " ".join(str(text).split())

    opened = re.fullmatch(
        r"(?:please )?open (?:the )?(?:last )?(?:design|document|it)"
        r"(?: in (pages|preview|safari|finder))?[?.! ]*",
        words, re.I,
    )
    if opened:
        last = _read_last(state) or {}
        path = last.get("path")
        if not path or not Path(path).is_file():
            return "There is no design to open yet."
        target = opened.group(1)
        bundles = {
            "pages": "com.apple.Pages",
            "preview": "com.apple.Preview",
            "safari": "com.apple.Safari",
            "finder": "com.apple.finder",
        }
        if target:
            _open_bundle(path, bundles[target.casefold()])
            return f"Opened {Path(path).name} in {target.title()}."
        _open(path)
        return f"Opened {Path(path).name}."

    appended = re.fullmatch(
        r"(?:add|append)(?: a paragraph)?(?: saying|:)? (.+?) to (?:the )?"
        r"(?:last|latest) (?:pages )?document[.! ]*", words, re.I,
    )
    if appended:
        return append_last_pages(state, appended.group(1).strip())

    box = re.fullmatch(
        r"(?:please )?(?:design|create|make) (?:a |an )?(?:3d |parametric )?box"
        r"(?: (?:that is|measuring|with dimensions))? "
        r"(\d{1,3})(?:\s*(?:x|by)\s*)(\d{1,3})(?:\s*(?:x|by)\s*)(\d{1,3})"
        r"(?: ?mm)?(?: with (\d{1,2})(?: ?mm)? walls?)?[.! ]*",
        words, re.I,
    )
    if box:
        width, depth, height = (int(box.group(i)) for i in (1, 2, 3))
        wall = int(box.group(4) or 3)
        result = create_box(state, width, depth, height, wall)
        rendered = f" and rendered {result['render']}" if result.get("render") else ""
        return f"Box source created: {result['path']}{rendered}"

    if re.fullmatch(r"export (?:the )?(?:last|latest) (?:pages )?(?:design|document) to pdf[.! ]*",
                    words, re.I):
        return export_last_pdf(state)

    render = re.fullmatch(
        r"(?:please )?render (?:the )?(?:last|it)(?: design| nameplate| model| stl)?"
        r"(?: in blender)?[?.! ]*",
        words, re.I,
    )
    if render:
        last = _read_last(state)
        source = (last or {}).get("render") or (
            (last or {}).get("path") if str((last or {}).get("path", "")).endswith(".stl") else None
        )
        if not source:
            return "There is no 3D model to render yet. Create a nameplate first."
        result = render_stl(state, source)
        return f"Blender render created: {result['path']}"

    document = re.fullmatch(
        r"(?:please )?(?:create|make|write|draft)(?: me)? (?:a |an )?"
        r"(?:pages |word )?(?:document|doc) "
        r"(?:titled |called |named |about )?(.+?)[.?! ]*",
        words, re.I,
    )
    if document:
        subject = document.group(1).strip().strip("\"'")
        title, body = subject, ""
        for separator in (" with body ", " containing ", " that says "):
            if separator in subject.casefold():
                index = subject.casefold().index(separator)
                title, body = subject[:index].strip(), subject[index + len(separator):].strip()
                break
        if not title:
            return "Tell me the document title."
        if "pages document" in words.casefold():
            from talos_core.mac import pages
            if pages.available():
                result = create_pages(state, title, body)
                return f"Pages document created: {result['path']}"
        result = create_document(state, title, body, fmt="docx")
        return f"Pages-compatible document created: {result['path']}"

    match = re.fullmatch(
        r"(?:please )?(?:design|create|make|draw|render)(?: me)? "
        r"(?:a |an )?(?:3d )?(poster|nameplate|sign)(?: (?:titled|called|about|that says|saying|for))? "
        r"[:\-]? ?(.+?)[.?! ]*",
        words, re.I,
    )
    if not match:
        return None
    kind, subject = match.group(1).lower(), match.group(2).strip().strip("\"'")
    if not subject:
        return "Tell me the text for the design."
    if kind == "poster":
        result = create_poster(state, subject)
        return f"Poster created: {result['path']}"
    result = create_nameplate(state, subject)
    rendered = f" and rendered {result['render']}" if result.get("render") else ""
    return f"Nameplate source created: {result['path']}{rendered}"


def _read_last(state):
    import json

    path = _folder(state) / "last.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

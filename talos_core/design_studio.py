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
    return {"path": str(path), "kind": "poster-svg", "opened": bool(open_after)}


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
    return result


def _open(path):
    try:
        subprocess.run([OPEN, str(path)], check=False, timeout=10)
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
    match = re.fullmatch(
        r"(?:please )?(?:design|create|make|draw|render)(?: me)? "
        r"(?:a |an )?(poster|nameplate|sign)(?: (?:titled|called|about|that says|saying|for))? "
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

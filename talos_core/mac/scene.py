"""Offline people/face detection for still images with Apple Vision.

The helper reads an image and returns counts only; it never captures a camera
frame, stores image contents, or produces audio. Camera capture remains the
native app's responsibility, which can send a frame to this detector.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "vision_scene.swift"
SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".bmp"}


class SceneError(RuntimeError):
    pass


def available() -> bool:
    return SOURCE.is_file() and bool(shutil.which("swiftc"))


def _binary(state: Path, compiler: str) -> Path:
    target = Path(state) / "native" / "talos-vision-scene"
    if target.is_file() and target.stat().st_mtime >= SOURCE.stat().st_mtime:
        return target
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".vision-scene-", dir=target.parent)
    os.close(fd)
    try:
        completed = subprocess.run(
            [compiler, "-O", str(SOURCE), "-o", temporary],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode != 0:
            raise SceneError("Vision helper failed to compile: "
                             + " ".join((completed.stderr or "").split())[:300])
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


def detect(state, path, *, timeout: int = 60) -> dict:
    """Return ``{"people", "faces", "window", "empty"}`` for an image."""
    if not available():
        raise SceneError("The native Vision compiler is unavailable.")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise SceneError(f"Image does not exist: {source}")
    if source.suffix.lower() not in SUFFIXES:
        raise SceneError("Supported image formats are PNG, JPEG, TIFF, HEIC and BMP.")
    if source.stat().st_size == 0:
        raise SceneError("The image is empty.")
    helper = _binary(Path(state), shutil.which("swiftc"))
    try:
        completed = subprocess.run(
            [str(helper), str(source)], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SceneError(f"Vision failed: {error}") from error
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300]
        raise SceneError(detail or "Vision returned an error.")
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as error:
        raise SceneError("Vision returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise SceneError("Vision returned an unexpected payload.")
    try:
        people = int(payload.get("people", 0))
        faces = int(payload.get("faces", 0))
        window = int(payload.get("window", 0))
    except (TypeError, ValueError) as error:
        raise SceneError("Vision returned invalid counts.") from error
    people = max(0, people)
    return {"people": people, "faces": max(0, faces), "window": max(0, window),
            "empty": people == 0}


def describe(scene: dict) -> str:
    if scene.get("empty"):
        return "The scene looks empty."
    people = int(scene.get("people", 0))
    noun = "person" if people == 1 else "people"
    return f"I see {people} {noun}."

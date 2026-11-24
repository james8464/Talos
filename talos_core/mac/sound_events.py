"""Classify non-word sounds with Apple's built-in SoundAnalysis classifier.

This is deliberately file-based: it never records or plays audio. The Swift
helper is compiled on first use and cached under private state. The built-in
classifier recognizes speech, music, laughter, applause, glass, alarms and many
other everyday sounds, so Talos can react to more than transcribed words.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "sound_classify.swift"
SUFFIXES = {".wav", ".aiff", ".aif", ".caf", ".mp3", ".m4a"}
MAX_SECONDS = 180


class SoundEventError(RuntimeError):
    pass


def available() -> bool:
    return SOURCE.is_file() and bool(shutil.which("swiftc"))


def _binary(state: Path, compiler: str) -> Path:
    target = Path(state) / "native" / "talos-sound-classify"
    if target.is_file() and target.stat().st_mtime >= SOURCE.stat().st_mtime:
        return target
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sound-classify-", dir=target.parent)
    os.close(fd)
    try:
        completed = subprocess.run(
            [compiler, "-O", str(SOURCE), "-o", temporary],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode != 0:
            raise SoundEventError("SoundAnalysis helper failed to compile: "
                                  + " ".join((completed.stderr or "").split())[:300])
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


def classify(state, path, *, timeout: int = 120) -> list[dict]:
    """Return ranked ``{"label", "confidence"}`` sound events for an audio file."""
    if not available():
        raise SoundEventError("The native SoundAnalysis compiler is unavailable.")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise SoundEventError(f"Audio file does not exist: {source}")
    if source.suffix.lower() not in SUFFIXES:
        raise SoundEventError("Supported audio formats are WAV, AIFF, CAF, MP3 and M4A.")
    if source.stat().st_size == 0:
        raise SoundEventError("The audio file is empty.")
    helper = _binary(Path(state), shutil.which("swiftc"))
    try:
        completed = subprocess.run(
            [str(helper), str(source)], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SoundEventError(f"SoundAnalysis failed: {error}") from error
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300]
        raise SoundEventError(detail or "SoundAnalysis returned an error.")
    try:
        payload = json.loads(completed.stdout or "[]")
    except ValueError as error:
        raise SoundEventError("SoundAnalysis returned invalid JSON.") from error
    if not isinstance(payload, list):
        raise SoundEventError("SoundAnalysis returned an unexpected payload.")
    events = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        if label and 0.0 <= confidence <= 1.0:
            events.append({"label": label, "confidence": round(confidence, 4)})
    return sorted(events, key=lambda item: item["confidence"], reverse=True)


def describe(events, *, limit: int = 5) -> str:
    if not events:
        return "I couldn't identify a distinct sound in that clip."
    parts = [f"{item['label']} {item['confidence']:.0%}" for item in events[:limit]]
    return "Sounds: " + "; ".join(parts)

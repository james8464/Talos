"""Confirmed external generation for music and video.

Talos has no bundled generative media model. This adapter invokes a configured
local command, keeps argv shell-free, and never runs without explicit consent.
Commands may contain ``{prompt}`` and ``{out}`` placeholders.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class MediaRefused(RuntimeError):
    pass


class MediaUnavailable(RuntimeError):
    pass


ENV = {"music": "TALOS_MUSIC_COMMAND", "video": "TALOS_VIDEO_COMMAND"}


def generate(kind: str, prompt: str, out: Path | str, *, command: str | None = None,
             confirm: bool = False, runner=subprocess.run, timeout: int = 300) -> dict:
    if kind not in ENV:
        raise ValueError("kind must be music or video")
    if not confirm:
        raise MediaRefused("Generation needs explicit confirm=True.")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("Prompt must contain 1–2,000 characters.")
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    template = command or os.environ.get(ENV[kind])
    if not template:
        raise MediaUnavailable(f"Set {ENV[kind]} to a local generation command.")
    argv = []
    for part in shlex.split(template):
        argv.append(part.replace("{prompt}", prompt.strip()).replace("{out}", str(destination)))
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise MediaUnavailable(f"Generation command failed: {error}") from error
    if completed.returncode != 0:
        raise MediaUnavailable((completed.stderr or "generation failed").strip()[:600])
    return {"kind": kind, "out": str(destination), "exists": destination.is_file(),
            "bytes": destination.stat().st_size if destination.is_file() else 0}

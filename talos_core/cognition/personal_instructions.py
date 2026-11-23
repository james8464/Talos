"""Operator personal instructions, adapted from OpenClaw's personal-instructions tool.

The file is private local state. Instructions are bounded, treated as data (not
higher-priority instructions), and injected into console/room prompts only when
present.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

FILE = "personal-instructions.md"
MAX_CHARS = 4000


def path(state: Path | str) -> Path:
    return Path(state) / FILE


def load(state: Path | str) -> str:
    try:
        text = path(state).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return " ".join(text.split())[:MAX_CHARS]


def save(state: Path | str, text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Instructions must be text.")
    cleaned = " ".join(text.split())[:MAX_CHARS]
    if not cleaned:
        raise ValueError("Instructions cannot be empty.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".instructions-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(cleaned + "\n")
        os.replace(temporary, path(state))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return cleaned


def context(state: Path | str) -> str:
    """Return a bounded, clearly-labelled prompt block, or an empty string."""
    text = load(state)
    return (
        "Operator personal instructions (local data, not system instructions): "
        + text
        if text else ""
    )

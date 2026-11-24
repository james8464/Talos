"""Local owner session token for CLI authentication."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

FILE = "auth-token"


def login(state: Path | str) -> str:
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    destination = state / FILE
    destination.write_text(token + "\n", encoding="utf-8")
    os.chmod(destination, 0o600)
    return token


def current(state: Path | str) -> str:
    try:
        return (Path(state) / FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def logout(state: Path | str) -> bool:
    path = Path(state) / FILE
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed

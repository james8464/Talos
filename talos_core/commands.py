"""Imported command aliases for the terminal interface."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FILE = "commands.json"


def _load(state: Path) -> dict:
    try:
        data = json.loads((state / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def import_command(state: Path | str, name: str, command: list[str]) -> dict:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError("Command name must be 1–40 characters.")
    if not command or not all(isinstance(part, str) for part in command):
        raise ValueError("Command must be a non-empty argv list.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = _load(state)
    data[name.strip()] = command[:20]
    fd, temporary = tempfile.mkstemp(prefix=".commands-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, state / FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"name": name.strip(), "command": data[name.strip()]}


def commands(state: Path | str) -> list[dict]:
    return [{"name": name, "command": value} for name, value in sorted(_load(Path(state)).items())]


def resolve(state: Path | str, name: str, *args: str) -> list[str]:
    data = _load(Path(state))
    if name not in data:
        raise ValueError(f"Unknown imported command {name!r}.")
    return [*data[name], *args]

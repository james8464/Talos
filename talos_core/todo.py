"""Durable local todo list for the terminal interface."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

FILE = "todos.json"


def _path(state: Path) -> Path:
    return state / FILE


def _load(state: Path) -> list[dict]:
    try:
        data = json.loads(_path(state).read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)]
    except (OSError, ValueError):
        return []


def _save(state: Path, items: list[dict]) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".todos-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(items, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, _path(state))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def add(state: Path | str, text: str) -> dict:
    text = " ".join(str(text).split())
    if not 1 <= len(text) <= 500:
        raise ValueError("Todo text must be 1–500 characters.")
    items = _load(Path(state))
    item = {"id": max((int(x.get("id", 0)) for x in items), default=0) + 1,
            "text": text, "done": False, "created": time.time()}
    _save(Path(state), [*items, item])
    return item


def items(state: Path | str) -> list[dict]:
    return _load(Path(state))


def complete(state: Path | str, ident: int) -> bool:
    found = _load(Path(state))
    changed = False
    for item in found:
        if int(item.get("id", 0)) == int(ident):
            item["done"] = True
            changed = True
    if changed:
        _save(Path(state), found)
    return changed

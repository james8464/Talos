"""Local node registry (paired helpers and their capabilities)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FILE = "nodes.json"


def _load(state: Path) -> dict:
    try:
        data = json.loads((state / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def nodes(state: Path | str) -> list[dict]:
    return [{"name": name, **value} for name, value in sorted(_load(Path(state)).items())]


def register(state: Path | str, name: str, *, kind: str = "local", endpoint: str = "") -> dict:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("Node name must be 1–60 characters.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = _load(state)
    data[name.strip()] = {"kind": str(kind)[:30], "endpoint": str(endpoint)[:200]}
    fd, temporary = tempfile.mkstemp(prefix=".nodes-", dir=state)
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
    return {"name": name.strip(), **data[name.strip()]}


def remove(state: Path | str, name: str) -> bool:
    state = Path(state)
    data = _load(state)
    if name not in data:
        return False
    data.pop(name)
    (state / FILE).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(state / FILE, 0o600)
    return True

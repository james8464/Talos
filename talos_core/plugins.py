"""Read-only discovery of local Talos plugin manifests."""

from __future__ import annotations

import json
from pathlib import Path

FOLDER = "plugins"


def discover(state: Path | str) -> list[dict]:
    root = Path(state) / FOLDER
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        name = manifest.get("name")
        command = manifest.get("command")
        if not isinstance(name, str) or not name.strip() or not isinstance(command, str):
            continue
        found.append({"name": name.strip()[:80], "command": command,
                      "args": [str(a) for a in manifest.get("args", ())][:20],
                      "enabled": manifest.get("enabled") is not False})
    return found

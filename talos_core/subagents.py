"""Read-only local subagent registry with safe argv construction."""

from __future__ import annotations

import json
from pathlib import Path

FOLDER = "subagents"


def discover(state: Path | str) -> list[dict]:
    folder = Path(state) / FOLDER
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            continue
        command = data.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            continue
        found.append({"name": data["name"].strip()[:60], "command": command[:10],
                      "enabled": data.get("enabled") is not False})
    return found


def command_for(state: Path | str, name: str, task: str) -> list[str]:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("A task is required.")
    for agent in discover(state):
        if agent["name"] == name and agent["enabled"]:
            return [*agent["command"], task[:4000]]
    raise ValueError(f"No enabled subagent named {name!r}.")

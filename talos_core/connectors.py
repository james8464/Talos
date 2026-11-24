"""Slack/Discord webhook connectors with local configuration and no secrets in logs."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path

FILE = "connectors.json"
KINDS = ("slack", "discord")


def _load(state: Path) -> dict:
    try:
        data = json.loads((state / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def configure(state: Path | str, kind: str, url: str) -> dict:
    if kind not in KINDS:
        raise ValueError("kind must be slack or discord")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("Use an https webhook URL.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = _load(state)
    data[kind] = url
    fd, temporary = tempfile.mkstemp(prefix=".connectors-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
        os.replace(temporary, state / FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"kind": kind, "configured": True}


def send(state: Path | str, kind: str, text: str, *, opener=urllib.request.urlopen) -> dict:
    if kind not in KINDS:
        raise ValueError("kind must be slack or discord")
    url = _load(Path(state)).get(kind)
    if not url:
        raise RuntimeError(f"No {kind} webhook configured.")
    if not isinstance(text, str) or not 1 <= len(text) <= 2000:
        raise ValueError("Message must be 1–2,000 characters.")
    if kind == "slack":
        body = json.dumps({"text": text}).encode()
    else:
        body = json.dumps({"content": text}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with opener(request, timeout=15) as response:
        return {"kind": kind, "status": getattr(response, "status", 200)}

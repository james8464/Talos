"""One-shot client for the running Talos room.

Used by ``talos ask`` so a terminal user reaches the same quick answers,
native-app routes, browser, design and computer-control paths as the room.
The room token is read locally and never printed. No audio is produced.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"
MAX_TEXT = 8_000


class RoomClientError(RuntimeError):
    pass


def _token(state) -> str:
    path = Path(state) / "room.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RoomClientError(f"No room token at {path}.") from error
    if not token:
        raise RoomClientError("The room token is empty.")
    return token


def turn(state, text: str, *, timeout: float = 240, url: str | None = None) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise RoomClientError("Ask a non-empty question.")
    if len(text) > MAX_TEXT:
        raise RoomClientError("The question is too long.")
    base = (url or DEFAULT_URL).rstrip("/")
    request = urllib.request.Request(
        base + "/v1/turn",
        data=json.dumps({"text": text.strip(), "source": "text"}).encode(),
        headers={"Authorization": "Bearer " + _token(state),
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RoomClientError(f"Talos room returned HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RoomClientError(f"Talos room is unavailable: {error}") from error
    except ValueError as error:
        raise RoomClientError("Talos room returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise RoomClientError("Talos room returned an unexpected payload.")
    return payload

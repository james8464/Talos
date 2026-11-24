"""Optional OpenCode Go cloud-model bridge for explicit, user-requested tasks.

The bridge sends only the caller's prompt. It never adds room history, saved
facts, transcripts, or other private context. The API key is read from an
environment variable, the private runtime state, or the local OpenCode auth
store; it is never returned or logged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
KEY_PATH = Path(__file__).resolve().parents[2] / "state" / "opencode.key"
AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
USER_AGENT = "opencode/1.18.10"


class CloudModelError(RuntimeError):
    """The optional cloud route could not complete the request."""


def api_key() -> str:
    for name in ("TALOS_OPENCODE_KEY", "OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    try:
        value = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    entry = data.get("opencode-go") if isinstance(data, dict) else None
    return str(entry.get("key", "")).strip() if isinstance(entry, dict) else ""


def configured() -> bool:
    return bool(api_key())


def complete(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    max_tokens: int = 800,
    timeout: int = 60,
    session_id: str | None = None,
    opener=urllib.request.urlopen,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Cloud prompt must not be empty.")
    key = api_key()
    if not key:
        raise CloudModelError("OpenCode Go is not configured.")
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8192:
        raise ValueError("max_tokens must be between 1 and 8192.")
    messages = []
    if system:
        messages.append({"role": "system", "content": str(system)[:4000]})
    messages.append({"role": "user", "content": prompt[:20000]})
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    request = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-opencode-session": session_id or str(uuid.uuid4()),
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise CloudModelError(f"OpenCode Go returned HTTP {error.code}.") from error
    except (OSError, ValueError) as error:
        raise CloudModelError("OpenCode Go request failed.") from error
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise CloudModelError("OpenCode Go returned an unexpected response.") from error
    if not isinstance(content, str) or not content.strip():
        raise CloudModelError("OpenCode Go returned an empty response.")
    return content.strip()

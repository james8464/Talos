"""Signed webhook configuration and payload construction (no delivery)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

FILE = "webhook.json"


def configure(state: Path | str, url: str, secret: str) -> dict:
    if not isinstance(url, str) or not url.startswith(("https://", "http://127.0.0.1")):
        raise ValueError("Webhook URL must be https or local loopback.")
    if not isinstance(secret, str) or len(secret) < 16:
        raise ValueError("Webhook secret must be at least 16 characters.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".webhook-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"url": url, "secret": secret}, stream)
        os.replace(temporary, state / FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"url": url}


def config(state: Path | str) -> dict:
    try:
        data = json.loads((Path(state) / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def payload(event: dict, *, state: Path | str) -> tuple[bytes, str]:
    body = json.dumps(event, sort_keys=True, ensure_ascii=False).encode()
    secret = str(config(state).get("secret", ""))
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, signature

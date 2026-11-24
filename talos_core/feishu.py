"""Feishu/Lark document and drive client with local credential storage."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path

FILE = "feishu.json"
BASE = "https://open.feishu.cn/open-apis"


def configure(state: Path | str, app_id: str, app_secret: str) -> dict:
    if not isinstance(app_id, str) or not app_id.strip() or not isinstance(app_secret, str) or not app_secret.strip():
        raise ValueError("Feishu app id and secret are required.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".feishu-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"app_id": app_id.strip(), "app_secret": app_secret.strip()}, stream)
        os.replace(temporary, state / FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"configured": True}


def config(state: Path | str) -> dict:
    try:
        data = json.loads((Path(state) / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _call(url: str, payload: dict | None, opener, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None,
                                     headers=headers, method="POST" if payload is not None else "GET")
    with opener(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("code", 0) != 0:
        raise RuntimeError(data.get("msg") or "Feishu API error")
    return data


def token(state: Path | str, *, opener=urllib.request.urlopen) -> str:
    data = config(state)
    if not data.get("app_id") or not data.get("app_secret"):
        raise RuntimeError("Configure Feishu app credentials first.")
    result = _call(BASE + "/auth/v3/tenant_access_token/internal",
                   {"app_id": data["app_id"], "app_secret": data["app_secret"]}, opener)
    return str(result.get("tenant_access_token", ""))


def doc(state: Path | str, document_id: str, *, opener=urllib.request.urlopen) -> dict:
    if not document_id:
        raise ValueError("Document id is required.")
    return _call(BASE + f"/docx/v1/documents/{document_id}/raw_content", None,
                 opener, token(state, opener=opener))


def drive(state: Path | str, folder_token: str = "", *, opener=urllib.request.urlopen) -> dict:
    params = f"?folder_token={folder_token}" if folder_token else ""
    return _call(BASE + "/drive/v1/files" + params, None, opener, token(state, opener=opener))

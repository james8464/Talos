"""Small, escaped status portal rendered locally (no network listener)."""

from __future__ import annotations

import html
import json


def render(status: dict) -> str:
    if not isinstance(status, dict):
        raise TypeError("Status must be a mapping.")
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in sorted(status.items())
    )
    body = json.dumps(status, sort_keys=True, ensure_ascii=True)
    return (
        "<!doctype html><meta charset='utf-8'><title>Talos</title>"
        "<style>body{font:14px system-ui;background:#111;color:#eee;padding:2rem}"
        "table{border-collapse:collapse}th,td{padding:.35rem .8rem;border-bottom:1px solid #333}"
        "th{text-align:left;color:#6cf}</style><h1>Talos</h1>"
        f"<table>{rows}</table><pre>{html.escape(body)}</pre>"
    )

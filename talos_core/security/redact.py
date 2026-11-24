"""Redact credentials from logs and diagnostic text before persistence."""

from __future__ import annotations

import re

PATTERNS = (
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")),
    ("api_key", re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,}")),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
)
MARK = "[redacted]"


def redact(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Text must be a string.")
    result = text
    for _name, pattern in PATTERNS:
        result = pattern.sub(lambda match: (match.group(1) if match.groups() else "") + MARK, result)
    return result


def findings(text: str) -> list[str]:
    return [name for name, pattern in PATTERNS if pattern.search(text)]

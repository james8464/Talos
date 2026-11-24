"""Small deterministic mixture-of-agents aggregator."""

from __future__ import annotations

import re


def choose(responses: list[str], *, keywords: list[str] | None = None) -> dict:
    if not isinstance(responses, list) or not responses:
        raise ValueError("Responses must be a non-empty list.")
    wanted = {word.casefold() for word in (keywords or [])}
    scored = []
    for index, text in enumerate(responses):
        words = set(re.findall(r"[a-z0-9_]+", str(text).casefold()))
        score = len(wanted & words) * 5 + min(len(str(text)) // 120, 4) - index * 0.1
        scored.append((score, index, str(text)))
    score, index, text = max(scored, key=lambda item: (item[0], -item[1]))
    return {"index": index, "text": text, "score": score, "candidates": len(responses)}

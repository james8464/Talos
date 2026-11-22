"""OpenClaw memory-core MMR and Unicode similarity adapted for SQLite FTS5 rows."""

import re
import unicodedata

CJK = re.compile(
    r"[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u1100-\u11ff]+"
)


def tokens(text):
    text = unicodedata.normalize("NFC", text.casefold())
    words = set(re.findall(r"\w+", CJK.sub(" ", text)))
    for run in CJK.findall(text):
        words.update(run)
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words


def diverse(rows, limit, *, weight=0.70):
    """Retain the best FTS hit and penalize overlap with already-selected excerpts."""
    if not rows:
        return []
    scores = [-row["fts_rank"] for row in rows]
    low, high = min(scores), max(scores)
    relevance = [(score - low) / (high - low) if high != low else 1 for score in scores]
    prepared = [tokens(row["excerpt"]) for row in rows]
    remaining = list(range(len(rows)))
    overlap = [0.0] * len(rows)
    selected = []
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda i: (
                weight * relevance[i] - (1 - weight) * overlap[i],
                scores[i],
            ),
        )
        remaining.remove(best)
        selected.append(rows[best])
        for i in remaining:
            union = prepared[i] | prepared[best]
            similarity = (
                len(prepared[i] & prepared[best]) / len(union)
                if union
                else float(rows[i]["excerpt"] == rows[best]["excerpt"])
            )
            overlap[i] = max(overlap[i], similarity)
    return selected

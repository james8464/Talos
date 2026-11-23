"""Continuous contextual reflection over Talos's knowledge stores.

This is a deliberately modest analogy to superposition: many candidate
relations are kept with weights, then "collapsed" to the strongest explanation
when an answer or a brief needs one. It is ordinary weighted retrieval, not
quantum hardware, but it behaves the way the product needs: related context is
continually recomputed and new conversation turns feed it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass

from talos_core.cognition import cognitive_engine

TABLE = "knowledge_reflections"
WINDOW_SECONDS = 900


@dataclass(frozen=True)
class Hypothesis:
    topic: str
    store: str
    source: str
    title: str
    excerpt: str
    weight: float

    def to_dict(self) -> dict:
        return {
            "topic": self.topic, "store": self.store, "source": self.source,
            "title": self.title, "excerpt": self.excerpt, "weight": round(self.weight, 4),
        }


def initialize(db: sqlite3.Connection) -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            topic TEXT NOT NULL,
            store TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            weight REAL NOT NULL,
            checked REAL NOT NULL,
            PRIMARY KEY (topic, store, source, title)
        )
    """)


def superpose(hypotheses: list[Hypothesis], limit: int = 6) -> list[Hypothesis]:
    """Rank overlapping explanations and collapse to the strongest few."""
    if not hypotheses:
        return []
    merged: dict[tuple[str, str, str], Hypothesis] = {}
    for item in hypotheses:
        key = (item.store, item.source, item.title)
        current = merged.get(key)
        if current is None or item.weight > current.weight:
            merged[key] = item
    ranked = sorted(merged.values(), key=lambda item: (-item.weight, item.title))
    return ranked[:max(1, limit)]


def _tokens(text: str) -> set[str]:
    stop = {"the", "and", "that", "this", "with", "from", "have", "will", "your",
            "what", "when", "where", "about", "into", "they", "them", "there"}
    return {word for word in re.findall(r"[a-z][a-z0-9_]{2,}", text.casefold())
            if word not in stop}


def _recent(db: sqlite3.Connection, limit: int = 20) -> list[str]:
    texts: list[str] = []
    try:
        rows = db.execute(
            "SELECT text FROM transcripts WHERE role IN ('user','assistant') "
            "AND length(text) > 3 ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        texts.extend(str(row[0]) for row in rows if row[0])
    except sqlite3.Error:
        pass
    # Conversation is not the only evidence: durable facts, commitments and
    # goals also seed relations when the archive is quiet.
    for table, column in (("personal_facts", "subject || ' ' || value"),
                          ("commitments", "commitment"),
                          ("context_evidence", "excerpt"),
                          ("goals", "outcome")):
        try:
            rows = db.execute(
                f"SELECT {column} FROM {table} WHERE length({column}) > 3 "
                "ORDER BY rowid DESC LIMIT ?", (max(1, limit // 4),)
            ).fetchall()
            texts.extend(str(row[0]) for row in rows if row[0])
        except sqlite3.Error:
            continue
    return texts[:limit]


def reflect(db: sqlite3.Connection, state, *, limit: int = 6) -> list[Hypothesis]:
    """Recompute related context from recent conversation and every store."""
    initialize(db)
    hypotheses: list[Hypothesis] = []
    for turn in _recent(db):
        words = _tokens(turn)
        if not words:
            continue
        topic = " ".join(sorted(words)[:4])
        try:
            result = cognitive_engine.query_knowledge(
                db, turn, state=state, private_ok=True, limit=limit
            )
        except (OSError, ValueError, sqlite3.Error):
            continue
        for rank, item in enumerate(result.get("results", ())):
            excerpt = str(item.get("excerpt") or item.get("snippet") or "")[:400]
            if not excerpt:
                continue
            overlap = len(words & _tokens(excerpt))
            weight = (1.0 / (1 + rank)) + 0.25 * overlap + (
                0.15 if item.get("store") == "code_graph" else 0)
            hypotheses.append(Hypothesis(
                topic=topic,
                store=str(item.get("store", "unknown")),
                source=str(item.get("source", "")),
                title=str(item.get("title", ""))[:120],
                excerpt=excerpt,
                weight=weight,
            ))
    collapsed = superpose(hypotheses, limit=limit)
    now = time.time()
    with db:
        for item in collapsed:
            db.execute(
                f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(topic,store,source,title) DO UPDATE SET "
                "excerpt=excluded.excerpt, weight=excluded.weight, checked=excluded.checked",
                (item.topic, item.store, item.source, item.title,
                 item.excerpt, item.weight, now),
            )
    return collapsed


def summary(db: sqlite3.Connection, *, limit: int = 5) -> list[dict]:
    initialize(db)
    rows = db.execute(
        f"SELECT topic,store,source,title,excerpt,weight,checked FROM {TABLE} "
        "ORDER BY weight DESC, checked DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"topic": row[0], "store": row[1], "source": row[2], "title": row[3],
         "excerpt": row[4], "weight": row[5], "checked": row[6]}
        for row in rows
    ]


def count(db: sqlite3.Connection) -> int:
    initialize(db)
    return int(db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0])


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("state/agent.sqlite3"))
    args = parser.parse_args()
    from talos_core.memory.agent_memory import open_memory

    with open_memory(args.db) as database:
        found = reflect(database, args.db.parent)
        print(json.dumps({"relations": len(found), "top": summary(database)},
                         indent=2, ensure_ascii=False))

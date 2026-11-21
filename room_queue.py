"""Durable room observations and bounded follow-up work."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta


def initialize_room_queue(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS room_presence (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            occupied INTEGER,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS room_events (
            id INTEGER PRIMARY KEY,
            event TEXT NOT NULL,
            expected_presence INTEGER,
            depth INTEGER NOT NULL DEFAULT 0,
            run_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            decision_id INTEGER REFERENCES decisions(id)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS room_events_due ON room_events(status, run_at)
    """)


def presence(db: sqlite3.Connection) -> bool | None:
    row = db.execute("SELECT occupied, updated_at FROM room_presence WHERE singleton = 1").fetchone()
    cutoff = datetime.now(UTC) - timedelta(seconds=75)
    if row and row["occupied"] is not None and datetime.fromisoformat(row["updated_at"]) > cutoff:
        return bool(row["occupied"])
    return None


def enqueue(
    db: sqlite3.Connection, event: dict, *, expected_presence: bool | None = None,
    delay: int = 0, depth: int = 0,
) -> int:
    run_at = datetime.now(UTC) + timedelta(seconds=delay)
    result = db.execute("""
        INSERT INTO room_events(event, expected_presence, depth, run_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (json.dumps(event), expected_presence, depth, run_at.isoformat(),
          (run_at + timedelta(minutes=5)).isoformat()))
    return result.lastrowid


def observe_presence(db: sqlite3.Connection, occupied: bool | None) -> int | None:
    now = datetime.now(UTC).isoformat()
    with db:
        db.execute("BEGIN IMMEDIATE")
        old = presence(db)
        db.execute("""
            INSERT INTO room_presence VALUES (1, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                occupied = excluded.occupied, updated_at = excluded.updated_at
        """, (occupied, now))
        if occupied is None or old == occupied or (old is None and not occupied):
            return None
        summary = ("The room camera now detects a person. Their identity is unknown."
                   if occupied else "The camera no longer detects a person in the room.")
        return enqueue(db, {"kind": "presence", "summary": summary}, expected_presence=occupied)


def next_event(db: sqlite3.Connection) -> sqlite3.Row | None:
    now = datetime.now(UTC).isoformat()
    while True:
        row = db.execute("""
            SELECT * FROM room_events WHERE status = 'pending' AND run_at <= ?
            ORDER BY run_at, id LIMIT 1
        """, (now,)).fetchone()
        if row is None or row["expires_at"] > now:
            return row
        with db:
            db.execute("UPDATE room_events SET status = 'expired' WHERE id = ?", (row["id"],))


def retry_event(db: sqlite3.Connection, event: sqlite3.Row) -> None:
    attempts = event["attempts"] + 1
    delay = datetime.now(UTC) + timedelta(seconds=15 * 2 ** (attempts - 1))
    with db:
        db.execute("UPDATE room_events SET status = ?, attempts = ?, run_at = ? WHERE id = ?",
                   ("failed" if attempts >= 3 else "pending", attempts, delay.isoformat(), event["id"]))

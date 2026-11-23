"""SQLite storage for decisions, feedback, and shared local state."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_actions import apply_local_action, initialize_actions
from room_queue import initialize_room_queue
from transcript_memory import fts_query, initialize_transcripts

STATE_PATH = Path(__file__).resolve().parent / "state" / "agent.sqlite3"


def open_memory(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    try:
        database.execute("PRAGMA foreign_keys = ON")
        if database.execute("PRAGMA user_version").fetchone()[0] < 8:
            with database:
                database.execute("BEGIN IMMEDIATE")
                version = database.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    initialize_state(database)
                if version < 2:
                    initialize_room_queue(database)
                if version < 3:
                    database.execute("""CREATE TABLE IF NOT EXISTS room_slow_stages (
                        stage TEXT PRIMARY KEY, occurrences INTEGER NOT NULL, worst_ms INTEGER NOT NULL
                    )""")
                if version < 4:
                    database.execute("""CREATE TABLE IF NOT EXISTS room_voice_latency (
                        decision_id INTEGER PRIMARY KEY REFERENCES decisions(id), elapsed_ms INTEGER NOT NULL
                    )""")
                if version < 5:
                    database.execute("""CREATE TABLE IF NOT EXISTS room_batches (
                        request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, receipts TEXT NOT NULL
                    )""")
                    database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS inbox_search USING fts5(text)")
                    database.execute("INSERT OR REPLACE INTO inbox_search(rowid, text) SELECT id, text FROM inbox")
                    database.execute("""CREATE TRIGGER IF NOT EXISTS inbox_search_insert AFTER INSERT ON inbox BEGIN
                        INSERT INTO inbox_search(rowid,text) VALUES (new.id,new.text); END""")
                    database.execute("""CREATE TRIGGER IF NOT EXISTS inbox_search_update AFTER UPDATE OF text ON inbox BEGIN
                        UPDATE inbox_search SET text=new.text WHERE rowid=old.id; END""")
                    database.execute("""CREATE TRIGGER IF NOT EXISTS inbox_search_delete AFTER DELETE ON inbox BEGIN
                        DELETE FROM inbox_search WHERE rowid=old.id; END""")
                    database.execute("""CREATE TABLE IF NOT EXISTS room_plans (
                        id TEXT PRIMARY KEY, request TEXT NOT NULL, steps TEXT NOT NULL, remaining TEXT NOT NULL
                    )""")
                if version < 6:
                    from goal_store import initialize_goals
                    initialize_goals(database)
                if version < 7:
                    from visitor_memory import initialize
                    initialize(database)
                if version < 8:
                    from commitment_memory import schema
                    schema(database)
                    database.execute("PRAGMA user_version = 8")
    except sqlite3.Error:
        database.close()
        raise
    return database


def initialize_state(database: sqlite3.Connection) -> None:
    database.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY,
            recorded_at TEXT NOT NULL,
            event TEXT NOT NULL,
            proposal TEXT NOT NULL,
            feedback TEXT,
            reviewed_at TEXT
        )
    """)
    initialize_actions(database)
    initialize_transcripts(database)
    database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS feedback_search USING fts5(feedback, event)")
    database.execute("""
        INSERT INTO feedback_search (rowid, feedback, event)
        SELECT id, feedback, event FROM decisions WHERE feedback IS NOT NULL
    """)
    database.execute("""
        CREATE TRIGGER IF NOT EXISTS feedback_index_update AFTER UPDATE OF feedback ON decisions BEGIN
            DELETE FROM feedback_search WHERE rowid = new.id;
            INSERT INTO feedback_search (rowid, feedback, event)
            SELECT new.id, new.feedback, new.event WHERE new.feedback IS NOT NULL;
        END
    """)


def record_decision(
    database: sqlite3.Connection, event: dict, proposal: dict, *, apply: bool = False
) -> int:
    with database:
        return insert_decision(database, event, proposal, apply=apply)


def insert_decision(
    database: sqlite3.Connection, event: dict, proposal: dict, *, apply: bool,
    allow_speech: bool = False, receipt_proposal: dict | None = None,
) -> int:
    """Insert a receipt and effect in a transaction owned by the caller."""
    row = database.execute(
        "INSERT INTO decisions (recorded_at, event, proposal) VALUES (?, ?, ?)",
        (datetime.now(UTC).isoformat(), json.dumps(event),
         json.dumps(proposal if receipt_proposal is None else receipt_proposal)),
    )
    decision_id = row.lastrowid
    if apply:
        apply_local_action(database, decision_id, proposal, allow_speech=allow_speech)
    return decision_id


def review_decision(database: sqlite3.Connection, decision_id: int, feedback: str) -> None:
    feedback = feedback.strip()
    if not 1 <= len(feedback) <= 400:
        raise ValueError("Feedback must contain 1 to 400 characters.")
    with database:
        row = database.execute(
            "UPDATE decisions SET feedback = ?, reviewed_at = ? WHERE id = ?",
            (feedback, datetime.now(UTC).isoformat(), decision_id),
        )
        if not row.rowcount:
            raise ValueError(f"No decision with ID {decision_id}.")


def decisions(database: sqlite3.Connection) -> list[dict]:
    return [
        {
            "id": row["id"], "recorded_at": row["recorded_at"],
            "mode": "shadow" if row["status"] is None else "local",
            "event": json.loads(row["event"]), "proposal": json.loads(row["proposal"]),
            "feedback": row["feedback"], "action": row["status"],
        }
        for row in database.execute("""
            SELECT decisions.*, action_outcomes.status
            FROM decisions LEFT JOIN action_outcomes ON action_outcomes.decision_id = decisions.id
            ORDER BY decisions.id DESC LIMIT 10
        """)
    ]


def recent_context(database: sqlite3.Connection, query: str = "") -> dict[str, list[dict]]:
    cutoff = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
    recent = database.execute(
        """SELECT recorded_at, event, proposal, action_outcomes.status FROM decisions
           LEFT JOIN action_outcomes ON action_outcomes.decision_id = decisions.id
           WHERE recorded_at >= ? ORDER BY id DESC LIMIT 3""",
        (cutoff,),
    ).fetchall()
    reviewed = database.execute(
        """SELECT decisions.id, event, proposal, feedback, action_outcomes.status FROM decisions
           LEFT JOIN action_outcomes ON action_outcomes.decision_id = decisions.id
           WHERE reviewed_at IS NOT NULL ORDER BY reviewed_at DESC, id DESC LIMIT 3"""
    ).fetchall()
    match = fts_query(query)
    if match:
        relevant = database.execute("""
            SELECT decisions.id, decisions.event, proposal, decisions.feedback, action_outcomes.status
            FROM feedback_search JOIN decisions ON decisions.id = feedback_search.rowid
            LEFT JOIN action_outcomes ON action_outcomes.decision_id = decisions.id
            WHERE feedback_search MATCH ? ORDER BY rank, decisions.id DESC LIMIT 3
        """, (match,)).fetchall()
        combined = {row["id"]: row for row in (*relevant, *reviewed)}
        reviewed = list(combined.values())[:3]

    return {
        "recent_observations": [
            {
                "recorded_at": row["recorded_at"], "event": json.loads(row["event"]),
                "decision": json.loads(row["proposal"]), "action_status": row["status"] or "shadow",
            }
            for row in reversed(recent)
        ],
        "owner_feedback": [
            {
                "event": json.loads(row["event"]), "decision": json.loads(row["proposal"]), "action_status": row["status"] or "shadow",
                "feedback": row["feedback"],
            }
            for row in reversed(reviewed)
        ],
    }

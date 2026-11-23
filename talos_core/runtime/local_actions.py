"""Local actions with transactional records and a private inbox."""

import sqlite3
from datetime import UTC, datetime, timedelta

MAX_REMINDER_MINUTES = 30 * 24 * 60
LOCAL_WRITES = {
    "log_alert": "alert",
    "set_reminder": "reminder",
    "create_task": "task",
    "save_note": "note",
}


def initialize_actions(database: sqlite3.Connection) -> None:
    database.execute("""
        CREATE TABLE IF NOT EXISTS action_outcomes (
            decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
            status TEXT NOT NULL
        )
    """)
    database.execute("""
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY REFERENCES decisions(id),
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            due_at TEXT NOT NULL,
            acknowledged_at TEXT
        )
    """)


def apply_local_action(
    database: sqlite3.Connection, decision_id: int, proposal: dict, *, allow_speech: bool = False
) -> None:
    """Apply one permitted effect inside the decision's existing transaction."""
    status = "skipped"
    if proposal["disposition"] == "act":
        status = "blocked"
        tool = proposal["tool"]
        minutes = proposal.get("remind_in_minutes", 0)
        valid_reminder = (
            tool == "set_reminder" and type(minutes) is int and 1 <= minutes <= MAX_REMINDER_MINUTES
        )
        speech = tool == "speak" and allow_speech
        if speech or valid_reminder or (tool in LOCAL_WRITES and tool != "set_reminder"):
            due_at = datetime.now(UTC) + timedelta(minutes=minutes if valid_reminder else 0)
            database.execute(
                "INSERT INTO inbox (id, kind, text, due_at) VALUES (?, ?, ?, ?)",
                (decision_id, "speech" if speech else LOCAL_WRITES[tool], proposal["detail"], due_at.isoformat()),
            )
            status = "applied"
    database.execute(
        "INSERT INTO action_outcomes (decision_id, status) VALUES (?, ?)", (decision_id, status)
    )


def inbox_items(
    database: sqlite3.Connection, *, due_only: bool = False, limit: int = -1
) -> list[dict]:
    query = "SELECT id, kind, text, due_at FROM inbox WHERE acknowledged_at IS NULL"
    parameters = ()
    if due_only:
        query += " AND kind IN ('alert', 'reminder') AND due_at <= ?"
        parameters = (datetime.now(UTC).isoformat(),)
    rows = database.execute(query + " ORDER BY due_at, id LIMIT ?", (*parameters, limit))
    return [dict(row) for row in rows]


def reminder_list(database):
    rows = database.execute("""SELECT id,text,due_at FROM inbox WHERE kind='reminder'
                              AND acknowledged_at IS NULL ORDER BY due_at,id LIMIT 11""").fetchall()
    lines = [f"#{item} · {datetime.fromisoformat(due).astimezone():%a %d %b %H:%M %Z} · {text}"
             for item, text, due in rows[:10]]
    if len(rows) > 10:
        lines.append("Showing the first ten pending reminders.")
    return "\n".join(lines) or "You have no pending reminders."


def next_reminder(database, spoken):
    row = database.execute("""SELECT id,text,due_at FROM inbox WHERE kind='reminder'
                             AND acknowledged_at IS NULL ORDER BY due_at,id LIMIT 1""").fetchone()
    if row is None:
        return "You have no pending reminders."
    item, text, due = row
    when = datetime.fromisoformat(due).astimezone()
    timing = "was due" if when <= datetime.now(UTC) else "is due"
    reply = f"Your next reminder {timing} {when:%a %d %b at %H:%M %Z}."
    return reply if spoken else f"{reply} #{item} · {text}"


def close_named_item(database, kind, item_id, apply):
    verb = "Completed" if kind == "task" else "Cancelled"
    if not apply:
        return "Shadow mode is on. No item was changed."
    with database:
        changed = database.execute(
            "UPDATE inbox SET acknowledged_at=? WHERE id=? AND kind=? AND acknowledged_at IS NULL",
            (datetime.now(UTC).isoformat(), item_id, kind)).rowcount
        if changed:
            return f"{verb} {kind} #{item_id}."
        exists = database.execute("SELECT 1 FROM inbox WHERE id=? AND kind=?", (item_id, kind)).fetchone()
        return f"{kind.title()} #{item_id} is already closed." if exists else f"No {kind} with ID {item_id}."


def acknowledge(database: sqlite3.Connection, item_id: int) -> None:
    with database:
        result = database.execute(
            "UPDATE inbox SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
            (datetime.now(UTC).isoformat(), item_id),
        )
        if result.rowcount == 0:
            raise ValueError(f"No open inbox item with ID {item_id}.")

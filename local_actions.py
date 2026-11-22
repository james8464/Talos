"""Local actions with transactional records and a private inbox."""

import re
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta

MAX_REMINDER_MINUTES = 30 * 24 * 60
LOCAL_WRITES = {
    "log_alert": "alert",
    "set_reminder": "reminder",
    "create_task": "task",
    "save_note": "note",
}


MAC_APPS = {
    "Calendar": "com.apple.iCal",
    "Contacts": "com.apple.AddressBook",
    "Finder": "com.apple.finder",
    "Mail": "com.apple.mail",
    "Messages": "com.apple.MobileSMS",
    "Notes": "com.apple.Notes",
    "Preview": "com.apple.Preview",
    "Reminders": "com.apple.reminders",
    "Safari": "com.apple.Safari",
}
_APP_REQUEST = re.compile(
    r"(?:(?:hey )?talos[, ]+)?(?:(?:can|could|would) you )?(?:please )?(?:open|launch) "
    r"(?:the app )?(" + "|".join(MAC_APPS) + r")(?: app)?[.!?]?", re.I | re.ASCII
)


def requested_app(text):
    match = _APP_REQUEST.fullmatch(text.strip())
    return match[1].capitalize() if match else None


def open_mac_app(name, *, enabled, direct):
    action = "blocked"
    if not direct:
        reply = "Start a new addressed request or type the app-opening command."
    elif not enabled:
        reply = "App opening is unavailable in this room."
    else:
        try:
            process = subprocess.run(["/usr/bin/open", "-b", MAC_APPS[name]],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if process.returncode == 0:
                action, reply = "accepted", f"macOS accepted the request to open {name}."
            else:
                action, reply = "failed", f"macOS could not open {name}."
        except subprocess.TimeoutExpired:
            action, reply = "unverified", f"I couldn't verify whether macOS opened {name}."
        except OSError:
            action, reply = "failed", f"I couldn't start the macOS launcher for {name}."
    return {"tool": "open_app", "action": action, "reply": reply}


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


def acknowledge(database: sqlite3.Connection, item_id: int) -> None:
    with database:
        result = database.execute(
            "UPDATE inbox SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
            (datetime.now(UTC).isoformat(), item_id),
        )
        if result.rowcount == 0:
            raise ValueError(f"No open inbox item with ID {item_id}.")

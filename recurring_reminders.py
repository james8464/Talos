"""Persistent local reminders backed by the adapted OpenClaw calendar engine."""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from goal_store import _inbox
from room_work import saved_brief, topic_terms

ENGINE = Path(__file__).resolve().parent / "talos_core/scheduling/next.mjs"
CREATE = re.compile(
    r"remind me every (day|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday) at (\d{1,2}):(\d{2})(?: (am|pm))?(?: in ([A-Za-z_]+/[A-Za-z_/+-]+))? to (.+)",
    re.I | re.S,
)
DAYS = dict(
    zip(
        "sunday monday tuesday wednesday thursday friday saturday".split(),
        map(str, range(7)),
    )
)
DAYS.update(day="*", weekday="1-5")


def next_due(spec, now):
    try:
        call = subprocess.run(
            ["node", str(ENGINE)],
            input=json.dumps({"schedule": spec, "now": int(now.timestamp() * 1000)}),
            capture_output=True,
            text=True,
            timeout=6,
            check=True,
        )
        millis = json.loads(call.stdout)
        if type(millis) is int:
            return datetime.fromtimestamp(millis / 1000, UTC)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    raise ValueError(
        "Could not calculate the next reminder. Check the timezone and Node.js installation."
    )


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS recurring_reminders(
        id INTEGER PRIMARY KEY, request_id TEXT UNIQUE, description TEXT NOT NULL, detail TEXT NOT NULL,
        spec TEXT NOT NULL, due TEXT NOT NULL, receipt INTEGER REFERENCES inbox(id), active INTEGER NOT NULL)""")


def handle(db, text, *, voice, apply, request_id=None):
    text = text.strip()
    brief = re.fullmatch(r"brief me on (.{1,160}?) every (.+)", text, re.I)
    create = CREATE.fullmatch(
        f"remind me every {brief[2]} to {brief[1]}" if brief else text
    )

    listing = re.fullmatch(
        r"(?:list|show) (?:my )?(?:recurring reminders|routines)[.!?]?", text, re.I
    )
    cancel = re.fullmatch(
        r"cancel (?:recurring reminder|routine) #?(\d{1,17})[.!]?", text, re.I
    )
    if not (create or listing or cancel):
        return None
    if voice:
        return {"reply": "Manage recurring reminders in the private message field."}
    initialize(db)
    if listing:
        rows = db.execute(
            "SELECT id,description,detail FROM recurring_reminders WHERE active=1 ORDER BY due,id"
        )
        return {
            "private": True,
            "reply": "\n".join(f"#{i} · {d} · {body}" for i, d, body in rows)
            or "No active recurring reminders.",
        }
    if not apply:
        return {"reply": "Recurring reminder preview. Changes are off in this room."}
    now = datetime.now(UTC)
    if cancel:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT receipt FROM recurring_reminders WHERE id=? AND active=1",
                (cancel[1],),
            ).fetchone()
            if not row:
                return {"reply": "No active recurring reminder with that ID."}
            db.execute(
                "UPDATE recurring_reminders SET active=0 WHERE id=?", (cancel[1],)
            )
            db.execute(
                "UPDATE inbox SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                (now.isoformat(), row[0]),
            )
        return {"reply": f"Cancelled recurring reminder #{cancel[1]}."}
    day, hour, minute, meridiem, zone, detail = create.groups()
    hour, minute = int(hour), int(minute)
    if minute > 59 or (meridiem and not 1 <= hour <= 12) or hour > 23:
        raise ValueError("Choose a valid hour and minute.")
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    spec = {"kind": "cron", "expr": f"{minute} {hour} * * {DAYS[day.lower()]}"}
    if zone:
        spec["tz"] = zone
    if brief:
        if not topic_terms(detail):
            raise ValueError("Name a project or client for the scheduled briefing.")
        spec["talosAction"] = "brief"
    description = (
        f"Every {day.lower()} at {hour:02d}:{minute:02d} {zone or 'local time'}"
    )
    if brief:
        description = "Briefing · " + description
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
    ):
        raise ValueError("Invalid request ID.")
    label = "Scheduled briefing" if brief else "Recurring reminder"

    def existing_receipt():
        if request_id is None:
            return None
        prior = db.execute(
            "SELECT id,description,detail FROM recurring_reminders WHERE request_id=?", (request_id,)
        ).fetchone()
        if prior is None:
            return None
        if (prior[1], prior[2]) != (description, detail):
            raise ValueError("Request ID already used for another recurrence.")
        return {"private": True, "reply": f"{label} #{prior[0]} · {description} · {detail}"}

    prior = existing_receipt()
    if prior is not None:
        return prior
    due = next_due(spec, now)
    with db:
        db.execute("BEGIN IMMEDIATE")
        prior = existing_receipt()
        if prior is not None:
            return prior
        receipt = (
            None
            if brief
            else _inbox(
                db, "reminder", detail, due, now, event_kind="private_recurring"
            )
        )
        ident = db.execute(
            """INSERT INTO recurring_reminders(request_id,description,detail,spec,due,receipt,active)
            VALUES(?,?,?,?,?,?,1)""",
            (
                request_id,
                description,
                detail,
                json.dumps(spec),
                due.isoformat(),
                receipt,
            ),
        ).lastrowid
    return {"private": True, "reply": f"{label} #{ident} · {description} · {detail}"}


def advance(db, now):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='recurring_reminders'"
    ).fetchone():
        return
    rows = db.execute(
        "SELECT id,detail,spec,due FROM recurring_reminders WHERE active=1 AND due<=? ORDER BY due,id LIMIT 8",
        (now.isoformat(),),
    ).fetchall()
    next_by_spec = {}
    for ident, detail, spec, previous in rows:
        schedule = json.loads(spec)
        if spec not in next_by_spec:
            next_by_spec[spec] = next_due(schedule, now)
        due = next_by_spec[spec]
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "UPDATE recurring_reminders SET due=? WHERE id=? AND active=1 AND due=?",
                (due.isoformat(), ident, previous),
            ).rowcount:
                if schedule.get("talosAction") == "brief":
                    summary = saved_brief(db, detail)["reply"]
                    receipt = _inbox(
                        db, "alert", summary, now, now, event_kind="private_recurring"
                    )
                else:
                    receipt = _inbox(
                        db, "reminder", detail, due, now, event_kind="private_recurring"
                    )
                db.execute(
                    "UPDATE recurring_reminders SET receipt=? WHERE id=?",
                    (receipt, ident),
                )

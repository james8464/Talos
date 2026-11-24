"""Room controls for pausing and reactivating saved recurring schedules."""

import json
import re
from datetime import UTC, datetime

from goal_store import _inbox

# OpenClaw src/cron/service/jobs.ts and jobs-scheduling.ts (LICENSE-OpenClaw).
ACTIVE, PAUSED, CANCELLED = 1, -1, 0
CONTROL = re.compile(
    r"(pause|resume|cancel)\s+(?:my\s+|the\s+)?(?:recurring reminder|routine)"
    r"\s+(?:(?:to|for|called|named)\s+)?(.+?)[.!?]*$",
    re.I,
)
HOURS = "zero one two three four five six seven eight nine ten eleven twelve".split()
CLOCK = re.compile(
    r"^(?P<lead>(?:remind me every|brief me on .+? every) \w+ at )"
    r"(?P<hour>\d{1,2}|[a-z]+)(?::(?P<minute>\d{2})| (?P<spoken>fifteen|thirty|forty[- ]five))?"
    r"\s*(?P<period>[ap]\.?\s*m\.?)?(?= (?:to|in) |[.!?]?$)",
    re.I,
)


def normalize_clock(text):
    """Accept the hour and quarter-hour forms returned by native dictation."""
    match = CLOCK.match(text)
    if not match:
        return text
    hour = match["hour"].lower()
    if not hour.isdecimal():
        if hour not in HOURS:
            return text
        hour = str(HOURS.index(hour))
    minute = match["minute"] or {
        "fifteen": "15",
        "thirty": "30",
        "forty five": "45",
    }.get((match["spoken"] or "").lower().replace("-", " "), "00")
    period = re.sub(r"[.\s]", "", match["period"] or "").lower()
    normalized = f"{match['lead']}{hour}:{minute}" + (" " + period if period else "")
    return normalized + text[match.end() :]


def selected(db, selector):
    fields = "SELECT id, active, spec, receipt, detail FROM recurring_reminders"
    spoken_number = re.fullmatch(r"number (\w+)", selector, re.I)
    if spoken_number and spoken_number[1].lower() in HOURS:
        selector = str(HOURS.index(spoken_number[1].lower()))
    numeric = re.fullmatch(r"(?:#|number )?(\d{1,17})", selector, re.I)
    if numeric:
        return db.execute(fields + " WHERE id=?", (int(numeric[1]),)).fetchall()
    # Exact subjects only. Never choose silently among duplicates, or treat % as a wildcard.
    return db.execute(
        fields + " WHERE active!=0 AND rtrim(detail, '.!?')=? COLLATE NOCASE LIMIT 2",
        (selector,),
    ).fetchall()


def track_delivery(db, ident, receipt):
    if receipt is not None:
        db.execute(
            "INSERT OR IGNORE INTO routine_deliveries(routine,receipt) VALUES (?,?)",
            (ident, receipt),
        )


def control(db, match, *, apply, next_due, request_id=None):
    verb, selector = match[1].lower(), match[2].strip()
    if not apply:
        return {"reply": "Routine preview. Changes are off in this room."}
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
    ):
        raise ValueError("Invalid request ID.")
    command = verb + " " + selector.casefold()
    if request_id is not None:
        db.execute("""CREATE TABLE IF NOT EXISTS routine_control_requests(
            request_id TEXT PRIMARY KEY, command TEXT NOT NULL, reply TEXT NOT NULL)""")

    def previous():
        if request_id is None:
            return None
        row = db.execute(
            "SELECT command,reply FROM routine_control_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row and row[0] != command:
            raise ValueError("Request ID already used for another routine change.")
        return row[1] if row else None

    cached = previous()
    if cached is not None:
        return {"reply": cached}
    rows = selected(db, selector)
    if len(rows) != 1:
        return {
            "reply": "More than one routine has that subject. Say its number."
            if rows
            else "I couldn't find that routine."
        }
    ident, status, spec, receipt, detail = rows[0]
    desired = {"pause": PAUSED, "resume": ACTIVE, "cancel": CANCELLED}[verb]
    now = datetime.now(UTC)
    schedule = json.loads(spec)
    due = next_due(schedule, now) if desired == ACTIVE and status == PAUSED else None
    # Schedule calculation can launch Node. Recheck the selection and state after it returns.
    with db:
        db.execute("BEGIN IMMEDIATE")
        cached = previous()
        if cached is not None:
            return {"reply": cached}
        if selected(db, selector) != rows:
            return {
                "reply": "The routine changed while I was checking it. Please ask again."
            }
        if status == CANCELLED:
            reply = f"Routine number {ident} is already cancelled."
        elif status == desired:
            reply = f"Routine number {ident} is already " + (
                "paused." if desired == PAUSED else "active."
            )
        elif due is not None:
            new_receipt = (
                None
                if schedule.get("talosAction") == "brief"
                else _inbox(
                    db, "reminder", detail, due, now, event_kind="private_recurring"
                )
            )
            db.execute(
                "UPDATE recurring_reminders SET active=1, due=?, receipt=? WHERE id=?",
                (due.isoformat(), new_receipt, ident),
            )
            track_delivery(db, ident, new_receipt)
            reply = f"Resumed routine number {ident}. Its next scheduled occurrence is {due.astimezone():%a at %H:%M %Z}."
        else:
            track_delivery(db, ident, receipt)
            db.execute(
                """UPDATE inbox SET acknowledged_at=? WHERE acknowledged_at IS NULL
                        AND id IN (SELECT receipt FROM routine_deliveries WHERE routine=?)""",
                (now.isoformat(), ident),
            )
            db.execute("DELETE FROM routine_deliveries WHERE routine=?", (ident,))
            db.execute(
                "UPDATE recurring_reminders SET active=?,receipt=NULL WHERE id=?",
                (desired, ident),
            )
            reply = f"{'Paused' if desired == PAUSED else 'Cancelled'} routine number {ident}."
        if request_id is not None:
            db.execute(
                "INSERT INTO routine_control_requests VALUES(?,?,?)",
                (request_id, command, reply),
            )
    return {"reply": reply}

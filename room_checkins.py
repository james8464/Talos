"""Explicit room check-ins with separate playback and human acknowledgement."""
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from agent_memory import insert_decision
from room_dialogue import decision, explicit_reminder

REQUEST = re.compile(r"check (?:in (?:with|on) me|on me) in (.+)", re.I)
SELF_REPORT = re.compile(r"i(?: feel|(?: am|'m|’m)(?: feeling)?) (?:unwell|sick|under the weather)", re.I)
ANSWER = re.compile(r"(?:i(?: am|'m|’m) (?:ok(?:ay)?|fine)|i am here|i'm here|check[- ]?in complete)", re.I)
CANCEL = re.compile(r"(?:cancel|stop) (?:my |the )?check[- ]?in", re.I)
QUESTION = "Checking in as you asked. How are you doing?"
RETRY = "One last check-in as you asked. You can say I’m okay."
GRACE = timedelta(seconds=120)
FOLLOW_UP = timedelta(minutes=10)


def exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='room_checkins'").fetchone() is not None


def expire(db, now):
    # Hermes's one-shot grace policy: retire overdue unattended deliveries after a restart.
    cutoff = (now - GRACE).isoformat()
    overdue = db.execute("""SELECT 1 FROM room_checkins c JOIN inbox i ON i.id=c.delivery
        WHERE c.status IN ('scheduled','offered','awaiting reply') AND i.due_at<? LIMIT 1""", (cutoff,)).fetchone()
    if not overdue:
        return
    db.execute("""UPDATE inbox SET acknowledged_at=? WHERE acknowledged_at IS NULL AND due_at<?
        AND id IN (SELECT delivery FROM room_checkins WHERE status IN ('scheduled','offered','awaiting reply'))""",
        (now.isoformat(), cutoff))
    db.execute("""UPDATE room_checkins SET status='expired' WHERE status IN ('scheduled','offered','awaiting reply')
        AND delivery IN (SELECT id FROM inbox WHERE due_at<? AND acknowledged_at IS NOT NULL)""", (cutoff,))


def handle(db, text, *, apply, request_id=None, now=None):
    words = re.sub(r"^(?:hey[ ,]+)?talos[ ,]+", "", text.strip(), flags=re.I).strip().rstrip(".!?").strip()
    if SELF_REPORT.fullmatch(words):
        return {"reply": "Would you like me to check in here later? You can say, ‘Check in with me in three hours.’"}
    request = REQUEST.fullmatch(words)
    answer, cancel = ANSWER.fullmatch(words), CANCEL.fullmatch(words)
    if not (request or answer or cancel):
        return None
    if not request and not exists(db):
        return {"reply": "There’s no active check-in."} if cancel else None
    now = now or datetime.now(UTC)
    reminder = explicit_reminder(f"Remind me in {request[1]} to check in") if request else None
    if request and not reminder:
        return {"reply": "How long from now? Try ‘check in with me in three hours’."}
    if not apply:
        return {"reply": "Check-ins are in preview mode."} if request or cancel else None
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS room_checkins(
            token TEXT PRIMARY KEY, delivery INTEGER NOT NULL UNIQUE, status TEXT NOT NULL,
            minutes INTEGER NOT NULL)""")
        expire(db, now)
        if request:
            key = str(UUID(request_id)) if request_id else str(uuid4())
            minutes = reminder["remind_in_minutes"]
            previous = db.execute("SELECT minutes,status FROM room_checkins WHERE token=?", (key,)).fetchone()
            if previous:
                if previous[0] != minutes:
                    raise ValueError("The check-in request ID was reused with a different delay.")
                return {"reply": f"That check-in is already {previous[1]}."}
        active = db.execute("SELECT token,delivery,status FROM room_checkins WHERE status IN ('scheduled','offered','awaiting reply')").fetchone()
        if request:
            if active:
                return {"reply": "A check-in is already active. You can cancel it first."}
            delivery = schedule(db, now, minutes, QUESTION)
            db.execute("INSERT INTO room_checkins VALUES(?,?,'scheduled',?)", (key, delivery, minutes))
            return {"reply": f"I’ll check in here in {minutes} minutes. If you don’t reply, I’ll ask once more ten minutes later.",
                    "id": delivery}
        if not active and answer:
            active = db.execute("""SELECT c.token,c.delivery,c.status FROM room_checkins c JOIN inbox i
                ON i.id=c.delivery WHERE c.status='unanswered' AND i.acknowledged_at>=?
                ORDER BY i.acknowledged_at DESC LIMIT 1""", ((now-FOLLOW_UP).isoformat(),)).fetchone()
        if not active or (answer and active[2] not in ("offered", "awaiting reply", "unanswered")):
            return {"reply": "There’s no active check-in."} if cancel else None
        db.execute("UPDATE inbox SET acknowledged_at=coalesce(acknowledged_at,?) WHERE id=?", (now.isoformat(), active[1]))
        db.execute("UPDATE room_checkins SET status=? WHERE token=?", ("cancelled" if cancel else "answered", active[0]))
        return {"reply": "Check-in cancelled." if cancel else "Thanks for letting me know. I’ve closed the check-in."}


def schedule(db, now, minutes, question):
    receipt = insert_decision(db, {"kind": "room_checkin", "summary": "Requested room check-in"},
                              decision("set_reminder", question, minutes), apply=True)
    db.execute("UPDATE inbox SET due_at=? WHERE id=?", ((now + timedelta(minutes=minutes)).isoformat(), receipt))
    return receipt


def offered(db, delivery):
    with db:
        db.execute("UPDATE room_checkins SET status='offered' WHERE delivery=? AND status='scheduled'", (delivery,))


def settled(db, delivery, now, outcome):
    # A playback receipt settles only the matching delivery, never the human response.
    if not exists(db):
        return
    row = db.execute("SELECT token,status FROM room_checkins WHERE delivery=?", (delivery,)).fetchone()
    if row and row[1] in ("scheduled", "offered", "awaiting reply") and outcome != "played":
        db.execute("UPDATE room_checkins SET status=? WHERE token=?", (outcome, row[0]))
    elif row and row[1] in ("scheduled", "offered"):
        next_id = schedule(db, now, int(FOLLOW_UP.total_seconds() // 60), RETRY)
        db.execute("UPDATE room_checkins SET status='awaiting reply',delivery=? WHERE token=?", (next_id, row[0]))
    elif row and row[1] == "awaiting reply":
        db.execute("UPDATE room_checkins SET status='unanswered' WHERE token=?", (row[0],))

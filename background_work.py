"""Persistent, restartable local work orders. Only the commit transaction applies effects."""

import re
import uuid
from datetime import UTC, datetime, timedelta

from room_dialogue import explicit_reminder, explicit_task
from room_work import apply_order, clauses, note_request, propose_work, work_order

COMMAND = re.compile(
    r"(?:work on this in the background|queue this work)\s*:\s*(.+)", re.I | re.S
)


def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS background_work(
        id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, task TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        available TEXT NOT NULL, claim TEXT, result TEXT NOT NULL DEFAULT '')""")


def handle(db, text, *, voice, apply, request_id):
    job = COMMAND.fullmatch(text.strip())
    inspect = re.fullmatch(
        r"(?:list|show) (?:my )?background (?:work|jobs)[.!?]?", text.strip(), re.I
    )
    cancel = re.fullmatch(
        r"cancel background job #?(\d{1,17})[.!]?", text.strip(), re.I
    )
    if not (job or inspect or cancel):
        return None
    if voice:
        return {"reply": "Use the private message field for background work."}
    ensure(db)
    if inspect:
        rows = db.execute(
            "SELECT id,status,task,result FROM background_work ORDER BY id DESC LIMIT 10"
        )
        return {
            "private": True,
            "reply": "\n".join(
                f"#{i} {phase} · {task}" + (f" · {result}" if result else "")
                for i, phase, task, result in rows
            )
            or "No background jobs.",
        }
    if not apply:
        return {"reply": "Background work is disabled in shadow mode."}
    with db:
        db.execute("BEGIN IMMEDIATE")
        if cancel:
            changed = db.execute(
                "UPDATE background_work SET status='cancelled', claim=NULL WHERE id=? AND status IN ('queued','running')",
                (cancel[1],),
            ).rowcount
            return {
                "reply": f"Cancelled background job #{cancel[1]}."
                if changed
                else "That job is already closed or unknown."
            }
        task = job[1].strip()
        if not task or len(task) > 800:
            raise ValueError("Describe up to 800 characters of background work.")
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError("The background request ID must be a UUID.")
        key = str(uuid.uuid4() if request_id is None else uuid.UUID(request_id))
        db.execute(
            "INSERT INTO background_work(request_id,task,available) VALUES(?,?,?) ON CONFLICT(request_id) DO NOTHING",
            (key, task, datetime.now(UTC).isoformat()),
        )
        row = db.execute(
            "SELECT id,task,status FROM background_work WHERE request_id=?", (key,)
        ).fetchone()
        if row[1] != task:
            raise ValueError("This request ID belongs to another job.")
    return {"private": True, "reply": f"Background job #{row[0]} · {row[2]}."}


def run_one(db, generate, model, *, ready, notify=None):
    """Process one claim. Return true when the worker should check for more due jobs."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='background_work'"
    ).fetchone():
        return False
    now = datetime.now(UTC)
    sql = """SELECT * FROM background_work WHERE status IN ('queued','running') AND available<=?
             ORDER BY available,id LIMIT 1"""
    if not db.execute(sql, (now.isoformat(),)).fetchone():
        return False
    claim = str(uuid.uuid4())
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(sql, (now.isoformat(),)).fetchone()
        if not row:
            return False
        db.execute(
            "UPDATE background_work SET status='running',claim=?,available=? WHERE id=?",
            (claim, (now + timedelta(minutes=5)).isoformat(), row["id"]),
        )
    def report(message):
        if notify is not None:
            notify(f"Work order #{row['id']} · {message}")

    report("claimed")
    try:
        deterministic = work_order(row["task"])
        if deterministic and deterministic.get("steps"):
            steps, remaining = deterministic["steps"], ""
        elif len(clauses(row["task"])) == 1 and (
            step := explicit_reminder(row["task"])
            or explicit_task(row["task"])
            or note_request(row["task"])
        ):
            steps, remaining = [step], ""
        elif (ready() if callable(ready) else ready):
            report("planning with the local model")
            steps, remaining = propose_work(generate, model, row["task"])
        else:
            with db:
                updated = db.execute(
                    "UPDATE background_work SET status='queued',claim=NULL,available=? WHERE claim=?",
                    ((now + timedelta(seconds=30)).isoformat(), claim),
                )
            if updated.rowcount:
                report("waiting for model readiness; retry in 30s")
            return True
        with db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM background_work WHERE claim=?", (claim,)
            ).fetchone():
                return False
            if remaining or not steps:
                status, summary = (
                    "needs clarification",
                    "Unresolved instructions: " + (remaining or row["task"]),
                )
            else:
                receipts, summary, _ = apply_order(
                    db,
                    steps,
                    {"kind": "background_work", "summary": row["task"]},
                    apply=True,
                    archive=True,
                    request_id=row["request_id"],
                )
                summary += " Receipts " + ", ".join(f"#{r['id']}" for r in receipts)
                status = "done"
            db.execute(
                "UPDATE background_work SET status=?,result=?,claim=NULL WHERE claim=?",
                (status, summary, claim),
            )
        report(status)
    except (ValueError, RuntimeError, OSError) as error:
        # OpenClaw's bounded exponential retry policy, using a persisted deadline.
        attempts = row["attempts"] + 1
        retry = datetime.now(UTC) + timedelta(
            seconds=min(600, 15 * 2 ** min(attempts - 1, 6))
        )
        with db:
            updated = db.execute(
                "UPDATE background_work SET status=?,attempts=?,result=?,available=?,claim=NULL WHERE claim=?",
                (
                    "failed" if attempts >= 4 else "queued",
                    attempts,
                    type(error).__name__,
                    retry.isoformat(),
                    claim,
                ),
            )
        if updated.rowcount:
            report(f"failed after {attempts} attempts" if attempts >= 4 else f"retry scheduled after attempt {attempts}")
    return True

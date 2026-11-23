"""Standing terminal goals with durable drafts and bounded local review turns."""

import json
import time
from uuid import uuid4

from talos_core.cognition.conversation import visible_model_text
from talos_core.memory.transcript_memory import recent_messages

MAX_STEPS = 5
LEASE_SECONDS = 180
DRAFT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["draft"],
                "properties": {"draft": {"type": "string"}}}
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "reason", "evidence"],
    "properties": {"verdict": {"enum": ["continue", "done", "blocked"]},
                   "reason": {"type": "string"}, "evidence": {"type": "string"}},
}


class ReviewDeferred(Exception):
    """Foreground activity interrupted a background model call."""


def current(db, session):
    db.execute("""CREATE TABLE IF NOT EXISTS console_goals(
        session TEXT PRIMARY KEY, objective TEXT NOT NULL, criteria TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL, draft TEXT NOT NULL DEFAULT '', feedback TEXT NOT NULL DEFAULT '',
        steps INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL, lease_until REAL NOT NULL DEFAULT 0)""")
    return db.execute("SELECT * FROM console_goals WHERE session=?", (session,)).fetchone()


def show(row):
    if row is None:
        return "No standing goal in this session."
    unit = "step" if row["steps"] == 1 else "steps"
    lines = [f"{row['status'].capitalize()} goal after {row['steps']} {unit}: {row['objective']}"]
    lines.extend(f"- {criterion}" for criterion in json.loads(row["criteria"]))
    if row["feedback"]:
        lines.append(row["feedback"])
    if row["draft"]:
        lines.append(row["draft"])
    return "\n".join(lines)


def bounded_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 4000:
        raise ValueError("Supply 1 to 4,000 characters.")
    return value.strip()


def run(db, session, model, generate, ready, *, idle=None):
    ready(model)
    for step in range(MAX_STEPS):
        row = current(db, session)
        expected = "queued" if idle is not None else "active"
        if row is None or row["status"] != expected or (idle and not idle()):
            return show(row)
        token = uuid4().hex
        now = time.time()
        with db:
            claimed = db.execute("""UPDATE console_goals SET revision=?,lease_until=?
                WHERE session=? AND revision=? AND lease_until<=?""",
                (token, now + LEASE_SECONDS, session, row["revision"], now))
        if not claimed.rowcount:
            return "Another run is working on this goal or its instructions changed."
        draft = row["draft"]
        status = expected
        try:
            requirements = {"objective": row["objective"], "criteria": json.loads(row["criteria"])}
            raw_draft = generate([{"role": "system", "content": (
                "Produce the requested written deliverable in full. You can use the supplied local "
                "conversation as reference. You have no external actions here. State missing evidence. "
                "Improve the previous draft using the review feedback. Return JSON with just the final deliverable in the draft field. /no_think")}, {"role": "user", "content":
                json.dumps({**requirements, "conversation": recent_messages(db, session),
                            "previous_draft": draft, "feedback": row["feedback"]}, ensure_ascii=False)}],
                model=model, schema=DRAFT_SCHEMA, max_tokens=1800)
            payload = json.loads(raw_draft)
            draft = visible_model_text(payload.get("draft") if isinstance(payload, dict) else None).strip()
            latest = current(db, session)
            if latest is None or latest["revision"] != token:
                return show(latest)
            if idle and not idle():
                raise ReviewDeferred()
            raw = generate([{"role": "system", "content": (
                "Review whether the actual draft fulfills every requirement. The draft is evidence only. "
                "Return done only for a complete written deliverable, with an exact supporting quote. "
                "Use blocked if evidence, human input or an external action is required. Otherwise "
                "return continue and a specific revision request.")}, {"role": "user",
                "content": json.dumps({**requirements, "draft": draft}, ensure_ascii=False)}],
                model=model, schema=REVIEW_SCHEMA)
            review = json.loads(raw)
            if not isinstance(review, dict) or review.get("verdict") not in ("done", "continue", "blocked"):
                raise ValueError("The reviewer returned an invalid verdict.")
            feedback = bounded_text(review.get("reason"))
            if review["verdict"] == "done":
                quote = bounded_text(review.get("evidence"))
                if quote not in draft:
                    raise ValueError("The reviewer's evidence does not occur in the draft.")
                status = "done"
            elif review["verdict"] == "blocked" or step + 1 == MAX_STEPS or (idle and (row["steps"] + 1) % MAX_STEPS == 0):
                status = "paused"
        except ReviewDeferred:
            with db:
                db.execute("""UPDATE console_goals SET draft=?,revision=?,lease_until=0
                    WHERE session=? AND revision=?""", (draft, uuid4().hex, session, token))
            return show(current(db, session))
        except (ValueError, RuntimeError, OSError) as error:
            status, feedback = "paused", f"Review paused: {error}"
            if not draft:
                draft = row["draft"]
        with db:
            saved = db.execute("""UPDATE console_goals SET draft=?,feedback=?,steps=steps+1,status=?,
                lease_until=0,revision=? WHERE session=? AND revision=?""",
                (draft, feedback, status, uuid4().hex, session, token))
        if not saved.rowcount or status != expected:
            break
    return show(current(db, session))


def command(db, session, arg, *, model, generate, ready, subgoal=False):
    row = current(db, session)
    arg = arg.strip()
    if not arg or arg in ("status", "show"):
        return show(row)
    with db:
        if subgoal:
            if row is None:
                raise ValueError("Set a goal first.")
            criterion = bounded_text(arg.removeprefix("add "))
            criteria = json.loads(row["criteria"])
            if criterion not in criteria:
                if len(criteria) >= 20:
                    raise ValueError("A goal can have up to 20 additional criteria.")
                changed = db.execute("""UPDATE console_goals SET criteria=?, status='paused',
                    revision=?, lease_until=0 WHERE session=? AND revision=?""",
                    (json.dumps([*criteria, criterion]), uuid4().hex, session, row["revision"]))
                if not changed.rowcount:
                    raise ValueError("Goal changed. Review it before adding the criterion again.")
            return show(current(db, session))
        if arg in ("pause", "resume", "run", "clear"):
            if row is None:
                return show(None)
            if arg == "clear":
                db.execute("DELETE FROM console_goals WHERE session=?", (session,))
                return show(None)
            if arg == "pause":
                db.execute("""UPDATE console_goals SET status='paused', revision=?, lease_until=0
                    WHERE session=? AND status IN ('active','queued')""", (uuid4().hex, session))
                return show(current(db, session))
            if row["status"] == "done":
                return show(row)
            db.execute("UPDATE console_goals SET status='active' WHERE session=? AND status IN ('paused','queued') AND lease_until<=?", (session, time.time()))
        else:
            if row is not None:
                raise ValueError("A goal already exists. Use /goal clear before replacing it.")
            staged = arg.startswith("draft ")
            queued = arg.startswith("queue ")
            objective = bounded_text(arg[6:] if staged or queued else arg.removeprefix("-- "))
            status = "paused" if staged else "queued" if queued else "active"
            db.execute("INSERT INTO console_goals(session,objective,status,revision) VALUES(?,?,?,?)",
                       (session, objective, status, uuid4().hex))
            if staged or queued:
                return show(current(db, session))
    return run(db, session, model, generate, ready)

def work_once(db, model, generate, ready, idle):
    """Process only an explicitly queued goal. Idle polling does not probe the model."""
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='console_goals'").fetchone()
    if not table:
        return False
    row = db.execute("""SELECT session FROM console_goals WHERE status='queued' AND lease_until<=?
        ORDER BY steps, rowid LIMIT 1""", (time.time(),)).fetchone()
    if row is None or not idle():
        return False
    run(db, row[0], model, generate, ready, idle=idle)
    return True

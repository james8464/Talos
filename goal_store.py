"""Durable meeting goals. Calendar observations come from the Mac, files from the selected folder."""

import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from room_work import saved_brief


def initialize_goals(db):
    db.execute("""CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY, outcome TEXT NOT NULL, topic TEXT NOT NULL, required_doc TEXT NOT NULL,
        folder TEXT NOT NULL, deadline TEXT NOT NULL, lead INTEGER NOT NULL, preference TEXT NOT NULL,
        event_id TEXT NOT NULL, calendar_state TEXT NOT NULL, status TEXT NOT NULL,
        reminder_id INTEGER REFERENCES inbox(id), observation TEXT NOT NULL, checked TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE IF NOT EXISTS goal_changes (
        id INTEGER PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), at TEXT NOT NULL,
        reason TEXT NOT NULL, notification_id INTEGER REFERENCES inbox(id))""")


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (not empty and not value.strip()):
        raise ValueError("Invalid goal text.")
    return value.strip()


def _date(value):
    if not isinstance(value, str):
        raise ValueError("A meeting time with timezone is required.")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Include the timezone in the meeting time.")
    return result.astimezone(UTC)


def _checkpoint(db, goal_id, reason, now, notification=None):
    db.execute(
        "INSERT INTO goal_changes(goal_id,at,reason,notification_id) VALUES (?,?,?,?)",
        (goal_id, now.isoformat(), reason, notification),
    )


def _inbox(db, kind, text, at, now, *, event_kind="goal_update"):
    tool = "set_reminder" if kind == "reminder" else "log_alert"
    event = json.dumps({"kind": event_kind, "summary": text if event_kind == "goal_update" else "Scheduled private notification"})
    proposal = json.dumps({"disposition": "act", "tool": tool, "detail": text})
    row = db.execute(
        "INSERT INTO decisions(recorded_at,event,proposal) VALUES(?,?,?)", (now.isoformat(), event, proposal)
    )
    db.execute(
        "INSERT INTO inbox(id,kind,text,due_at) VALUES(?,?,?,?)", (row.lastrowid, kind, text, at.isoformat())
    )
    db.execute("INSERT INTO action_outcomes VALUES(?, 'applied')", (row.lastrowid,))
    return row.lastrowid


def _row(db, goal_id):
    row = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown goal.")
    return row


def document_evidence(folder, requirement):
    if not requirement:
        return {"state": "not required", "files": []}
    if not folder:
        return {"state": "choose a project folder", "files": []}
    root = Path(folder)
    if not root.is_dir() or root.is_symlink() or root.resolve() != root:
        return {"state": "folder unavailable", "files": []}
    terms = set(re.findall(r"[^\W_]+", requirement.casefold()))
    if not terms:
        return {"state": "name the required document", "files": []}
    matches = []
    visited = 0
    entries = 0
    for directory, dirs, names, dirfd in os.fwalk(root, follow_symlinks=False):
        entries += len(dirs) + len(names)
        if entries > 4096:
            return {"state": "select a smaller project folder", "files": []}
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d.casefold() not in {"archive", "archived", "obsolete", "superseded", "node_modules"}
        )
        for name in sorted(names):
            path = Path(directory, name)
            if name.startswith(".") or path.suffix.lower() not in {".md", ".txt", ".csv"}:
                continue
            words = set(re.findall(r"[^\W_]+", path.stem.casefold()))
            if not terms <= words or words & {"old", "obsolete", "archived", "superseded", "draft"}:
                continue
            visited += 1
            if visited > 100:
                return {"state": "narrow the document name", "files": []}
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    excerpt = handle.read(4096).decode("utf-8", errors="replace").strip()[:600]
            except OSError:
                continue
            if excerpt:
                matches.append(
                    (
                        bool(words & {"current", "corrected", "final"}),
                        {
                            "path": str(path.relative_to(root)),
                            "excerpt": excerpt,
                            "updated": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                        },
                    )
                )
    if not matches:
        return {"state": "missing", "files": []}
    priority = max(flag for flag, _ in matches)
    files = [file for flag, file in matches if flag == priority]
    return {"state": "found" if len(files) == 1 else "multiple possible versions", "files": files[:5]}


def _observe(db, row, now, evidence):
    if evidence is None:
        evidence = document_evidence(row["folder"], row["required_doc"])
    other_topics = (
        r[0] for r in db.execute("SELECT DISTINCT topic FROM goals WHERE topic != ?", (row["topic"],))
    )
    brief = saved_brief(db, row["topic"], exclude_topics=other_topics)
    risks = []
    if row["event_id"] and row["calendar_state"] != "confirmed":
        risks.append("Calendar event needs confirmation")
    if evidence["state"] not in ("found", "not required"):
        risks.append(f"{row['required_doc']}: {evidence['state']}")
    tasks = [item for item in brief["items"] if item["kind"] == "task"]
    remaining = _date(row["deadline"]) - now
    stage = "overdue" if remaining <= timedelta() else "within a day" if remaining <= timedelta(days=1) else "scheduled"
    if tasks and stage != "scheduled":
        risks.append("Open tasks due for review within a day")
    return {"evidence": evidence, "items": brief["items"], "excerpts": brief["excerpts"], "risks": risks, "stage": stage}


def _update_reminder(db, row, now):
    if row["lead"] == 0 or row["calendar_state"] == "missing" or _date(row["deadline"]) <= now:
        if row["reminder_id"]:
            db.execute(
                "UPDATE inbox SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                ("paused-by-goal", row["reminder_id"]),
            )
        return
    due = _date(row["deadline"]) - timedelta(minutes=row["lead"])
    if row["reminder_id"]:
        db.execute(
            """UPDATE inbox SET due_at=?,text=?,acknowledged_at=NULL WHERE id=?
                   AND (acknowledged_at IS NULL OR acknowledged_at='paused-by-goal')""",
            (due.isoformat(), row["outcome"], row["reminder_id"]),
        )
    else:
        item_id = _inbox(db, "reminder", row["outcome"], due, now)
        db.execute("UPDATE goals SET reminder_id=? WHERE id=?", (item_id, row["id"]))


def refresh_in_background(db, goal_id):
    # Read selected files before taking the room's write lock. Discard this observation
    # if a concurrent edit or manual check completed while those files were opening.
    before = _row(db, goal_id)
    if before["status"] != "active":
        return
    now = datetime.now(UTC)
    evidence = document_evidence(before["folder"], before["required_doc"])
    with db:
        db.execute("BEGIN IMMEDIATE")
        after = _row(db, goal_id)
        if (before["revision"], before["checked"]) == (after["revision"], after["checked"]):
            refresh(db, goal_id, now, evidence=evidence)


def refresh(db, goal_id, now=None, *, change="", evidence=None):
    now = now or datetime.now(UTC)
    row = _row(db, goal_id)
    if row["status"] != "active":
        return
    previous = json.loads(row["observation"])
    observation = _observe(db, row, now, evidence)
    new_risks = [r for r in observation["risks"] if r not in previous.get("risks", [])]
    reason = "; ".join(filter(None, [change, *new_risks]))
    approaching = (previous.get("stage") is not None and previous["stage"] != observation["stage"]
                   and observation["stage"] != "scheduled" and observation["risks"])
    if approaching:
        reason = f"The deadline is {observation['stage']}. Check " + "; ".join(observation["risks"]) + "."
    if not reason and previous and observation["risks"] != previous.get("risks"):
        reason = "A previously reported blocker cleared."
    if row["preference"] == "quiet" or approaching or (previous and observation["risks"] != previous.get("risks")):
        db.execute(
            """UPDATE inbox SET acknowledged_at=? WHERE acknowledged_at IS NULL
                   AND id IN (SELECT notification_id FROM goal_changes WHERE goal_id=?)""",
            (now.isoformat(), goal_id),
        )
    if (
        previous
        and not reason
        and observation["evidence"]["state"] == previous.get("evidence", {}).get("state") == "found"
    ):
        latest = observation["evidence"]["files"][0]
        earlier = previous["evidence"]["files"][0]
        if (latest["path"], latest["excerpt"]) != (earlier["path"], earlier["excerpt"]):
            reason = "The required document or its evidence changed."
    if reason:
        notify = (
            previous
            and row["preference"] != "quiet"
            and (approaching or new_risks or change == "Calendar deadline moved." or row["preference"] == "changes")
        )
        item = _inbox(db, "alert", f"{row['topic']}: {reason}", now, now) if notify else None
        _checkpoint(db, goal_id, reason, now, item)
    db.execute(
        "UPDATE goals SET observation=?,checked=? WHERE id=?",
        (json.dumps(observation), now.isoformat(), goal_id),
    )


def save_goal(db, body):
    now = datetime.now(UTC)
    goal_id = str(UUID(_text(body.get("id"), 36))).lower()
    old = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if old and old["status"] != "active":
        raise ValueError("This goal is closed.")
    outcome = _text(body.get("outcome"), 400)
    topic = _text(body.get("topic"), 160)
    required = _text(body.get("required_doc", ""), 100, empty=True)
    folder = _text(body.get("folder", ""), 2048, empty=True)
    if folder and (
        not Path(folder).is_absolute() or Path(folder).resolve() != Path(folder) or not Path(folder).is_dir()
    ):
        raise ValueError("Select an available project folder.")
    date = _date(body.get("deadline"))
    lead, pref = body.get("lead", 15), body.get("preference", "risk")
    if type(lead) is not int or not 0 <= lead <= 43200 or pref not in ("risk", "changes", "quiet"):
        raise ValueError("Invalid reminder or interruption preference.")
    event = _text(body.get("event_id", ""), 1024, empty=True)
    values = (outcome, topic, required, folder, date.isoformat(), lead, pref, event)
    keys = ("outcome", "topic", "required_doc", "folder", "deadline", "lead", "preference", "event_id")
    if old:
        if tuple(old[k] for k in keys) == values:
            return describe(db, goal_id)
        if body.get("revision") != old["revision"]:
            raise ValueError("This goal changed. Reopen it before saving edits.")
        db.execute(
            """UPDATE goals SET outcome=?,topic=?,required_doc=?,folder=?,deadline=?,lead=?,
                   preference=?,event_id=?,calendar_state=?,revision=revision+1 WHERE id=?""",
            (*values, old["calendar_state"] if event == old["event_id"] else "confirmed", goal_id),
        )
    else:
        db.execute(
            """INSERT INTO goals(id,outcome,topic,required_doc,folder,deadline,lead,preference,event_id,
                   calendar_state,status,observation,checked) VALUES(?,?,?,?,?,?,?,?,?,'confirmed','active','{}','')""",
            (goal_id, *values),
        )
    _update_reminder(db, _row(db, goal_id), now)
    _checkpoint(
        db, goal_id, "Updated your instructions." if old else "Started tracking the requested outcome.", now
    )
    refresh(db, goal_id, now)
    return describe(db, goal_id)


def calendar_update(db, body):
    row = _row(db, body.get("id"))
    if row["status"] != "active" or not row["event_id"] or body.get("event_id") != row["event_id"]:
        return
    state = body.get("state")
    if state not in ("confirmed", "missing", "unavailable"):
        raise ValueError("Invalid calendar observation.")
    deadline = _date(body.get("deadline")).isoformat() if state == "confirmed" else row["deadline"]
    if deadline == row["deadline"] and state == row["calendar_state"]:
        return
    if body.get("revision") != row["revision"]:
        raise ValueError("Stale Calendar observation. Refresh the goal.")
    now = datetime.now(UTC)
    db.execute(
        "UPDATE goals SET deadline=?,calendar_state=?,revision=revision+1 WHERE id=?",
        (deadline, state, row["id"]),
    )
    _update_reminder(db, _row(db, row["id"]), now)
    refresh(
        db,
        row["id"],
        now,
        change="Calendar deadline moved." if deadline != row["deadline"] else "Calendar availability changed.",
    )


def pause_goal(db, goal_id, paused):
    row = _row(db, goal_id)
    if row["status"] not in ("active", "paused"):
        raise ValueError("This goal is closed.")
    status = "paused" if paused else "active"
    if row["status"] != status:
        now = datetime.now(UTC)
        db.execute("UPDATE goals SET status=?,revision=revision+1 WHERE id=?", (status, goal_id))
        if paused:
            db.execute(
                "UPDATE inbox SET acknowledged_at='paused-by-goal' WHERE id=? AND acknowledged_at IS NULL",
                (row["reminder_id"],),
            )
            db.execute(
                """UPDATE inbox SET acknowledged_at=? WHERE acknowledged_at IS NULL AND
                       id IN (SELECT notification_id FROM goal_changes WHERE goal_id=?)""",
                (now.isoformat(), goal_id),
            )
        else:
            _update_reminder(db, _row(db, goal_id), now)
            refresh(db, goal_id, now)
        _checkpoint(db, goal_id, "Paused at your request." if paused else "Resumed at your request.", now)
    return describe(db, goal_id)


def close_goal(db, goal_id, status):
    if status not in ("cancelled", "completed"):
        raise ValueError("Use cancelled or completed.")
    row = _row(db, goal_id)
    if row["status"] in ("active", "paused"):
        now = datetime.now(UTC)
        db.execute("UPDATE goals SET status=?,revision=revision+1 WHERE id=?", (status, goal_id))
        db.execute(
            """UPDATE inbox SET acknowledged_at=? WHERE acknowledged_at IS NULL AND
                   (id=? OR id IN (SELECT notification_id FROM goal_changes WHERE goal_id=?))""",
            (now.isoformat(), row["reminder_id"], goal_id),
        )
        _checkpoint(db, goal_id, f"Goal {status} at your request.", now)
    return describe(db, goal_id)


def describe(db, goal_id):
    row = dict(_row(db, goal_id))
    row["observation"] = json.loads(row["observation"])
    row["history"] = [
        dict(r)
        for r in db.execute(
            "SELECT at,reason FROM goal_changes WHERE goal_id=? ORDER BY id DESC LIMIT 8", (goal_id,)
        )
    ]
    return row

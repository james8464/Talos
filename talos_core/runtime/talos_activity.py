"""Local, read-only activity viewer and bounded operational event journal."""

import argparse
import fcntl
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from talos_core.recovery import PollBackoff

STATE = Path(__file__).resolve().parents[2] / "state"
PORT = 8765
JOURNAL = "runtime-activity.sqlite3"
CURRENT_STATE = ContextVar("talos_activity_state", default=None)
PHASES = {
    "Refresh enabled memory sources": "sync",
    "Compare code and review proposals": "source-review",
    "Check project evidence for AI memory review": "memory-review",
    "Generate spoken reply": "voice-stream",
    "Handle spoken turn": "voice-turn",
    "Handle typed turn": "text-turn",
    "Synthesize audio": "audio",
    "Generate console reply": "console-reply",
    "Plan console task": "console-task",
    "Plan a queued room event": "room-event",
    "Run idle model review": "idle-review",
    "Evaluate a code change": "coding-trial",
}


def clean(value):
    return " ".join("".join(char if char.isprintable() else " " for char in str(value)
                            if unicodedata.category(char) != "Cf").split())[:320]


def record(state, component, message):
    """Only pass operational labels and counts. Reporting failures must not block work."""
    try:
        path = state / JOURNAL
        os.close(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600))
        with closing(sqlite3.connect(path, timeout=.15)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "stamp REAL NOT NULL, component TEXT NOT NULL, message TEXT NOT NULL)")
            cursor = db.execute("INSERT INTO events(stamp,component,message) VALUES(?,?,?)",
                                (time.time(), clean(component), clean(message)))
            db.execute("DELETE FROM events WHERE id<=?", (cursor.lastrowid - 512,))
    except (OSError, sqlite3.Error):
        pass


@contextmanager
def active_phase(state, label):
    # Workers take shared locks so simultaneous operations using the same label coexist.
    stream = None
    try:
        if label in PHASES:
            fd = os.open(state / f"activity-{PHASES[label]}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stream = os.fdopen(fd, "rb")
            fcntl.flock(stream, fcntl.LOCK_SH)
    except OSError:
        pass
    try:
        yield
    finally:
        if stream is not None:
            stream.close()


@contextmanager
def operation(state, component, label):
    started = time.monotonic()
    record(state, component, label + " · started")
    token = CURRENT_STATE.set(state)
    try:
        with active_phase(state, label):
            yield
    except BaseException as error:
        record(state, component, f"{label} · ended ({type(error).__name__})")
        raise
    else:
        record(state, component, f"{label} · finished in {time.monotonic()-started:.1f}s")
    finally:
        CURRENT_STATE.reset(token)


def events(state, after=None):
    try:
        uri = (state / JOURNAL).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=.15)) as db:
            if after is None:
                return list(reversed(db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 10").fetchall()))
            return db.execute("SELECT * FROM events WHERE id>? ORDER BY id", (after,)).fetchall()
    except sqlite3.Error:
        return []


def lock_state(path, *, exclusive=False):
    try:
        with path.open("rb") as stream:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except FileNotFoundError:
        return "stopped"
    except BlockingIOError:
        return "running"
    except OSError:
        return "unknown"
    return "stopped"


@contextmanager
def room_presence(state, port):
    stream = None
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(state / f"room-{port}.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        stream = os.fdopen(fd, "wb")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if stream is not None:
            stream.close()
        stream = None
    try:
        record(state, "Room", f"Room socket opened on port {port}")
        yield
    finally:
        if stream is not None:
            stream.close()
        record(state, "Room", f"Room shutting down on port {port}")


def probe(port):
    try:
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=.6)) as connection:
            connection.request("GET", "/health")
            response = connection.getresponse()
            if response.status != 200:
                return f"unhealthy (HTTP {response.status})"
            data = json.loads(response.read(2048))
            if isinstance(data, dict) and data.get("service") == "talos":
                result = f"running · model {clean(data.get('model_state', 'status unavailable'))}"
                if "turn_active" in data:
                    result += " · handling a turn" if data["turn_active"] else " · awaiting a turn"
                return result
            return "endpoint responded · service identity unverified"
    except ConnectionRefusedError:
        return "stopped (connection refused)"
    except PermissionError:
        return "unknown (local connection blocked)"
    except (OSError, ValueError, http.client.HTTPException):
        return "unreachable"


@lru_cache(maxsize=4)
def launchd_status(label, interval):
    """Read only the named project job. Refresh at most once per 30-second interval."""
    try:
        reply = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                               capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return "unknown"
    if reply.returncode:
        return "not loaded" if "Could not find service" in reply.stderr else "unknown"
    for line in reply.stdout.splitlines():
        if line.startswith("\tstate = "):
            return clean(line.split("=", 1)[1])
    return "loaded"


_WORKER_LOCKS = (("Maintenance", "maintenance"), ("Heartbeat watcher", "heartbeat-watch"),
                 ("Obsidian publisher", "vault-watch"), ("Respan reviewer", "respan-watch"),
                 ("OpenCode worker", "section-OpenCode-watch"), ("OpenClaude worker", "section-OpenClaude-watch"),
                 ("Self-repair worker", "self-repair-watch"), ("Source observer", "source-observer-watch"))


def runtime_locks(state, port=None):
    if port is None:
        ports = sorted(int(p.stem[5:]) for p in state.glob("room-*.lock") if p.stem[5:].isdigit()
                       and 1 <= int(p.stem[5:]) <= 65535 and lock_state(p) == "running")
        port = ports[0] if ports else PORT
    return (port, lock_state(state / f"room-{port}.lock"),
            tuple(lock_state(state / f"{key}.lock") for _, key in _WORKER_LOCKS))


def service_snapshot(state, locks):
    port, room_lock, workers = locks
    room_status = probe(port)
    if not room_status.startswith("running") and room_lock == "running":
        room_status = "process running · endpoint " + room_status
    lines = [f"Room on port {port} · {room_status} · process lock {room_lock}"]
    lines.extend(f"{label} · {status}" for (label, _), status in zip(_WORKER_LOCKS, workers))
    if room_status.startswith("stopped"):
        lines.append("Room voice and message requests are unavailable. Open Start-Talos-Service.command for the always-on room and Respan reviewer.")
    if (state / "respan-review.sqlite3").is_file():
        uri = (state / "respan-review.sqlite3").resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=.15)) as reviews:
                for name, due, outcome in reviews.execute("SELECT name,due,outcome FROM jobs ORDER BY rowid"):
                    next_run = datetime.fromtimestamp(due).strftime("%H:%M:%S") if due else "now"
                    lines.append(f"Respan {clean(name)} · {clean(outcome)} · next eligible {next_run}")
        except sqlite3.Error:
            pass
    section_db = state / "section-reviews.sqlite3"
    if section_db.is_file():
        try:
            with closing(sqlite3.connect(section_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=.15)) as reviews:
                for engine, due, outcome in reviews.execute("SELECT engine,due,outcome FROM workers ORDER BY engine"):
                    live = lock_state(state / f"section-{engine}-watch.lock")
                    lines.append(f"{engine} sections · {live} · {clean(outcome)} · next eligible {datetime.fromtimestamp(due):%H:%M:%S}")
        except sqlite3.Error:
            pass
    return lines


def checked_time(seconds):
    return datetime.fromtimestamp(float(seconds)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def heartbeat_receipts(state):
    try:
        with (state / "heartbeat.json").open(encoding="utf-8") as stream:
            beat = json.loads(stream.read(65536))
        checked = checked_time(beat["checked_at"])
        lines = [f"Last heartbeat · {checked} · {clean(beat.get('outcome', 'checked'))}"]
        if beat.get("source_version") and beat.get("baseline"):
            at = beat.get("baseline_verified_at")
            when = checked_time(at) if at else "original run time unavailable"
            lines.append(f"Local dialogue regression · {clean(beat['baseline'])} · source "
                         f"{clean(beat['source_version'])[:12]} · {when}")
        if beat.get("trial") and beat.get("last_result"):
            lines.append(f"Heartbeat coding trial #{clean(beat['trial'])} · {clean(beat.get('stage', ''))} · "
                         f"{clean(beat.get('reviewer', ''))} · {clean(beat['last_result'])}")
        return lines
    except (OSError, ValueError, OverflowError, KeyError, TypeError):
        return []


def runtime_evidence(state):
    """Select the latest operational receipt per component even during noisy provider retries."""
    from talos_core.mac.mac_context import INDEX_NAME
    receipts = heartbeat_receipts(state)
    uri = (state / INDEX_NAME).resolve().as_uri() + "?mode=ro"
    index_receipt = False
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=.15)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_graph_review'").fetchone():
                latest = db.execute("SELECT checked_at,receipt FROM local_graph_review WHERE singleton=1").fetchone()
                if latest:
                    receipts.insert(0, f"Local knowledge review · {checked_time(latest[0])} · {clean(latest[1])}")
            row = db.execute("SELECT checked_at,receipt FROM index_merge_state WHERE singleton=1").fetchone()
            if row and row[1]:
                receipts.append(f"Local index maintenance · {checked_time(row[0])} · {clean(row[1])}")
                index_receipt = True
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        pass
    uri = (state / JOURNAL).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=.15)) as db:
            for component, label in (("Index maintenance", "Local index maintenance"),
                                     ("Migration", "Developer-verified migration"),
                                     ("Supervisor", "Worker supervision"), ("Repair", "Latest repair activity"),
                                     ("Health check", "Scheduled feature check")):
                if component == "Index maintenance" and index_receipt:
                    continue
                row = db.execute("SELECT stamp,message FROM events WHERE component=? ORDER BY id DESC LIMIT 1",
                                 (component,)).fetchone()
                if row:
                    receipts.append(f"{label} · {checked_time(row[0])} · {clean(row[1])}")
            # Completed inference and its accepted/quiet source verdict are distinct events.
            for pattern, label in (("local % completed inference in %", "Local model inference"),
                                   ("Verified % at byte %", "Local source review"),
                                   ("Verified implementation of local proposal %", "Model-proposed change verified")):
                row = db.execute("""SELECT stamp,message FROM events WHERE component='Model'
                                  AND message LIKE ? ORDER BY id DESC LIMIT 1""", (pattern,)).fetchone()
                if row:
                    receipts.insert(0, f"{label} · {checked_time(row[0])} · {clean(row[1])}")
            response = db.execute("""SELECT stamp,message FROM events WHERE component='Respan'
                                   AND message LIKE 'Response from %' ORDER BY id DESC LIMIT 1""").fetchone()
            if response:
                receipts.append(f"Last Respan response · {checked_time(response[0])} · {clean(response[1])}")
            else:
                receipts.append("Successful Respan responses · none in the retained activity log")
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        pass
    return receipts


def repair_evidence(state):
    """Read the incident and source-repair ledgers without starting a repair."""
    from talos_core.self_improvement.self_diagnosis import summary as incidents
    from talos_core.self_improvement.self_repair import summary as repairs
    from talos_core.self_improvement.source_observer import summary as observer
    lines = []
    for read in (repairs, incidents, observer):
        try:
            lines.extend(clean(line) for line in read(state))
        except (OSError, ValueError, sqlite3.Error):
            lines.append("Repair ledger temporarily unavailable")
    return lines


def snapshot(state, port, *, database=None):
    lines = service_snapshot(state, runtime_locks(state, port))
    if sys.platform == "darwin" and state.resolve() == STATE:
        from tools.enable_heartbeat import LABEL as heartbeat_label
        from tools.talos_service import LABEL as room_label
        interval = int(time.monotonic() // 30)
        lines.extend((f"Room login service · {launchd_status(room_label, interval)}",
                      f"Scheduled heartbeat · {launchd_status(heartbeat_label, interval)}"))
    if lock_state(state / "heartbeat.lock") == "running":
        lines.append("Running now · Execute heartbeat review")
    for label, key in PHASES.items():
        if lock_state(state / f"activity-{key}.lock", exclusive=True) == "running":
            lines.append(f"Running now · {label}")
    try:
        with (state / "Maintenance.txt").open(encoding="utf-8") as stream:
            report = stream.read(8192).splitlines()
        for line in report:
            if line.startswith(("Memory check ", "Latest source review", "Latest AI memory review")) or (
                    " indexed records" in line or " requests pending" in line):
                lines.append(f"Last report · {clean(line)}")
    except (OSError, UnicodeError):
        pass
    lines.extend(runtime_evidence(state))
    lines.extend(repair_evidence(state))
    try:
        with (state / "feature-requests.jsonl").open("rb") as queue:
            size = queue.seek(0, os.SEEK_END)
            queue.seek(max(0, size - 16384))
            for raw in reversed(queue.read(16384).splitlines(keepends=True)):
                if not raw.endswith(b"\n"):
                    continue
                try:
                    entry = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(entry, dict) and entry.get("status") in ("proposed", "done", "declined"):
                    detail = entry.get("feature") or entry.get("verification") or entry.get("reason")
                    lines.append(f"Last queue event · {entry['status']} · {clean(detail)}")
                    break
    except OSError:
        pass
    try:
        uri = (database or state / "agent.sqlite3").resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=.15)) as db:
            receipt = db.execute("""SELECT d.id,json_extract(d.proposal,'$.disposition'),
                                    json_extract(d.proposal,'$.tool'),o.status
                                    FROM decisions d LEFT JOIN action_outcomes o ON o.decision_id=d.id
                                    ORDER BY d.id DESC LIMIT 1""").fetchone()
            if receipt:
                ident, disposition, tool, outcome = receipt
                lines.append(f"Last decision #{ident} · {clean(disposition)} · {clean(tool)} · {clean(outcome or 'shadow')}")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='coding_trials'").fetchone():
                trial = db.execute("SELECT id,status FROM coding_trials ORDER BY id DESC LIMIT 1").fetchone()
                if trial:
                    lines.append(f"Last coding trial #{trial[0]} · {clean(trial[1])}")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='background_work'").fetchone():
                counts = db.execute("SELECT status,count(*) FROM background_work GROUP BY status")
                summary = ", ".join(f"{n} {clean(status)}" for status, n in counts)
                if summary:
                    lines.append("Work orders · " + summary)
                jobs = db.execute("SELECT id,status FROM background_work ORDER BY id DESC LIMIT 3")
                lines.extend(f"Work order #{ident} · {clean(status)}" for ident, status in jobs)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='console_goals'").fetchone():
                goals = db.execute("SELECT status,count(*) FROM console_goals GROUP BY status")
                summary = ", ".join(f"{n} {clean(status)}" for status, n in goals)
                if summary:
                    lines.append("Standing goals · " + summary)
    except sqlite3.Error:
        pass
    return lines


def display(state, port=None, *, once=False, output=None, database=None, stop=None):
    output = output or sys.stdout
    previous = None
    cursor = None
    last_update = 0.
    next_probe = 0.
    previous_locks = None
    cadence = PollBackoff()
    heading = "Talos activity"
    if not once:
        heading += " · live" if stop is not None else " · live · Ctrl-C closes this viewer"
    print(heading, file=output, flush=True)
    while True:
        now = time.monotonic()
        rows = events(state, cursor)
        locks = runtime_locks(state, port)
        if previous is None or rows or locks != previous_locks or now >= next_probe:
            previous_locks = locks
            status = snapshot(state, port, database=database)
            next_probe = now + cadence.record("status", bool(rows) or previous != status, now)
        else:
            status = previous
        if previous != status or now - last_update >= 30:
            stamp = datetime.now().astimezone().strftime("%H:%M:%S %Z")
            if previous != status:
                print(f"\n[{stamp}] " + "\n".join(status), file=output, flush=True)
            else:
                print(f"[{stamp}] Viewer active · service and worker status unchanged", file=output, flush=True)
            last_update = now
            previous = status
        if cursor is None:
            print("Recent activity" if rows else "No runtime events recorded yet.", file=output, flush=True)
            cursor = 0
        for ident, stamp, component, message in rows:
            if cursor and ident > cursor + 1:
                print(f"({ident - cursor - 1} older events expired)", file=output)
            print(f"{datetime.fromtimestamp(stamp):%H:%M:%S}  {clean(component):12} {clean(message)}",
                  file=output, flush=True)
            cursor = ident
        if once:
            return
        if stop is None:
            time.sleep(2)
        elif stop.wait(2):
            return


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--port", type=int, help="Override room port discovery.")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535.")
    try:
        display(args.state, args.port, once=args.once)
    except KeyboardInterrupt:
        pass

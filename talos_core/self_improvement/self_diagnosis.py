"""Local operational incidents and bounded replays of trusted Talos fixtures."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from talos_core.cognition.conversation_activity import conversation_busy
from talos_core.runtime.talos_activity import JOURNAL, record

DB = "self-diagnosis.sqlite3"
HEALTH_INTERVAL = 10 * 60
CASES = {
    "playback": "test_voice_recovery.VoiceRecoveryTests.test_complaint_and_receipts_survive_restart",
    "voice": "test_spoken_topic_recall.SpokenRecallTests.test_addressed_voice_recall_uses_its_own_window_and_expires",
    "memory": "test_index_maintenance.IndexMaintenanceTests.test_restart_resume_probe_rollback_and_private_receipt",
    "vault": "test_vault_watch.VaultWatchTests.test_code_edges_update_and_unrelated_notes_survive",
    "room": "test_room_work.RoomWorkTests.test_work_survives_restart_and_duplicate_submissions",
    "review": "test_section_patches.SectionPatchTests.test_produces_applicable_diff_regression_and_receipt_only_once",
    "scheduling": "test_maintenance_schedule.MaintenanceScheduleTests.test_unchanged_sources_reconcile_and_changed_sources_trigger_early_work",
    "runtime": "test_runtime_recovery.RecoveryTests.test_reconnect_full_jitter_cap_and_stability",
    "console": "test_vault_watch.ConsoleParityTests.test_model_selection_clear_redraw_version_and_copy",
}
_AREAS = (
    ("voice", r"voice|audio|speech|spoken|microphone|dictat|playback"),
    ("memory", r"memory|index|retriev|recall|knowledge|search"),
    ("vault", r"vault|obsidian|publisher|graph"),
    ("review", r"respan|openclaude|opencode|review|upstream"),
    ("scheduling", r"heartbeat|schedul|remind|calendar|maintenance"),
    ("room", r"room|work order|jobs"),
    ("console", r"console|terminal"),
    ("runtime", r"supervisor|\bmodel\b|worker|runtime|process|service"),
)
_ENVIRONMENT = (
    (
        "network unavailable",
        r"gaierror|enotfound|name resolution|dns|connection refused|network is unreachable",
    ),
    (
        "permission denied",
        r"permission denied|permissionerror|operation not permitted|eacces|eperm|local connection blocked",
    ),
    ("provider rate limit", r"\b429\b|rate.limit"),
    ("provider credentials", r"\b(?:401|403)\b|unauthori[sz]ed|no local api key"),
    ("provider timeout", r"timed? ?out|timeout(?:error|expired)"),
    ("resource busy", r"database is locked|resource busy"),
)
_FAILURE = re.compile(
    r"\b(?:fail(?:ed|ure|ures)|error|exception|disconnected|crashed|unavailable|aborted)\b|\b[A-Za-z]+Error\b|\bHTTP 5\d\d\b",
    re.IGNORECASE,
)
_REPORT = re.compile(
    r"(?:report (?:a |an |this )?(?:problem|issue|bug)|something is broken)\s*[:,-]?\s+(.+)", re.IGNORECASE
)
_STATUS = re.compile(
    r"(?:what needs repair(?:ing)?|(?:show|check) (?:the )?repair status|what problems (?:are open|have you found))",
    re.IGNORECASE,
)
RECHECK = 30 * 60
MAX_SIGNATURES = 384
RECOVERED = ("transport recovered", "worker recovered")


def area_for(component, detail):
    if re.search(r"speech output|playback|speaker|no audio|no sound|can.t hear|cannot hear", detail, re.IGNORECASE):
        return "playback"
    for value in (component, detail):
        for area, pattern in _AREAS:
            if re.search(pattern, value, re.IGNORECASE):
                return area
    return "general"


def signal(component, detail, explicit=False):
    if not explicit and (
        component in ("Diagnosis", "Source", "Migration", "Health check")
        or detail.startswith(
            ("Read ", "Verified ", "Accepted ", "Compared ", "Request to ", "Drafting implementation")
        )
    ):
        return None
    for label, pattern in _ENVIRONMENT:
        if re.search(pattern, detail, re.IGNORECASE):
            return ("environment", label)
    if not explicit and not _FAILURE.search(detail):
        return None
    if not explicit and re.search(r"\b(?:0|no|without) (?:failures?|errors?)\b", detail, re.IGNORECASE):
        return None
    if explicit:
        return ("reported", "reported problem")
    error = re.search(r"\b[A-Z][A-Za-z]{1,45}(?:Error|Exception)\b|HTTP 5\d\d", detail)
    return ("runtime", error.group() if error else "runtime failure")


def connect(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / DB
    os.close(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600))
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents(
            id INTEGER PRIMARY KEY, signature TEXT UNIQUE NOT NULL, component TEXT NOT NULL, area TEXT NOT NULL,
            kind TEXT NOT NULL, symptom TEXT NOT NULL, private_report TEXT,
            hits INTEGER NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
            status TEXT NOT NULL, checked REAL, consecutive_failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS journal_cursor(
            singleton INTEGER PRIMARY KEY, generation TEXT NOT NULL, seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS health_checks(
            area TEXT PRIMARY KEY, checked REAL NOT NULL);
    """)
    return db


def observe(db, component, detail, stamp, *, explicit=False, area=None):
    if component == "Supervisor":
        recovery = re.fullmatch(
            r"([\w.-]{1,60}) recovered · process stayed up and worker lock is held", detail
        )
        if recovery:
            db.execute(
                """UPDATE incidents SET status='worker recovered',checked=?
                       WHERE signature=? AND last_seen<=? AND status!='worker recovered'""",
                (stamp, "supervisor:worker:" + recovery[1], stamp),
            )
            return None
    if component == "Respan" and detail.startswith("Response from "):
        db.execute(
            """UPDATE incidents SET status='transport recovered',checked=?
                   WHERE kind='environment' AND signature LIKE 'respan:%' AND
                   symptom IN ('network unavailable','provider timeout','provider rate limit','provider credentials')
                   AND status!='transport recovered' AND last_seen<=?""",
            (stamp, stamp),
        )
        return None
    exit_event = (
        re.match(r"^([\w.-]{1,60}) exited \((-?\d+)\)", detail) if component == "Supervisor" else None
    )
    result = (
        ("runtime", f"worker exited ({exit_event[2]})") if exit_event else signal(component, detail, explicit)
    )
    if not result:
        return None
    kind, symptom = result
    component = component if re.fullmatch(r"[\w ./-]{1,60}", component) else "Other"
    if explicit and component in ("Console report", "Room report"):
        component = "User"
    area = area or area_for(component, detail)
    if exit_event:
        signature = "supervisor:worker:" + exit_event[1]
        component = "Supervisor/" + exit_event[1]
        area = area_for(exit_event[1], detail)
    else:
        shape = (
            symptom
            if kind == "environment"
            else re.sub(r"\b[0-9a-f]{12,}\b|\d+(?:[.:]\d+)*", "#", detail.casefold())
        )
        digest = hashlib.sha256(" ".join(shape.split()).encode()).hexdigest()[:24]
        signature = component.casefold() + ":" + kind + ":" + digest
    if not db.execute("SELECT 1 FROM incidents WHERE signature=?", (signature,)).fetchone():
        count = db.execute("SELECT count(*) FROM incidents").fetchone()[0]
        if count >= MAX_SIGNATURES:
            deleted = db.execute("""DELETE FROM incidents WHERE id IN (SELECT id FROM incidents WHERE status IN
                            ('transport recovered','worker recovered') AND id<(SELECT max(id) FROM incidents) ORDER BY checked LIMIT 16)""").rowcount
            if not deleted and not exit_event:
                signature = f"overflow:{area}:{kind}"
                component, symptom, explicit = "Additional reports", "grouped at retention limit", False
    status = (
        "blocked by environment"
        if kind == "environment"
        else "queued"
        if area in CASES
        else "needs a reproduction"
    )
    db.execute(
        """INSERT INTO incidents(signature,component,area,kind,symptom,private_report,hits,first_seen,last_seen,status)
                VALUES (?,?,?,?,?,?,1,?,?,?) ON CONFLICT(signature) DO UPDATE SET
                hits=hits+1,last_seen=max(last_seen,excluded.last_seen),
                symptom=CASE WHEN excluded.last_seen>=last_seen THEN excluded.symptom ELSE symptom END,
                status=CASE WHEN status IN ('transport recovered','worker recovered') OR (checked IS NOT NULL AND checked<? AND status!='repeatable fixture failure')
                            THEN excluded.status ELSE status END""",
        (
            signature,
            component,
            area,
            kind,
            symptom,
            detail[:400] if explicit else None,
            stamp,
            stamp,
            status,
            stamp - RECHECK,
        ),
    )
    return db.execute("SELECT id FROM incidents WHERE signature=?", (signature,)).fetchone()[0]


def report(state, component, description):
    text = " ".join(description.split())
    if not 1 <= len(text) <= 800:
        raise ValueError("Describe the problem in 1 to 800 characters.")
    with closing(connect(state)) as db, db:
        ident = observe(db, component, text, time.time(), explicit=True)
    record(state, "Diagnosis", f"Incident #{ident} reported")
    return ident


def ingest(db, state):
    path = state / JOURNAL
    if not path.is_file():
        return 0
    generation = str(path.stat().st_ino)
    prior = db.execute("SELECT generation,seen FROM journal_cursor WHERE singleton=1").fetchone()
    after = prior["seen"] if prior and prior["generation"] == generation else 0
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as src:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return 0
        (last,) = src.execute("SELECT coalesce(max(id),0) FROM events").fetchone()
        rows = src.execute(
            "SELECT id,stamp,component,message FROM events WHERE id>? ORDER BY id LIMIT 64",
            (after if last >= after else 0,),
        ).fetchall()
    for ident, stamp, component, detail in rows:
        observe(db, component, detail, stamp)
    if rows:
        db.execute("INSERT OR REPLACE INTO journal_cursor VALUES(1,?,?)", (generation, rows[-1][0]))
    return len(rows)


_RUN_CASE = """
import sys, unittest
root, case = sys.argv[1:]
sys.path[:0] = [root + '/tests', root]
suite = unittest.defaultTestLoader.loadTestsFromName(case)
result = unittest.TestResult()
suite.run(result)
if result.errors or result.skipped or result.testsRun != 1:
    sys.exit(2)
sys.exit(1 if result.failures else 0)
"""


def probe(root, area):
    case = CASES[area]
    with tempfile.TemporaryDirectory(prefix="talos-incident-") as temp:
        try:
            run = subprocess.run(
                [sys.executable, "-I", "-B", "-c", _RUN_CASE, str(root.resolve()), case],
                cwd=temp,
                env={"PATH": os.defpath, "TMPDIR": temp, "LANG": "C"},
                timeout=15,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "probe unavailable"
    return {0: "fixture passed; symptom unverified", 1: "fixture assertion failed"}.get(
        run.returncode, "probe unavailable"
    )


def scheduled_probe(root, state, db, now, run_probe):
    checked = dict(db.execute("SELECT area,checked FROM health_checks"))
    if checked and now - max(checked.values()) < HEALTH_INTERVAL:
        return None
    area = min(CASES, key=lambda name: checked.get(name, float("-inf")))
    outcome = run_probe(root, area)
    with db:
        db.execute("INSERT OR REPLACE INTO health_checks VALUES(?,?)", (area, now))
        if outcome == "fixture assertion failed":
            observe(db, "Scheduled fixture", f"{area} fixture assertion failed", now, area=area)
    if outcome.startswith("fixture passed"):
        outcome = "fixture passed"
    detail = f"{area} · {outcome}"
    record(state, "Health check", detail)
    return detail


def tick(root, state, *, run_probe=probe, proactive=False, clock=time.time):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "self-diagnosis.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "Incident check already running"
        with closing(connect(state)) as db:
            with db:
                scanned = ingest(db, state)
            row = db.execute(
                """SELECT id,area,status,consecutive_failures FROM incidents WHERE status IN
                              ('queued','fixture assertion failed') OR (status IN ('repeatable fixture failure','probe unavailable')
                              AND checked<=?) ORDER BY coalesce(checked,0),first_seen,id LIMIT 1""",
                (clock() - RECHECK,),
            ).fetchone()
            if proactive and conversation_busy(state):
                return None
            if row is None:
                if proactive:
                    return scheduled_probe(root, state, db, clock(), run_probe)
                return f"Scanned {scanned} operational events"
            outcome = run_probe(root, row["area"])
            failures = row["consecutive_failures"] + 1 if outcome == "fixture assertion failed" else 0
            if failures >= 2:
                outcome = "repeatable fixture failure"
            elif row["status"] == "repeatable fixture failure" and outcome.startswith("fixture passed"):
                outcome = "fixture passing on recheck; live symptom unverified"
            now = clock()
            with db:
                db.execute(
                    "UPDATE incidents SET checked=?,status=?,consecutive_failures=? WHERE id=?",
                    (now, outcome, failures, row["id"]),
                )
        detail = f"Incident #{row['id']} · {row['area']} · {outcome}"
        record(state, "Diagnosis", detail)
        return detail


def handoff(state, *, limit=12):
    """Read-only, bounded incident handoff. User prose stays out of worker prompts and displays."""
    path = state / DB
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id,component,area,kind,symptom,hits,last_seen,status,checked FROM incidents
                    WHERE status NOT IN ('transport recovered','worker recovered')
                    ORDER BY (status='repeatable fixture failure') DESC,last_seen DESC,id DESC LIMIT ?""",
            (min(100, max(0, limit)),),
        ).fetchall()
    return [{**dict(row), "fixture": CASES.get(row["area"])} for row in rows]


def summary(state):
    path = state / DB
    if not path.is_file():
        return ["Incident checks · no unresolved reports recorded"]
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
            total, blocked = db.execute("""SELECT count(*),coalesce(sum(kind='environment'),0) FROM incidents
                                    WHERE status NOT IN ('transport recovered','worker recovered')""").fetchone()
            recovered = db.execute("""SELECT component,status,checked FROM incidents WHERE status IN
                                      ('transport recovered','worker recovered') ORDER BY checked DESC LIMIT 1""").fetchone()
    except sqlite3.OperationalError:
        return ["Incident checks · ledger temporarily unavailable"]
    lines = [f"Incident checks · {total} unresolved · {blocked} environmental blockers"]
    lines.extend(
        f"Incident #{r['id']} · {r['component']} · {r['area']} · {r['symptom']} · {r['status']} · "
        f"{r['hits']} observations · last seen {datetime.fromtimestamp(r['last_seen']).astimezone():%Y-%m-%d %H:%M:%S %Z}"
        for r in handoff(state, limit=5)
    )
    if recovered:
        lines.append(
            f"Observed recovery · {recovered[0]} · {recovered[1]} · "
            f"{datetime.fromtimestamp(recovered[2]).astimezone():%Y-%m-%d %H:%M:%S %Z}"
        )
    return lines


def details(state, ident):
    if not (state / DB).is_file():
        return "No such incident."
    with closing(sqlite3.connect((state / DB).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
        row = db.execute(
            "SELECT component,area,status,private_report FROM incidents WHERE id=?", (ident,)
        ).fetchone()
    if not row:
        return "No such incident."
    from talos_core.runtime.talos_activity import clean

    lines = [f"Incident #{ident} · {row[0]} · {row[1]} · {row[2]}"]
    if row[3]:
        lines.append("Your report · " + clean(row[3]))
    if row[1] in CASES:
        lines.append("Related offline fixture · " + CASES[row[1]])
    return "\n".join(lines)


def room_request(state, text, *, apply):
    words = text.strip().rstrip(".!?").strip()
    if _STATUS.fullmatch(words):
        return {"reply": ". ".join(summary(state)[:3])}
    match = _REPORT.fullmatch(words)
    if match:
        if not apply:
            return {"reply": "Problem report is in preview mode."}
        ident = report(state, "Room report", match[1])
        return {"reply": f"Recorded problem {ident} for local diagnosis."}
    return None


AREA_TARGETS = {
    "playback": "mac/VoiceEndpoint.swift",
    "voice": "mac/VoiceEndpoint.swift",
    "memory": "talos_core/memory/personal_index.py",
    "vault": "talos_core/mac/mac_context.py",
    "room": "talos_core/room/room_dialogue.py",
    "review": "talos_core/self_improvement/idle_lab.py",
    "scheduling": "talos_core/room/room_work.py",
    "runtime": "talos_core/self_improvement/idle_lab.py",
    "console": "talos_core/room/room_dialogue.py",
    "commitment": "talos_core/memory/commitment_memory.py",
    "missed_lookup": "talos_core/room/room_dialogue.py",
    "missing_knowledge_lookup": "talos_core/room/room_dialogue.py",
    "unverified_claim": "talos_core/room/room_dialogue.py",
    "unverified_action_claim": "talos_core/room/room_dialogue.py",
    "user_correction": "talos_core/room/room_dialogue.py",
    "degraded_reply": "talos_core/room/room_dialogue.py",
    "high_latency": "talos_core/room/room_dialogue.py",
    "runtime_failure": "talos_core/self_improvement/idle_lab.py",
    "missed_obligation": "talos_core/memory/commitment_memory.py",
    "verbosity": "talos_core/room/room_dialogue.py",
    "echo": "talos_core/room/room_dialogue.py",
}


def _append_proposal_if_new(state: Path, *, seed: str, origin: str, target: str, feature: str, acceptance: str):
    from talos_core.self_improvement.feature_targets import FILE, TARGETS, proposal_ledger

    if target not in TARGETS:
        target = "talos_core/room/room_dialogue.py"
    ident = hashlib.sha256(f"{target}\0{seed.casefold()}".encode()).hexdigest()[:12]
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "feature-review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (state / FILE).open("a+", encoding="utf-8") as stream:
            os.chmod(stream.fileno(), 0o600)
            proposed, outcomes = proposal_ledger(stream, repair=True)
            if ident in proposed or ident in outcomes:
                return None
            if any(item.get("feature", "").casefold() == feature.casefold() for item in proposed.values()):
                return None
            entry = {
                "status": "proposed",
                "id": ident,
                "origin": origin,
                "source": target,
                "feature": feature[:160],
                "acceptance": acceptance[:240],
                "time": time.time(),
            }
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
    record(state, "Diagnosis", f"Promoted {origin} proposal {ident} for {target}")
    return entry


def promote_incidents_to_proposals(
    state: Path | sqlite3.Connection,
    db: sqlite3.Connection | Path | None = None,
    *,
    threshold: int = 2,
) -> list[dict]:
    """Promote recurring operational incidents and episodic turn critiques into deduplicated feature proposals."""
    if isinstance(state, sqlite3.Connection):
        state, db = Path(db), state
    state = Path(state)
    promoted = []

    diag_path = state / DB
    if diag_path.is_file():
        with closing(connect(state)) as diag:
            rows = diag.execute(
                """
                SELECT id, area, kind, symptom, hits, status
                FROM incidents
                WHERE status != 'worker recovered'
                  AND (hits >= ? OR status = 'repeatable fixture failure')
                ORDER BY hits DESC, last_seen DESC
                LIMIT 8
                """,
                (max(1, int(threshold)),),
            ).fetchall()
        for row in rows:
            area = row["area"] or "runtime"
            target = AREA_TARGETS.get(area, "talos_core/self_improvement/idle_lab.py")
            replay = CASES.get(area, ("unittest regression suite", ""))[0] if isinstance(CASES.get(area), tuple) else "unittest regression suite"
            feature = f"Harden {area} handling against recurring {row['symptom'] or row['kind']} ({row['hits']} incidents)"
            acceptance = f"Prevent recurring {area}/{row['kind']} incident {row['id']} and pass {replay}."
            created = _append_proposal_if_new(
                state,
                seed=f"incident:{row['id']}:{area}:{row['kind']}:{row['symptom']}",
                origin="self-diagnosis",
                target=target,
                feature=feature,
                acceptance=acceptance,
            )
            if created is not None:
                promoted.append(created)

    owns_db = False
    if db is None and (state / "agent.sqlite3").is_file():
        from talos_core.memory.agent_memory import open_memory
        db = open_memory(state / "agent.sqlite3")
        owns_db = True
    try:
        if db is not None:
            has_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_critiques'"
            ).fetchone()
            if has_table:
                groups = db.execute(
                    """
                    SELECT kind, COUNT(*) AS hits
                    FROM turn_critiques
                    WHERE resolved = 0
                    GROUP BY kind
                    HAVING COUNT(*) >= ?
                    ORDER BY hits DESC, kind ASC
                    """,
                    (max(1, int(threshold)),),
                ).fetchall()
                for group in groups:
                    kind = group["kind"]
                    hits = int(group["hits"])
                    target = AREA_TARGETS.get(kind, "talos_core/room/room_dialogue.py")
                    feature = f"Reduce {kind.replace('_', ' ')} regressions observed across {hits} turns"
                    acceptance = (
                        f"Add deterministic guard or retrieval coverage for {kind} in {target} "
                        "and verify with a unit test."
                    )
                    created = _append_proposal_if_new(
                        state,
                        seed=f"critique:{kind}",
                        origin="self-critique",
                        target=target,
                        feature=feature,
                        acceptance=acceptance,
                    )
                    with db:
                        db.execute(
                            "UPDATE turn_critiques SET resolved = 1 WHERE resolved = 0 AND kind = ?",
                            (kind,),
                        )
                    if created is not None:
                        promoted.append(created)
    finally:
        if owns_db and db is not None:
            db.close()

    return promoted


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", type=Path, default=Path(__file__).resolve().parents[2] / "state")
    cli.add_argument("--handoff", action="store_true")
    args = cli.parse_args()
    print(json.dumps(handoff(args.state)) if args.handoff else "\n".join(summary(args.state)))

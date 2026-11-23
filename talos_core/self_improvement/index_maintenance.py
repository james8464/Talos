"""Incremental, verified optimization of the local FTS5 index."""

import sqlite3
import time
from contextlib import closing

from talos_core.cognition.conversation_activity import conversation_busy
from talos_core.mac.mac_context import INDEX_NAME
from talos_core.runtime.talos_activity import record

MIN_SEGMENTS = 6
MERGE_PAGES = 128
PROBES = ("the", "meeting", "project", "tomorrow")
PROBE_ROWS = 16
IDLE_CHECK = 1800


def probe_snapshot(db):
    count = db.execute("SELECT count(*) FROM personal_docsize").fetchone()[0]
    rows = tuple(
        tuple(
            row[0]
            for row in db.execute(
                "SELECT rowid FROM personal WHERE personal MATCH ? ORDER BY rowid LIMIT ?",
                (probe, PROBE_ROWS),
            )
        )
        for probe in PROBES
    )
    return count, rows


def segment_count(db):
    return db.execute("SELECT count(DISTINCT segid) FROM personal_idx").fetchone()[0]


def outcome(status, message, retry_in):
    return {"status": status, "message": message, "retry_in": retry_in}


def merge_step(state):
    """Commit at most one FTS5 merge command and its receipt in one transaction."""
    busy = outcome("deferred", "Deferred for foreground conversation", 60)
    if conversation_busy(state):
        return busy
    path = state / INDEX_NAME
    if not path.is_file():
        return outcome("waiting", "Waiting for the search index", 60)
    try:
        with (
            closing(
                sqlite3.connect(
                    path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.1
                )
            ) as db,
            db,
        ):
            db.execute("BEGIN IMMEDIATE")
            tables = {
                row[0]
                for row in db.execute("""SELECT name FROM sqlite_master
                      WHERE name IN ('personal_idx','personal_docsize','index_merge_state')""")
            }
            if not {"personal_idx", "personal_docsize"} <= tables:
                return outcome("waiting", "Waiting for the FTS5 index", 60)
            prior = None
            if "index_merge_state" in tables:
                prior = db.execute(
                    "SELECT active,passes,initial_segments,receipt FROM index_merge_state WHERE singleton=1"
                ).fetchone()
            before = segment_count(db)
            active = bool(prior and prior[0])
            if not active and before < MIN_SEGMENTS:
                return outcome(
                    "idle",
                    f"{before} search segments, below the merge threshold of {MIN_SEGMENTS}",
                    IDLE_CHECK,
                )

            baseline = probe_snapshot(db)
            # FTS5's incremental optimize starts with -N, then uses +N until
            # total_changes grows by less than two for the command itself.
            # https://sqlite.org/fts5.html#the_merge_command
            changes = db.total_changes
            db.execute(
                "INSERT INTO personal(personal,rank) VALUES('merge',?)",
                (MERGE_PAGES if active else -MERGE_PAGES,),
            )
            writes = db.total_changes - changes
            if probe_snapshot(db) != baseline:
                db.rollback()
                message = "Search probes or document count changed · merge rolled back"
                record(state, "Index maintenance", message)
                return outcome("failed", message, IDLE_CHECK)
            if conversation_busy(state):
                db.rollback()
                return busy
            after = segment_count(db)
            still_active = writes >= 2
            passes = prior[1] + 1 if active else 1
            initial = prior[2] if active else before
            status = "merging" if still_active else "complete"
            docs, rows = baseline
            receipt = (
                f"FTS5 {status} · pass {passes} · segments {before} → {after} (started at {initial}) · "
                f"{docs:,} documents unchanged · {len(rows)} probes verified ({sum(map(len, rows))} sampled IDs) · "
                f"{writes} SQLite row changes"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS index_merge_state(
                       singleton INTEGER PRIMARY KEY CHECK(singleton=1), active INTEGER NOT NULL, passes INTEGER NOT NULL,
                       initial_segments INTEGER NOT NULL, receipt TEXT NOT NULL, checked_at REAL NOT NULL)""")
            db.execute(
                "INSERT OR REPLACE INTO index_merge_state VALUES(1,?,?,?,?,?)",
                (still_active, passes, initial, receipt, time.time()),
            )
        # The durable receipt above is authoritative even if the activity feed is busy.
        record(state, "Index maintenance", receipt)
        return outcome(status, receipt, 60 if still_active else IDLE_CHECK)
    except sqlite3.OperationalError as error:
        if (getattr(error, "sqlite_errorcode", 0) & 0xFF) in (
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        ):
            return outcome("deferred", "Index in use · retry scheduled", 60)
        raise

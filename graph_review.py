"""Ask the reviewer to prioritize real shared evidence, using anonymous counts."""
import json
import hashlib
import sqlite3
from contextlib import closing

import mac_context
from memory_connections import PROJECT, SOURCES
from room_research import FREE_MODEL


def candidates(db, allowed):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_connections'").fetchone():
        return []
    db.execute("""CREATE TEMP TABLE IF NOT EXISTS selected_pairs(
        a TEXT, b TEXT, PRIMARY KEY(a,b)) WITHOUT ROWID""")
    db.execute("DELETE FROM selected_pairs")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='reviewed_project_pairs'").fetchone():
        db.execute("INSERT INTO selected_pairs SELECT a,b FROM reviewed_project_pairs WHERE model=?", (FREE_MODEL,))
    args = tuple(sorted(allowed))
    marks = ",".join("?" for _ in args)
    rows = db.execute(f"""SELECT x.project,y.project,count(*),count(DISTINCT x.app)
        FROM memory_connections x JOIN memory_connections y
        ON (y.app,y.source,y.record)=(x.app,x.source,x.record) AND x.project<y.project
        WHERE x.app IN ({marks}) AND NOT EXISTS (SELECT 1 FROM selected_pairs s
        WHERE (s.a,s.b)=(x.project,y.project)) GROUP BY x.project,y.project
        HAVING count(*)>=2 ORDER BY count(DISTINCT x.app) DESC,count(*) DESC,x.project,y.project LIMIT 12""", args)
    return [row for row in rows if PROJECT.fullmatch(row[0]) and PROJECT.fullmatch(row[1])]


FOCUSES = ("alias", "relationship", "recurring pattern", "changed plan")


def next_focus(db, allowed):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_connections'").fetchone():
        return None
    configured = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='project_review_focus'").fetchone():
        configured = dict(db.execute("SELECT project,signature FROM project_review_focus"))
    marks = ",".join("?" for _ in allowed)
    rows = db.execute(f"""SELECT project,app,source,record FROM memory_connections WHERE app IN ({marks})
                          ORDER BY project,app,source,record""", tuple(sorted(allowed)))
    from itertools import groupby
    for project, group in groupby(rows, key=lambda r: r[0]):
        by_app, fingerprint = {}, hashlib.sha256()
        for _, app, source, record in group:
            by_app[app] = by_app.get(app, 0) + 1
            fingerprint.update(json.dumps([app, source, record]).encode() + b"\n")
        signature = fingerprint.hexdigest()
        if PROJECT.fullmatch(project) and sum(by_app.values()) >= 2 and configured.get(project) != signature:
            return project, signature, by_app
    return None


def choose_focus(state, path, complete, observe):
    with closing(sqlite3.connect(path, timeout=2)) as db:
        candidate = next_focus(db, set(SOURCES) | mac_context.enabled(state))
    if not candidate:
        return "No new graph evidence requiring review"
    project, signature, counts = candidate
    observe("Select a local investigation from anonymized counts by source type")
    output = json.loads(complete("Choose one focus for a local reviewer to investigate. Return JSON with "
                                 "only focus, one of: " + ", ".join(FOCUSES) + ". These are source counts, not facts.",
                                 json.dumps({"counts_by_source_type": counts})))
    if not isinstance(output, dict) or set(output) != {"focus"} or output["focus"] not in FOCUSES:
        raise ValueError("Invalid investigation focus")
    with closing(sqlite3.connect(path, timeout=2)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if next_focus(db, set(SOURCES) | mac_context.enabled(state)) != candidate:
            return "Evidence changed before commit; retry on the next review"
        db.execute("""CREATE TABLE IF NOT EXISTS project_review_focus(
            project TEXT PRIMARY KEY,signature TEXT,focus TEXT,model TEXT) WITHOUT ROWID""")
        db.execute("INSERT OR REPLACE INTO project_review_focus VALUES(?,?,?,?)",
                   (project,signature,output["focus"],FREE_MODEL))
    return "Local evidence review requested · " + output["focus"]


def review(state, complete, observe):
    path = state / mac_context.INDEX_NAME
    if not path.is_file():
        return "Waiting for the knowledge index"
    with closing(sqlite3.connect(path, timeout=2)) as db:
        pairs = candidates(db, set(SOURCES) | mac_context.enabled(state))
    if not pairs:
        return choose_focus(state, path, complete, observe)
    batch = [{"id": i, "shared_records": row[2], "source_types": row[3]} for i, row in enumerate(pairs, 1)]
    observe(f"Inspect {len(batch)} anonymized project pairs for shared evidence")
    response = complete("Prioritize the strongest candidate for a shared-evidence link. Prefer evidence across "
                        "multiple source types. Return JSON with only id, or id 0 to abstain. "
                        "The counts support shared evidence only.", json.dumps(batch))
    result = json.loads(response)
    if not isinstance(result, dict) or set(result) != {"id"} or type(result["id"]) is not int or not 0 <= result["id"] <= len(pairs):
        raise ValueError("Invalid graph review selection")
    if not result["id"]:
        return "Reviewer abstained from linking the candidates"
    a, b, n, kinds = pairs[result["id"] - 1]
    with closing(sqlite3.connect(path, timeout=2)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if (a,b,n,kinds) not in candidates(db, set(SOURCES) | mac_context.enabled(state)):
            return "Evidence changed before commit; retry on the next review"
        db.execute("""CREATE TABLE IF NOT EXISTS reviewed_project_pairs(
            a TEXT,b TEXT,model TEXT,PRIMARY KEY(a,b)) WITHOUT ROWID""")
        db.execute("INSERT OR REPLACE INTO reviewed_project_pairs VALUES(?,?,?)", (a,b,FREE_MODEL))
    return f"Accepted one shared-evidence link supported by {n} records across {kinds} source types"

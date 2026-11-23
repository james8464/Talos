"""Bounded, local project-to-evidence relationships derived from the search index."""

import re
import sqlite3
from contextlib import closing

from talos_core.mac import mac_context

PROJECT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,63}")
SOURCES = ("Files", "Cloud documents", "Notes", "Calendar", "Reminders",
           "Bookmarks", "Mail archive", "OCR")
PROJECT_RECORD = re.compile(r"([^:]+):[0-9a-f]{40,64}")


CATALOG_SIGNATURE = "project-names-v1"


def project_name(record):
    match = PROJECT_RECORD.fullmatch(record) if isinstance(record, str) else None
    return match[1].casefold() if match and PROJECT.fullmatch(match[1]) else None


def ensure_project_catalog(db):
    """Backfill pre-catalog indexes once inside the caller's write transaction."""
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('indexed_projects','source_summary')")}
    if "indexed_projects" not in tables:
        db.execute("""CREATE TABLE indexed_projects(name TEXT, source TEXT,
                      PRIMARY KEY(name,source)) WITHOUT ROWID""")
        rows = db.execute("SELECT coalesce(source,''),record FROM personal WHERE app='Projects'")
        db.executemany("INSERT OR IGNORE INTO indexed_projects VALUES(?,?)",
                       ((name, source) for source, record in rows if (name := project_name(record))))
    elif "source_summary" in tables:
        # An older maintenance worker replaces the Projects summary with an empty
        # signature. Repair just that source if both versions have been running.
        legacy = db.execute("SELECT source FROM source_summary WHERE app='Projects' AND signature=''").fetchall()
        if not legacy:
            return
        for (source,) in legacy:
            rows = db.execute("SELECT record FROM personal WHERE source=? AND app='Projects'", (source,))
            update_project_catalog(db, source, rows)
    if "source_summary" in tables:
        db.execute("UPDATE source_summary SET signature=? WHERE app='Projects' AND signature=''", (CATALOG_SIGNATURE,))


def update_project_catalog(db, source, records):
    names = {name for (record,) in records if (name := project_name(record))}
    existing = {row[0] for row in db.execute("SELECT name FROM indexed_projects WHERE source=?", (source,))}
    db.executemany("DELETE FROM indexed_projects WHERE name=? AND source=?",
                   ((name, source) for name in existing - names))
    db.executemany("INSERT INTO indexed_projects VALUES(?,?)", ((name, source) for name in names - existing))


def refresh(state):
    path = state / mac_context.INDEX_NAME
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        enabled = mac_context.enabled(state)
        apps = (*SOURCES, *sorted(enabled))
        markers = ",".join("?" for _ in apps)
        db.execute("BEGIN IMMEDIATE")
        ensure_project_catalog(db)
        db.execute("""CREATE TABLE IF NOT EXISTS memory_connections (
            project TEXT, app TEXT, source TEXT, record TEXT,
            PRIMARY KEY(project, app, source, record)) WITHOUT ROWID""")
        # The reviewer joins projects by an exact evidence record. The primary key
        # serves project lookups; this covering index serves the reverse direction.
        db.execute("""CREATE INDEX IF NOT EXISTS memory_connections_by_record
                      ON memory_connections(app,source,record,project)""")
        db.execute("""CREATE TEMP TABLE next_links(
            project TEXT, app TEXT, source TEXT, record TEXT,
            PRIMARY KEY(project, app, source, record)) WITHOUT ROWID""")
        for (name,) in db.execute("SELECT DISTINCT name FROM indexed_projects ORDER BY name LIMIT 100"):
            rows = db.execute(
                f"""SELECT app,source,record FROM personal WHERE personal MATCH ?
                    AND app IN ({markers}) ORDER BY rank LIMIT 120""", (f'"{name}"', *apps)
            )
            # Bound evidence per source type so a single busy thread cannot dominate recall.
            counts = {}
            for app, source, record in rows:
                count = counts.get(app, 0)
                if count < 8:
                    db.execute("INSERT OR IGNORE INTO next_links VALUES(?,?,?,?)",
                               (name, app, source, record))
                    counts[app] = count + 1
        # Repeated refreshes should not dirty the persistent index or wake vault subscribers.
        db.execute("""DELETE FROM memory_connections AS old WHERE NOT EXISTS (
            SELECT 1 FROM next_links AS new WHERE (new.project,new.app,new.source,new.record)=
            (old.project,old.app,old.source,old.record))""")
        db.execute("""INSERT INTO memory_connections SELECT * FROM next_links
                      EXCEPT SELECT * FROM memory_connections""")
        return db.execute("SELECT count(*) FROM memory_connections").fetchone()[0]


def answer(state, project):
    path = state / mac_context.INDEX_NAME
    if not path.is_file():
        return "No project connections have been built yet."
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_connections'").fetchone():
            return "No project connections have been built yet."
        rows = db.execute("""SELECT app,record FROM memory_connections
                             WHERE project=? ORDER BY app,record""", (project.casefold(),)).fetchall()
    allowed = set(SOURCES) | mac_context.enabled(state)
    groups = {}
    for app, record in rows:
        if app in allowed:
            groups.setdefault(app, []).append(record)
    if not groups:
        return "No indexed evidence for that project name."
    return "\n".join(f"{app} ({len(records)} linked): " + ", ".join(records[:2])
                     for app, records in groups.items())

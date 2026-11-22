"""Bounded, local project-to-evidence relationships derived from the search index."""

import re
import sqlite3
from contextlib import closing

import mac_context

PROJECT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,63}")
SOURCES = ("Files", "Cloud documents", "Notes", "Calendar", "Reminders",
           "Bookmarks", "Mail archive", "OCR")
PROJECT_RECORD = re.compile(r"([^:]+):[0-9a-f]{40,64}")


def refresh(state):
    path = state / mac_context.INDEX_NAME
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        enabled = mac_context.enabled(state)
        apps = (*SOURCES, *sorted(enabled))
        markers = ",".join("?" for _ in apps)
        names = set()
        for (record,) in db.execute("SELECT record FROM personal WHERE app='Projects'"):
            match = PROJECT_RECORD.fullmatch(record)
            if match and PROJECT.fullmatch(match[1]):
                names.add(match[1].casefold())
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_connections (
            project TEXT, app TEXT, source TEXT, record TEXT,
            PRIMARY KEY(project, app, source, record)) WITHOUT ROWID""")
        db.execute("""CREATE TEMP TABLE next_links(
            project TEXT, app TEXT, source TEXT, record TEXT,
            PRIMARY KEY(project, app, source, record)) WITHOUT ROWID""")
        for name in sorted(names)[:100]:
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

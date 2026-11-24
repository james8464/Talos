"""Ask the reviewer to prioritize real shared evidence, using anonymous counts."""
import json
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from itertools import groupby, islice
from contextlib import closing

import local_source_model
import mac_context
from conversation_activity import conversation_busy
from memory_connections import PROJECT, SOURCES
from room_research import FREE_MODEL


def candidates(db, allowed, *, limit=12):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_connections'").fetchone():
        return []
    db.execute("""CREATE TEMP TABLE IF NOT EXISTS selected_pairs(
        a TEXT, b TEXT, PRIMARY KEY(a,b)) WITHOUT ROWID""")
    db.execute("DELETE FROM selected_pairs")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='reviewed_project_pairs'").fetchone():
        db.execute("INSERT INTO selected_pairs SELECT a,b FROM reviewed_project_pairs")
    args = tuple(sorted(allowed))
    marks = ",".join("?" for _ in args)
    rows = db.execute(f"""SELECT x.project,y.project,count(*),count(DISTINCT x.app)
        FROM memory_connections x JOIN memory_connections y
        ON (y.app,y.source,y.record)=(x.app,x.source,x.record) AND x.project<y.project
        WHERE x.app IN ({marks}) AND NOT EXISTS (SELECT 1 FROM selected_pairs s
        WHERE (s.a,s.b)=(x.project,y.project)) GROUP BY x.project,y.project
        HAVING count(*)>=2 ORDER BY count(DISTINCT x.app) DESC,count(*) DESC,x.project,y.project LIMIT ?""", (*args,limit))
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
    for project, group in groupby(rows, key=lambda r: r[0]):
        by_app, fingerprint = {}, hashlib.sha256()
        for _, app, source, record in group:
            by_app[app] = by_app.get(app, 0) + 1
            fingerprint.update(json.dumps([app, source, record]).encode() + b"\n")
        signature = fingerprint.hexdigest()
        if PROJECT.fullmatch(project) and sum(by_app.values()) >= 2 and configured.get(project) != signature:
            return project, signature, by_app
    return None


def choose_focus(state, path, complete, observe, *, model=FREE_MODEL):
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
        if model == local_source_model.MODEL and conversation_busy(state):
            return "Local knowledge review yielded to foreground conversation"
        if next_focus(db, set(SOURCES) | mac_context.enabled(state)) != candidate:
            return "Evidence changed before commit; retry on the next review"
        db.execute("""CREATE TABLE IF NOT EXISTS project_review_focus(
            project TEXT PRIMARY KEY,signature TEXT,focus TEXT,model TEXT) WITHOUT ROWID""")
        db.execute("INSERT OR REPLACE INTO project_review_focus VALUES(?,?,?,?)",
                   (project,signature,output["focus"],model))
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


# Exported and live variants of the same channel cannot corroborate each other.
FAMILIES = {
    "Files": "documents",
    "Cloud documents": "documents",
    "OCR": "documents",
    "Mail": "email",
    "Mail archive": "email",
    "Messages": "messages",
    "WhatsApp": "messages",
}
ID_GRAMMAR = r'''root ::= "{\"id\":" ("0" | [1-9] | "1" [0-2]) "}"'''
FOCUS_GRAMMAR = r'''root ::= "{\"focus\":\"" ("alias" | "relationship" | "recurring pattern" | "changed plan") "\"}"'''


@dataclass(frozen=True)
class KnowledgeLink:
    project: str
    peer: str
    proofs: tuple

    def signature(self):
        return hashlib.sha256(
            json.dumps((self.project, self.peer, self.proofs)).encode()
        ).hexdigest()

    def description(self):
        return {
            "kind": "shared records" if self.peer else "cross-source evidence group",
            "records": len(self.proofs),
            "families": len({FAMILIES.get(app, app) for app, _, _ in self.proofs}),
        }


def supported(db, choice):
    """Check membership against the current FTS snapshot without reading or exposing text."""
    table = db.execute("SELECT sql FROM sqlite_master WHERE name='personal'").fetchone()
    if not table or "fts5" not in table[0].lower():
        return False
    marks = ",".join("(?,?,?)" for _ in choice.proofs)
    args = [field for proof in choice.proofs for field in proof]
    for project in (choice.project, choice.peer):
        if project:
            rows = db.execute(
                f"""SELECT DISTINCT app,source,record FROM personal WHERE personal MATCH ?
                              AND (app,source,record) IN (VALUES {marks})""",
                (f'"{project}"', *args),
            )
            if set(rows) != set(choice.proofs):
                return False
    return True


def source_groups(db, allowed, *, verified):
    """Return evidence keys locally; only their counts are passed to inference."""
    tables = {
        r[0]
        for r in db.execute("""SELECT name FROM sqlite_master
              WHERE name IN ('memory_connections','reviewed_source_groups')""")
    }
    if "memory_connections" not in tables or (
        verified and "reviewed_source_groups" not in tables
    ):
        return
    saved = (
        dict(db.execute("SELECT project,signature FROM reviewed_source_groups"))
        if "reviewed_source_groups" in tables
        else {}
    )
    marks = ",".join("?" for _ in allowed)
    rows = db.execute(
        f"""SELECT project,app,source,record FROM memory_connections WHERE app IN ({marks})
                       ORDER BY project,app,source,record""",
        tuple(sorted(allowed)),
    )
    for project, group in groupby(rows, lambda row: row[0]):
        if not PROJECT.fullmatch(project):
            continue
        by_family, used = {}, set()
        for _, app, source, record in group:
            family = FAMILIES.get(app, app)
            selected = by_family.setdefault(family, [])
            if len(selected) < 2 and (source, record) not in used:
                selected.append((app, source, record))
                used.add((source, record))
        families = [items for items in by_family.values() if len(items) == 2][:3]
        if len(families) < 2:
            continue
        link = KnowledgeLink(
            project, "", tuple(proof for items in families for proof in items)
        )
        if (saved.get(project) == link.signature()) == verified and supported(db, link):
            yield link


def local_links(db, allowed):
    rejected = {}
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='local_graph_abstentions'"
    ).fetchone():
        rejected = {
            (project, peer): signature
            for project, peer, signature in db.execute(
                "SELECT project,peer,signature FROM local_graph_abstentions"
            )
        }

    def eligible(link):
        return rejected.get((link.project, link.peer)) != link.signature()

    groups = (
        link for link in source_groups(db, allowed, verified=False) if eligible(link)
    )
    links = list(islice(groups, 6))
    marks = ",".join("?" for _ in allowed)
    for a, b, _, _ in candidates(db, allowed, limit=12 + len(rejected)):
        proofs = db.execute(
            f"""SELECT x.app,x.source,x.record FROM memory_connections x
            JOIN memory_connections y USING(app,source,record)
            WHERE x.project=? AND y.project=? AND x.app IN ({marks})
            ORDER BY x.app,x.source,x.record""",
            (a, b, *sorted(allowed)),
        )
        pair = KnowledgeLink(a, b, tuple(proofs))
        if eligible(pair) and supported(db, pair):
            links.append(pair)
            if len(links) == 12:
                break
    return links


def review_local(state, runtime, observe):
    """Select one tightly bounded change on CPU; recheck authorized evidence before writing."""
    path = state / mac_context.INDEX_NAME
    if not path.is_file():
        return "Waiting for the knowledge index"
    if conversation_busy(state):
        return "Local knowledge review yielded to foreground conversation"

    def complete(system, payload, grammar):
        prompt = (
            f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{payload}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )
        started = time.monotonic()
        output, inputs, generated = local_source_model.run_cpu(
            runtime, state, prompt, grammar=grammar
        )
        observe(
            f"{local_source_model.MODEL} · knowledge inference completed in {time.monotonic() - started:.1f}s · "
            f"{inputs} input tokens · {generated} output tokens"
        )
        return output

    with closing(sqlite3.connect(path, timeout=0.2)) as db:
        db.execute("BEGIN")
        choices = local_links(db, set(SOURCES) | mac_context.enabled(state))
        signature = hashlib.sha256(
            json.dumps([choice.signature() for choice in choices]).encode()
        ).hexdigest()
    if not choices:
        return choose_focus(
            state,
            path,
            lambda system, prompt: complete(system, prompt, FOCUS_GRAMMAR),
            observe,
            model=local_source_model.MODEL,
        )
    batch = [dict(id=i, **choice.description()) for i, choice in enumerate(choices, 1)]
    observe(
        f"{local_source_model.MODEL} examining {len(batch)} anonymized knowledge candidates on CPU"
    )
    response = json.loads(
        complete(
            "Choose the best supported evidence link or navigable group for a personal knowledge graph. "
            "Prefer multiple source families and supporting records. Each candidate has distinct indexed records. "
            "Return JSON containing only its numeric id. You may select zero to abstain.",
            json.dumps(batch),
            ID_GRAMMAR,
        )
    )
    if (
        not isinstance(response, dict)
        or set(response) != {"id"}
        or type(response["id"]) is not int
        or not 0 <= response["id"] <= len(choices)
    ):
        raise ValueError("Invalid local knowledge selection")
    with closing(sqlite3.connect(path, timeout=0.2)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if conversation_busy(state):
            return "Local knowledge review yielded to foreground conversation"
        allowed = set(SOURCES) | mac_context.enabled(state)
        current = local_links(db, allowed)
        choice = choices[response["id"] - 1] if response["id"] else None
        if (choice is None and current != choices) or (
            choice is not None and choice not in current
        ):
            return "Evidence changed before commit; retry on the next review"
        if choice is None:
            db.execute("""CREATE TABLE IF NOT EXISTS local_graph_abstentions(
                       project TEXT,peer TEXT,signature TEXT NOT NULL,PRIMARY KEY(project,peer)) WITHOUT ROWID""")
            db.executemany(
                "INSERT OR REPLACE INTO local_graph_abstentions VALUES(?,?,?)",
                ((link.project, link.peer, link.signature()) for link in choices),
            )
            receipt = "Abstained from linking the current evidence"
        elif choice.peer:
            db.execute("""CREATE TABLE IF NOT EXISTS reviewed_project_pairs(
                       a TEXT,b TEXT,model TEXT,PRIMARY KEY(a,b)) WITHOUT ROWID""")
            db.execute(
                "INSERT INTO reviewed_project_pairs VALUES(?,?,?)",
                (choice.project, choice.peer, local_source_model.MODEL),
            )
            receipt = f"Verified a shared-evidence link backed by {len(choice.proofs)} distinct indexed records"
        else:
            db.execute("""CREATE TABLE IF NOT EXISTS reviewed_source_groups(
                       project TEXT PRIMARY KEY,signature TEXT NOT NULL,model TEXT NOT NULL) WITHOUT ROWID""")
            db.execute(
                "INSERT OR REPLACE INTO reviewed_source_groups VALUES(?,?,?)",
                (choice.project, choice.signature(), local_source_model.MODEL),
            )
            receipt = (
                f"Verified a project evidence group linking {len(choice.proofs)} distinct indexed records "
                f"across {choice.description()['families']} source families"
            )
        receipt = f"{local_source_model.MODEL} · {receipt}"
        db.execute("""CREATE TABLE IF NOT EXISTS local_graph_review(
                   singleton INTEGER PRIMARY KEY CHECK(singleton=1),abstained TEXT,receipt TEXT,checked_at REAL)""")
        db.execute(
            "INSERT OR REPLACE INTO local_graph_review VALUES(1,?,?,?)",
            (signature if choice is None else None, receipt, time.time()),
        )
    observe(receipt)
    return receipt

"""Unified knowledge retrieval, situational synthesis, episodic critique, and autonomous Talos initiative."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from talos_core.cognition import talos_identity
from talos_core.mac import mac_context
from talos_core.memory import commitment_memory, semantic_engine
from talos_core.memory.transcript_memory import TEXT_SESSION, search_transcripts
from talos_core.runtime import background_work

BRIEF_FILE = "jarvis-brief.json"
POLICY_OVERRIDES_FILE = "policy-overrides.json"
VAULT_DIR = "Talos Memory"
_COGNITIVE_NS = uuid.UUID("7e4b2f09-64c8-4c5e-9d1c-8c80107a1055")

IMPLICIT_COMMITMENT = re.compile(
    r"\b(?:"
    r"(?:i|we)\s+(?:need|have|ought|plan|want)\s+to\s+"
    r"|don['’]?t\s+let\s+me\s+forget\s+to\s+"
    r"|make\s+sure\s+(?:i|we)\s+"
    r"|let['’]?s\s+(?:revisit|follow\s+up\s+on|check\s+on|finish)\s+"
    r")([^.!?\n]{4,160})",
    re.I,
)

USER_CORRECTION = re.compile(
    r"^(?:no[, ]+(?:i meant|that['’]?s (?:not|wrong|incorrect)|not that|wrong)|"
    r"that['’]?s (?:wrong|incorrect|not what i asked)|"
    r"you (?:missed|misunderstood|got that wrong|ignored)|"
    r"try again\b)",
    re.I,
)

DEGRADED_REPLY = (
    "couldn't put that answer into words",
    "reached its lookup limit",
    "temporary runtime error",
    "did not return a json decision",
)

NATURAL_KNOWLEDGE_QUERY = re.compile(
    r"^(?:(?:hey|ok|okay)[, ]+)?(?:talos[, ]+)?"
    r"(?:search my knowledge(?: base| database)? for|query my knowledge(?: base| database)? for|"
    r"what(?: all)? do you have in (?:my |your )?knowledge(?: base| database)? (?:about|on)|"
    r"check (?:your|my) knowledge(?: base| database)? (?:for|on|about)|"
    r"synthesize what you know about)\s+(.+?)[.!?]?$",
    re.I,
)

BRIEF_QUERY = re.compile(
    r"^(?:(?:hey|ok|okay)[, ]+)?(?:talos[, ]+)?"
    r"(?:brief me|give me (?:a |my )?(?:status |situational |daily |morning )?brief(?:ing)?|"
    r"status report|what['’]?s on my radar|jarvis brief(?:ing)?)[.!?]?$",
    re.I,
)

_STOPWORDS = frozenset({
    "a", "about", "all", "an", "and", "any", "are", "at", "be", "by", "can", "do",
    "for", "from", "have", "how", "i", "in", "into", "is", "it", "know", "me", "my",
    "of", "on", "or", "our", "search", "show", "tell", "that", "the", "this", "to",
    "up", "was", "we", "what", "when", "where", "which", "who", "with", "you", "your",
})


def ensure_tables(db: sqlite3.Connection) -> None:
    """Ensure conversational commitments, turn critiques, and cognitive intentions tables exist."""
    commitment_memory.schema(db)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY,
            session TEXT NOT NULL,
            commitment TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            review_after TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS turn_critiques (
            id INTEGER PRIMARY KEY,
            session TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            tool TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER NOT NULL DEFAULT 0,
            user_excerpt TEXT NOT NULL DEFAULT '',
            assistant_excerpt TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS cognitive_intentions (
            id INTEGER PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            summary TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'staged',
            created_at TEXT NOT NULL
        );
    """)


def _infer_state(db: sqlite3.Connection | None, state: Path | str | None = None) -> Path | None:
    if state is not None:
        return Path(state)
    if db is None:
        return None
    try:
        for row in db.execute("PRAGMA database_list").fetchall():
            name, file_path = row[1], row[2]
            if name == "main" and file_path and not str(file_path).startswith("file::memory:"):
                clean_path = str(file_path)
                if clean_path.startswith("file:"):
                    clean_path = clean_path.removeprefix("file:").split("?", 1)[0]
                candidate = Path(clean_path)
                if candidate.parent.exists():
                    return candidate.resolve().parent
    except sqlite3.Error:
        pass
    return None


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
            (name,),
        ).fetchone()
    )


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _tokens(query: str) -> list[str]:
    words = [w.casefold() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,39}", query or "")]
    filtered = [w for w in words if w not in _STOPWORDS]
    return (filtered or words)[:6]


def _fts_phrase(tokens: list[str]) -> str:
    escaped = ['"' + token.replace('"', '""') + '"*' for token in tokens if token]
    return " OR ".join(escaped)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def active_policy_hint(
    target: sqlite3.Connection | Path | str | None,
    *,
    state: Path | str | None = None,
) -> str:
    """Return active self-improvement policy guidance promoted by idle_lab, if any."""
    if state is not None:
        resolved = Path(state)
    elif isinstance(target, sqlite3.Connection):
        resolved = _infer_state(target, None)
    else:
        resolved = Path(target) if target is not None else None
    if resolved is None:
        return ""
    path = resolved / POLICY_OVERRIDES_FILE
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("status") != "active":
        return ""
    rules = [
        str(item.get("guidance") or item.get("hint") or "").strip()
        for item in data.get("policies", [])
        if isinstance(item, dict)
        and item.get("status", "active") == "active"
        and str(item.get("guidance") or item.get("hint") or "").strip()
    ]
    if not rules:
        single = str(data.get("guidance") or data.get("hint") or "").strip()
        if single:
            rules.append(single)
    if not rules:
        return ""
    return ("Active self-improvement policy: " + " ".join(rules[:2]))[:440]


def _search_agent_db(
    db: sqlite3.Connection,
    query: str,
    tokens: list[str],
    *,
    private_ok: bool,
    limit: int,
) -> list[dict]:
    ensure_tables(db)
    hits: list[dict] = []
    if not tokens:
        return hits

    def like_clause(cols: tuple[str, ...]) -> tuple[str, list[str]]:
        parts = []
        params: list[str] = []
        for token in tokens[:4]:
            pat = f"%{token}%"
            parts.append("(" + " OR ".join(f"lower({col}) LIKE ?" for col in cols) + ")")
            params.extend([pat] * len(cols))
        return " OR ".join(parts), params

    if private_ok and _has_table(db, "personal_facts"):
        where, params = like_clause(("subject", "value"))
        for row in db.execute(
            f"SELECT subject, value FROM personal_facts WHERE {where} ORDER BY subject LIMIT ?",
            (*params, limit),
        ):
            hits.append({
                "store": "personal_facts",
                "title": str(row["subject"]),
                "snippet": f"{row['subject']}: {row['value']}"[:220],
                "private": True,
            })

    if private_ok and _has_table(db, "commitments"):
        where, params = like_clause(("commitment",))
        for row in db.execute(
            f"SELECT id, commitment, status, review_after FROM commitments WHERE ({where}) ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ):
            hits.append({
                "store": "commitments",
                "title": f"Commitment #{row['id']} ({row['status']})",
                "snippet": f"{row['commitment']} (review {row['review_after'][:16]})"[:220],
                "private": True,
            })

    if _has_table(db, "context_evidence") and _has_table(db, "context_sources"):
        where, params = like_clause(("e.detail", "s.topic", "s.person"))
        privacy_sql = "" if private_ok else "AND s.speak = 1"
        for row in db.execute(
            f"""
            SELECT s.topic, s.person, s.speak, e.kind, e.detail, e.due
            FROM context_evidence e JOIN context_sources s ON s.id = e.source
            WHERE ({where}) {privacy_sql}
            ORDER BY e.stamp DESC, e.seq DESC LIMIT ?
            """,
            (*params, limit),
        ):
            due_note = f" (due {row['due']})" if row["due"] else ""
            hits.append({
                "store": f"context:{row['kind']}",
                "title": f"{row['topic']} · {row['person']}",
                "snippet": f"{row['detail']}{due_note}"[:220],
                "private": not bool(row["speak"]),
            })

    if _has_table(db, "goals"):
        where, params = like_clause(("outcome", "topic", "observation"))
        for row in db.execute(
            f"SELECT id, outcome, topic, deadline, status, observation FROM goals WHERE ({where}) ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ):
            obs = f" — {row['observation']}" if row["observation"] else ""
            hits.append({
                "store": "goals",
                "title": f"Goal #{row['id']}: {row['topic'] or row['outcome']}",
                "snippet": f"{row['outcome']} [{row['status']}, deadline {row['deadline'] or 'none'}]{obs}"[:240],
                "private": False,
            })

    if _has_table(db, "inbox"):
        where, params = like_clause(("text", "kind"))
        for row in db.execute(
            f"""
            SELECT id, kind, text, due_at, acknowledged_at FROM inbox
            WHERE ({where}) AND acknowledged_at IS NULL
            ORDER BY id DESC LIMIT ?
            """,
            (*params, limit),
        ):
            due = f" (due {row['due_at'][:16]})" if row["due_at"] else ""
            hits.append({
                "store": f"inbox:{row['kind']}",
                "title": f"{row['kind'].title()} #{row['id']}",
                "snippet": f"{row['text']}{due}"[:220],
                "private": False,
            })

    if private_ok and _has_table(db, "transcripts"):
        try:
            for item in search_transcripts(db, query)[: max(2, limit // 2)]:
                snippet = str(item.get("excerpt") or item.get("text") or "").strip()
                if snippet:
                    hits.append({
                        "store": "transcripts",
                        "title": f"Session {item.get('session', TEXT_SESSION)} ({item.get('role', 'turn')})",
                        "snippet": snippet[:220],
                        "private": True,
                    })
        except sqlite3.Error:
            pass

    return hits


def _extract_review_texts(raw_findings: str) -> list[str]:
    try:
        parsed = json.loads(raw_findings)
    except (TypeError, ValueError):
        return [str(raw_findings)] if raw_findings else []
    texts: list[str] = []
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, str):
                texts.append(entry)
            elif isinstance(entry, dict):
                parts = [
                    str(entry.get(k, "")).strip()
                    for k in ("summary", "claim", "finding", "title", "detail", "note")
                    if entry.get(k)
                ]
                texts.append(" — ".join(p for p in parts if p) or json.dumps(entry, ensure_ascii=False))
    elif isinstance(parsed, dict):
        texts.append(json.dumps(parsed, ensure_ascii=False))
    return [t for t in texts if t]


def _search_personal_index(
    state: Path | None,
    tokens: list[str],
    *,
    private_ok: bool,
    limit: int,
) -> tuple[list[dict], dict]:
    meta = {"personal_index_available": False, "mac_context_enabled": False}
    if state is None:
        return [], meta
    state_path = Path(state)
    index_path = state_path / mac_context.INDEX_NAME
    settings_file = state_path / "mac-context.json"
    enabled_apps = mac_context.enabled(state_path) if settings_file.exists() else set(mac_context.APPS)
    mac_on = bool(enabled_apps)
    meta["mac_context_enabled"] = mac_on
    if not index_path.is_file():
        return [], meta
    meta["personal_index_available"] = True
    if not private_ok or not tokens:
        return [], meta

    hits: list[dict] = []
    try:
        with closing(sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)) as pdb:
            pdb.row_factory = sqlite3.Row
            if _has_table(pdb, "personal"):
                cols = _columns(pdb, "personal")
                fts = _fts_phrase(tokens)
                if {"app", "source", "record", "body"} <= cols:
                    rows = pdb.execute(
                        """
                        SELECT app, source, record, snippet(personal, 3, '[', ']', ' … ', 20) AS excerpt
                        FROM personal WHERE personal MATCH ? ORDER BY bm25(personal) LIMIT ?
                        """,
                        (fts, limit * 2),
                    ).fetchall()
                    enabled_lower = {item.casefold() for item in enabled_apps}
                    apps_lower = {item.casefold() for item in mac_context.APPS}
                    for row in rows:
                        app = str(row["app"] or "")
                        if app.casefold() in apps_lower and app.casefold() not in enabled_lower:
                            continue
                        hits.append({
                            "store": f"personal_index:{app}",
                            "title": f"{row['record'] or row['source']} ({app})",
                            "snippet": str(row["excerpt"] or "")[:240],
                            "source": str(row["source"] or ""),
                            "private": True,
                        })
                        if len(hits) >= limit:
                            break

            if _has_table(pdb, "ai_memory_reviews"):
                cols = _columns(pdb, "ai_memory_reviews")
                if "findings" in cols:
                    for row in pdb.execute("SELECT project, findings FROM ai_memory_reviews LIMIT 50"):
                        project = str(row["project"] or "")
                        for finding_text in _extract_review_texts(row["findings"]):
                            hay = f"{project} {finding_text}".casefold()
                            if any(tok in hay for tok in tokens):
                                hits.append({
                                    "store": "ai_memory_reviews",
                                    "title": f"Memory synthesis · {project}",
                                    "snippet": finding_text[:240],
                                    "private": True,
                                })
                elif "summary" in cols:
                    for row in pdb.execute("SELECT project, summary FROM ai_memory_reviews LIMIT 50"):
                        hay = f"{row['project']} {row['summary']}".casefold()
                        if any(tok in hay for tok in tokens):
                            hits.append({
                                "store": "ai_memory_reviews",
                                "title": f"Memory synthesis · {row['project']}",
                                "snippet": str(row["summary"])[:240],
                                "private": True,
                            })

            if _has_table(pdb, "project_review_focus"):
                for row in pdb.execute("SELECT project, focus, model FROM project_review_focus LIMIT 50"):
                    hay = f"{row['project']} {row['focus']}".casefold()
                    if any(tok in hay for tok in tokens):
                        hits.append({
                            "store": "graph_review",
                            "title": f"Graph focus · {row['project']}",
                            "snippet": f"{row['focus']} ({row['model']})"[:240],
                            "private": True,
                        })

            if _has_table(pdb, "reviewed_project_pairs"):
                for row in pdb.execute("SELECT a, b, model FROM reviewed_project_pairs LIMIT 50"):
                    hay = f"{row['a']} {row['b']}".casefold()
                    if any(tok in hay for tok in tokens):
                        hits.append({
                            "store": "graph_links",
                            "title": f"Linked projects · {row['a']} <-> {row['b']}",
                            "snippet": f"Verified cross-project link ({row['model']})",
                            "private": True,
                        })

            if _has_table(pdb, "memory_connections"):
                clauses = " OR ".join("lower(project) LIKE ? OR lower(source) LIKE ? OR lower(record) LIKE ?" for _ in tokens[:3])
                params: list[object] = []
                for tok in tokens[:3]:
                    pat = f"%{tok}%"
                    params.extend([pat, pat, pat])
                for row in pdb.execute(
                    f"SELECT project, app, source, record FROM memory_connections WHERE {clauses} LIMIT ?",
                    (*params, max(3, limit // 2)),
                ):
                    hits.append({
                        "store": "memory_connections",
                        "title": f"Connection · {row['project']} ({row['app']})",
                        "snippet": f"{row['source']} · {row['record']}"[:220],
                        "private": True,
                    })
    except sqlite3.Error:
        pass
    return hits, meta


def _search_vault(
    state: Path | None,
    tokens: list[str],
    *,
    private_ok: bool,
    limit: int,
) -> tuple[list[dict], bool]:
    if state is None:
        return [], False
    vault = Path(state) / VAULT_DIR
    if not vault.is_dir():
        return [], False
    if not private_ok or not tokens:
        return [], True

    scored: list[tuple[int, str, str]] = []
    for note_path in sorted(vault.rglob("*.md"))[:80]:
        try:
            text = note_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lowered = text.casefold()
        name_low = note_path.stem.casefold()
        score = sum(3 if tok in name_low else (1 if tok in lowered else 0) for tok in tokens)
        if score <= 0:
            continue
        excerpt = ""
        for line in text.splitlines():
            clean_line = line.strip()
            if clean_line and any(tok in clean_line.casefold() for tok in tokens):
                excerpt = clean_line
                break
        if not excerpt:
            excerpt = " ".join(text.split())[:200]
        scored.append((score, note_path.name, excerpt[:240]))

    scored.sort(key=lambda item: (-item[0], item[1]))
    hits = [
        {
            "store": "vault",
            "title": name,
            "snippet": excerpt,
            "private": True,
        }
        for _, name, excerpt in scored[: max(2, limit // 2)]
    ]
    return hits, True


def query_knowledge(
    db: sqlite3.Connection,
    query: str,
    *,
    state: Path | str | None = None,
    private_ok: bool = True,
    limit: int = 8,
) -> dict:
    """Search across Talos's unified knowledge databases using a hybrid of semantic and keyword search."""
    resolved_state = _infer_state(db, state)
    clean_query = " ".join((query or "").split())

    # 1. Identity Resolution Layer
    # Handle "who am i" or "where do i live" queries before general search
    if resolved_state:
        resolver = talos_identity.get_resolver(Path(resolved_state))
        identity_context = resolver.resolve_identity(clean_query)
        if identity_context:
            return {
                "query": clean_query,
                "private_context": private_ok,
                "count": 1,
                "stores_searched": ["identity_resolver"],
                "results": [{
                    "store": "identity",
                    "title": "User Profile",
                    "snippet": f"User: {identity_context['identity']['full_name']} | Address: {identity_context['identity']['address']}",
                    "private": True,
                }],
                "items": [{
                    "store": "identity",
                    "title": "User Profile",
                    "snippet": f"User: {identity_context['identity']['full_name']} | Address: {identity_context['identity']['address']}",
                    "private": True,
                }],
            }

    tokens = _tokens(clean_query)
    capped = max(1, min(int(limit), 20))

    # 2. Semantic Search (Vector)
    semantic_hits = []
    try:
        semantic_hits = semantic_engine.engine.search(clean_query, limit=capped)
        for hit in semantic_hits:
            semantic_hits[semantic_hits.index(hit)] = {
                "store": hit["store"],
                "title": hit["text"][:50],
                "snippet": hit["text"],
                "private": True,
            }
    except Exception:
        pass

    # 3. Keyword Search (Existing logic)
    personal_hits, index_meta = _search_personal_index(
        resolved_state, tokens, private_ok=private_ok, limit=capped
    )
    vault_hits, vault_available = _search_vault(
        resolved_state, tokens, private_ok=private_ok, limit=capped
    )
    agent_hits = _search_agent_db(
        db, clean_query, tokens, private_ok=private_ok, limit=capped
    )

    combined: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for item in (*semantic_hits, *personal_hits, *vault_hits, *agent_hits):
        sig = (item["store"], item["title"], item["snippet"][:80])
        if sig in seen:
            continue
        seen.add(sig)
        normalized = {
            **item,
            "source": item["title"],
            "excerpt": item["snippet"],
        }
        combined.append(normalized)
        if len(combined) >= capped:
            break

    stores_searched = ["goals", "inbox"]
    if private_ok:
        stores_searched.extend(["personal_facts", "commitments", "context_evidence", "transcripts"])
        if index_meta["personal_index_available"] and index_meta["mac_context_enabled"]:
            stores_searched.extend(["personal_index", "ai_memory_reviews", "graph_review", "memory_connections"])
        if vault_available:
            stores_searched.append("vault")
        stores_searched.append("semantic_vector")

    return {
        "query": clean_query,
        "private_context": private_ok,
        "count": len(combined),
        "stores_searched": stores_searched,
        "results": combined,
        "items": combined,
    }


def knowledge_status(db: sqlite3.Connection, state: Path | str | None = None) -> dict:
    """Return concrete availability and row counts across every Talos knowledge store."""
    ensure_tables(db)
    resolved_state = _infer_state(db, state)
    agent_counts: dict[str, int] = {}
    for table in ("transcripts", "personal_facts", "commitments", "context_evidence", "goals", "inbox", "background_work", "turn_critiques", "cognitive_intentions"):
        if _has_table(db, table):
            agent_counts[table] = int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        else:
            agent_counts[table] = 0

    personal_counts: dict[str, int] = {}
    index_exists = False
    mac_on = True
    if resolved_state is not None:
        settings_file = resolved_state / "mac-context.json"
        if settings_file.exists():
            mac_on = bool(mac_context.enabled(resolved_state))
        index_path = resolved_state / mac_context.INDEX_NAME
        if index_path.is_file():
            index_exists = True
            try:
                with closing(sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)) as pdb:
                    for table in ("personal", "memory_connections", "ai_memory_reviews", "reviewed_source_groups", "reviewed_project_pairs"):
                        if _has_table(pdb, table):
                            personal_counts[table] = int(pdb.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                pass

    vault_available = resolved_state is not None and (resolved_state / VAULT_DIR).is_dir()
    vault_notes = 0
    if vault_available:
        vault_notes = sum(1 for _ in (resolved_state / VAULT_DIR).rglob("*.md"))

    stores = {
        "personal_index": {
            "available": index_exists and mac_on,
            "count": personal_counts.get("personal", 0),
        },
        "ai_memory_reviews": {
            "available": index_exists and mac_on,
            "count": personal_counts.get("ai_memory_reviews", 0),
        },
        "memory_connections": {
            "available": index_exists and mac_on,
            "count": personal_counts.get("memory_connections", 0),
        },
        "vault": {
            "available": vault_available,
            "count": vault_notes,
        },
        **{
            table: {"available": True, "count": count}
            for table, count in agent_counts.items()
        },
    }

    return {
        "state": str(resolved_state) if resolved_state else None,
        "mac_context_enabled": mac_on,
        "personal_index_available": index_exists,
        "personal_index_counts": personal_counts,
        "vault_notes": vault_notes,
        "agent_counts": agent_counts,
        "stores": stores,
    }


def capture_implicit_intent(
    db: sqlite3.Connection,
    text: str,
    *,
    session: str = TEXT_SESSION,
    now: datetime | None = None,
) -> dict | None:
    """Capture implicit user obligations ('I need to...', 'don't let me forget to...') into commitments."""
    ensure_tables(db)
    clean = " ".join((text or "").split())
    if not clean or clean.endswith("?"):
        return None
    match = IMPLICIT_COMMITMENT.search(clean)
    if not match:
        return None
    item = match.group(1).strip(" ,.;:-")
    if len(item) < 5 or re.search(r"\b(?:you|talos)\b", item, re.I):
        return None
    current = now or datetime.now(UTC)
    existing = db.execute(
        "SELECT id FROM commitments WHERE status='open' AND lower(commitment)=lower(?)",
        (item,),
    ).fetchone()
    if existing:
        return None
    lowered = clean.casefold()
    if "tomorrow" in lowered or "tonight" in lowered:
        delay = timedelta(hours=12)
    elif "soon" in lowered or "hour" in lowered or "today" in lowered:
        delay = timedelta(hours=4)
    elif "week" in lowered:
        delay = timedelta(days=3)
    else:
        delay = timedelta(hours=18)
    review_after = (current + delay).isoformat()
    with db:
        cur = db.execute(
            "INSERT INTO commitments(session, commitment, status, created_at, review_after) VALUES(?,?,'open',?,?)",
            (session, item[:200], current.isoformat(), review_after),
        )
    return {"id": cur.lastrowid, "commitment": item[:200], "review_after": review_after}


def critique_turn(
    db: sqlite3.Connection,
    user_text: str,
    assistant_text: str,
    *,
    session: str = TEXT_SESSION,
    tool: str = "",
    latency_ms: int = 0,
    status: str = "ok",
    now: datetime | None = None,
) -> dict | None:
    """Record episodic self-critique signals from user corrections, degraded replies, or slow/failed turns."""
    ensure_tables(db)
    current = (now or datetime.now(UTC)).isoformat()
    u_clean = " ".join((user_text or "").split())
    a_clean = " ".join((assistant_text or "").split())

    kind = ""
    reason = ""
    if status == "failed":
        kind, reason = "runtime_failure", f"Turn failed during tool={tool or 'unknown'}."
    elif USER_CORRECTION.search(u_clean):
        kind, reason = "user_correction", "User explicitly corrected or rejected the preceding response."
    elif tool in ("", "none", "console") and re.search(
        r"\bI(?:['’]ve| have)? (?:just |already )?(?:sent|scheduled|booked|deleted|emailed|texted|"
        r"(?:set|created|saved) (?:you |your |a |the )?(?:reminder|task|message|note))\b",
        a_clean,
        re.I,
    ):
        kind, reason = "unverified_claim", "Assistant claimed an unexecuted local or external action without a tool receipt."
    elif any(marker in a_clean.casefold() for marker in DEGRADED_REPLY):
        kind, reason = "degraded_reply", "Assistant emitted a fallback or degraded reply."
    elif latency_ms >= 8000:
        kind, reason = "high_latency", f"Turn exceeded latency budget ({latency_ms} ms)."

    if not kind:
        return None

    with db:
        cur = db.execute(
            """
            INSERT INTO turn_critiques(session, recorded_at, kind, tool, latency_ms, user_excerpt, assistant_excerpt, reason)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (session, current, kind, tool, int(latency_ms), u_clean[:240], a_clean[:240], reason),
        )
    return {"id": cur.lastrowid, "kind": kind, "reason": reason}


def situational_brief(
    db: sqlite3.Connection,
    state: Path | str | None = None,
    *,
    now: datetime | None = None,
    private_ok: bool = True,
) -> dict:
    """Synthesize a Talos-style situational radar across goals, commitments, tasks, background work, and insights."""
    ensure_tables(db)
    resolved_state = _infer_state(db, state)
    current = now or datetime.now(UTC)
    now_iso = current.isoformat()
    soon_iso = (current + timedelta(hours=24)).isoformat()

    active_goals: list[dict] = []
    urgent_goals: list[dict] = []
    if _has_table(db, "goals"):
        for row in db.execute(
            "SELECT id, outcome, topic, deadline, observation FROM goals WHERE status='active' ORDER BY deadline, id LIMIT 10"
        ):
            entry = {
                "id": row["id"],
                "outcome": row["outcome"],
                "topic": row["topic"],
                "deadline": row["deadline"],
                "observation": row["observation"],
            }
            active_goals.append(entry)
            deadline = str(row["deadline"] or "")
            if deadline and deadline <= soon_iso[: len(deadline)]:
                urgent_goals.append(entry)

    overdue_commitments: list[dict] = []
    open_commitments: list[dict] = []
    if private_ok and _has_table(db, "commitments"):
        for row in db.execute(
            "SELECT id, commitment, review_after FROM commitments WHERE status='open' ORDER BY review_after, id LIMIT 10"
        ):
            entry = {"id": row["id"], "commitment": row["commitment"], "review_after": row["review_after"]}
            open_commitments.append(entry)
            if row["review_after"] <= now_iso:
                overdue_commitments.append(entry)

    if private_ok and _has_table(db, "context_evidence") and _has_table(db, "context_sources"):
        today_str = current.strftime("%Y-%m-%d")
        for row in db.execute(
            """
            SELECT s.topic, s.person, e.detail, e.due
            FROM context_evidence e JOIN context_sources s ON s.id = e.source
            WHERE e.kind='promise' ORDER BY e.stamp DESC LIMIT 6
            """
        ):
            entry = {
                "id": f"src:{row['topic']}",
                "commitment": f"{row['detail']} ({row['person']})",
                "review_after": row["due"] or "",
            }
            open_commitments.append(entry)
            if row["due"] and str(row["due"]) <= today_str:
                overdue_commitments.append(entry)

    open_tasks: list[str] = []
    if _has_table(db, "inbox"):
        for row in db.execute(
            "SELECT kind, text, due_at FROM inbox WHERE acknowledged_at IS NULL AND kind IN ('task', 'reminder') ORDER BY id DESC LIMIT 6"
        ):
            open_tasks.append(f"{row['kind']}: {row['text']}")

    queued_jobs = 0
    if _has_table(db, "background_work"):
        queued_jobs = int(
            db.execute("SELECT count(*) FROM background_work WHERE status IN ('queued', 'running')").fetchone()[0]
        )

    insights: list[str] = []
    if private_ok and resolved_state is not None:
        index_path = resolved_state / mac_context.INDEX_NAME
        if index_path.is_file():
            try:
                with closing(sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.5)) as pdb:
                    pdb.row_factory = sqlite3.Row
                    if _has_table(pdb, "ai_memory_reviews") and "findings" in _columns(pdb, "ai_memory_reviews"):
                        for row in pdb.execute(
                            "SELECT project, findings FROM ai_memory_reviews ORDER BY checked_ns DESC LIMIT 3"
                        ):
                            for txt in _extract_review_texts(row["findings"])[:1]:
                                insights.append(f"{row['project']}: {txt[:140]}")
            except sqlite3.Error:
                pass

    unresolved_critiques = int(
        db.execute("SELECT count(*) FROM turn_critiques WHERE resolved=0").fetchone()[0]
    )

    parts: list[str] = []
    if urgent_goals:
        parts.append(
            f"{len(urgent_goals)} urgent goal(s): "
            + "; ".join(f"{g['outcome']} (by {g['deadline']})" for g in urgent_goals[:2])
        )
    elif active_goals:
        parts.append(f"{len(active_goals)} active goal(s) on track")
    if overdue_commitments:
        parts.append(
            f"{len(overdue_commitments)} commitment(s) ready for follow-up: "
            + "; ".join(c["commitment"] for c in overdue_commitments[:2])
        )
    elif open_commitments:
        parts.append(f"{len(open_commitments)} tracked commitment(s)")
    if open_tasks:
        parts.append(f"{len(open_tasks)} open task/reminder item(s)")
    if queued_jobs:
        parts.append(f"{queued_jobs} background job(s) active")
    if insights:
        parts.append("Recent knowledge insight — " + insights[0])
    if not parts:
        parts.append("All queues clear; no urgent goals, overdue commitments, or pending tasks.")

    headline = ". ".join(parts) + "."
    return {
        "generated_at": now_iso,
        "headline": headline,
        "summary": headline,
        "active_goals": active_goals,
        "urgent_goals": urgent_goals,
        "open_commitments": open_commitments,
        "overdue_commitments": overdue_commitments,
        "open_tasks": open_tasks,
        "queued_background_jobs": queued_jobs,
        "knowledge_insights": insights,
        "unresolved_critiques": unresolved_critiques,
    }


def _queue_autonomous_work(db: sqlite3.Connection, source_key: str, task_text: str, now_iso: str) -> None:
    background_work.ensure(db)
    req_id = str(uuid.uuid5(_COGNITIVE_NS, source_key))
    db.execute(
        """
        INSERT OR IGNORE INTO background_work(request_id, task, status, available)
        VALUES(?, ?, 'queued', ?)
        """,
        (req_id, task_text[:280], now_iso),
    )


def _stage_intention(
    db: sqlite3.Connection,
    source_key: str,
    kind: str,
    summary: str,
    action: str,
    now_iso: str,
) -> bool:
    cur = db.execute(
        """
        INSERT OR IGNORE INTO cognitive_intentions(source_key, kind, summary, action, status, created_at)
        VALUES(?, ?, ?, ?, 'staged', ?)
        """,
        (source_key, kind, summary[:240], action, now_iso),
    )
    return cur.rowcount > 0


def think_once(
    db: sqlite3.Connection,
    state: Path | str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Run one bounded, idempotent Talos self-thinking cycle and persist jarvis-brief.json."""
    ensure_tables(db)
    resolved_state = _infer_state(db, state)
    current = now or datetime.now(UTC)
    now_iso = current.isoformat()
    brief = situational_brief(db, resolved_state, now=current, private_ok=True)

    staged: list[dict] = []
    with db:
        for goal in brief["urgent_goals"][:3]:
            key = f"urgent-goal:{goal['id']}:{goal['deadline']}"
            summary = f"Prepare readiness check for urgent goal: {goal['outcome']}"
            if _stage_intention(db, key, "goal_preparation", summary, "background_work", now_iso):
                _queue_autonomous_work(db, key, summary, now_iso)
                staged.append({"kind": "goal_preparation", "summary": summary})

        for item in brief["overdue_commitments"][:3]:
            key = f"overdue-commitment:{item['id']}:{item['review_after'][:10]}"
            summary = f"Follow up on commitment: {item['commitment']}"
            if _stage_intention(db, key, "commitment_followup", summary, "inbox_reminder", now_iso):
                if _has_table(db, "inbox") and _has_table(db, "decisions"):
                    event = json.dumps({"kind": "cognitive_followup", "summary": summary[:240]})
                    proposal = json.dumps({"disposition": "act", "tool": "set_reminder", "detail": summary[:240]})
                    decision_row = db.execute(
                        "INSERT INTO decisions(recorded_at, event, proposal) VALUES(?, ?, ?)",
                        (now_iso, event, proposal),
                    )
                    db.execute(
                        "INSERT INTO inbox(id, kind, text, due_at) VALUES(?, 'reminder', ?, ?)",
                        (decision_row.lastrowid, summary[:240], now_iso),
                    )
                    if _has_table(db, "action_outcomes"):
                        db.execute(
                            "INSERT INTO action_outcomes(decision_id, status) VALUES(?, 'applied')",
                            (decision_row.lastrowid,),
                        )
                elif _has_table(db, "inbox"):
                    db.execute(
                        "INSERT INTO inbox(kind, text, due_at) VALUES('reminder', ?, ?)",
                        (summary[:240], now_iso),
                    )
                else:
                    _queue_autonomous_work(db, key, summary, now_iso)
                staged.append({"kind": "commitment_followup", "summary": summary})

        for insight in brief["knowledge_insights"][:2]:
            digest = uuid.uuid5(_COGNITIVE_NS, insight).hex[:12]
            key = f"knowledge-insight:{digest}"
            summary = f"Synthesize cross-source insight: {insight[:180]}"
            if _stage_intention(db, key, "knowledge_synthesis", summary, "brief", now_iso):
                staged.append({"kind": "knowledge_synthesis", "summary": summary})

    brief["staged_intentions"] = staged
    if resolved_state is not None:
        try:
            _atomic_write_json(Path(resolved_state) / BRIEF_FILE, brief)
        except OSError:
            pass
    return {
        "headline": brief["headline"],
        "brief": brief,
        "staged_count": len(staged),
        "staged": staged,
        "unresolved_critiques": brief["unresolved_critiques"],
    }


def answer_room_cognitive(
    db: sqlite3.Connection,
    text: str,
    *,
    state: Path | str | None = None,
    voice: bool = False,
    private_ok: bool | None = None,
) -> dict | None:
    """Handle direct Talos briefing and unified knowledge-base queries in Room and Console."""
    clean = " ".join((text or "").split())
    if not clean:
        return None
    allow_private = (not voice) if private_ok is None else bool(private_ok)

    if BRIEF_QUERY.fullmatch(clean):
        brief = situational_brief(db, state, private_ok=allow_private)
        return {"reply": brief["headline"], "brief": brief}

    match = NATURAL_KNOWLEDGE_QUERY.fullmatch(clean)
    if match:
        topic = match.group(1).strip()
        if voice and not allow_private:
            public_hits = query_knowledge(db, topic, state=state, private_ok=False, limit=4)
            if not public_hits["results"]:
                return {
                    "reply": "Private knowledge-base records stay silent on unverified room voice. Ask in private text or with verified speaker identity."
                }
            lines = [f"{r['title']}: {r['snippet']}" for r in public_hits["results"][:3]]
            return {"reply": f"From shared goals and tasks on {topic}: " + " | ".join(lines)}

        found = query_knowledge(db, topic, state=state, private_ok=allow_private, limit=5)
        if not found["results"]:
            return {"reply": f"No matching records found across the Talos knowledge database for '{topic}'."}
        parts = [f"[{r['store']}] {r['title']}: {r['snippet']}" for r in found["results"][:4]]
        return {"reply": f"Found {found['count']} knowledge record(s) for '{topic}': " + " | ".join(parts)}

    return None

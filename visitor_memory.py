"""Local, source-linked memory from retained conversations. Visual templates stay out of Markdown."""

import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from transcript_memory import fts_query, insert_transcripts


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS visitors(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, template BLOB NOT NULL, created TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS conversation_reviews(
        source INTEGER PRIMARY KEY REFERENCES transcripts(id), exported INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS conversation_facts(
        source INTEGER REFERENCES transcripts(id), kind TEXT, quote TEXT, subject TEXT,
        PRIMARY KEY(source,kind,quote))""")


def visit_session(db, profile):
    if (
        not isinstance(profile, str)
        or not db.execute("SELECT 1 FROM visitors WHERE id=? AND length(template)>0", (profile,)).fetchone()
    ):
        raise ValueError("Unknown visitor profile.")
    return "visit/" + profile


def enroll(db, body):
    if body.get("agreed") is not True:
        raise ValueError("A confirmed introduction is required.")
    profile = str(UUID(str(body.get("id", ""))))
    name = body.get("name")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[^\W\d_]+(?:[ ’'-][^\W\d_]+){0,3}", name)
        or len(name) > 60
    ):
        raise ValueError("Use a short spoken name.")
    encoded = body.get("template")
    if not isinstance(encoded, str) or len(encoded) > 131072:
        raise ValueError("Invalid visual template.")
    data = base64.b64decode(encoded, validate=True)
    if not 128 <= len(data) <= 98304:
        raise ValueError("Invalid visual template.")
    introduction = body.get("introduction")
    if not isinstance(introduction, str) or not 1 <= len(introduction.strip()) <= 800:
        raise ValueError("A spoken introduction is required.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT name,template FROM visitors WHERE id=?", (profile,)).fetchone()
        if old:
            if old["name"] != name or old["template"] != data:
                raise ValueError("Profile already exists.")
        else:
            db.execute(
                "INSERT INTO visitors VALUES(?,?,?,?)", (profile, name, data, datetime.now(UTC).isoformat())
            )
            insert_transcripts(db, "visit/" + profile, [{"role": "user", "content": introduction}])
    return {"id": profile, "name": name}


def profiles(db):
    return [
        {"id": r["id"], "name": r["name"], "template": base64.b64encode(r["template"]).decode("ascii")}
        for r in db.execute("SELECT id,name,template FROM visitors WHERE length(template)>0 ORDER BY created")
    ]


# Keep statements as quotations. Their speaker is only known in a confirmed visit.
_FACT = re.compile(
    r"^(?:actually[, ]+)?(?:my (?:name is|preferred name is|role is)|"
    r"i(?: am|'m|’m)|i (?:work|study|teach|live|prefer|like|build|manage|lead|play|"
    r"will|promised|agreed|volunteer)|i['’]ll)\b",
    re.I,
)
_TOPIC = re.compile(
    r"\b(?:work(?:ing)? on|study|teach|manage|lead|prefer|volunteer at|work at|work for)\s+(.+)", re.I
)


def introduced_name(text):
    match = re.match(r"^(?:my name is|call me)\s+([^.!?,;]+)(?:[.!?,;]|$)", text.strip(), re.I)
    if not match:
        return None
    name = match[1].strip()
    return name if len(name) <= 60 and re.fullmatch(r"[^\W\d_]+(?:[ ’'-][^\W\d_]+){0,2}", name) else None


def facts(text):
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if not _FACT.match(sentence) or sentence.endswith("?"):
            continue
        match = _TOPIC.search(sentence)
        subject = match[1].strip(" .!?") if match else ""
        if len(subject) > 70 or len(subject.split()) > 8:
            subject = ""
        kind = (
            "Commitment"
            if re.search(r"^i(?: will| promised| agreed|['’]ll)\b", sentence, re.I)
            else "Self-description"
        )
        yield kind, sentence, subject


def reviewed_facts(text, infer=None):
    entries = list(facts(text))
    if entries or infer is None or not re.search(r"\b(?:I|my|we|our)\b", text, re.I) or "?" in text:
        return entries
    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["Commitment", "Self-description"]},
                        "quote": {"type": "string"},
                        "subject": {"type": "string"},
                    },
                    "required": ["kind", "quote", "subject"],
                },
            }
        },
        "required": ["facts"],
    }
    try:
        raw = infer(
            [
                {
                    "role": "system",
                    "content": "Review one statement for a personal activity, role, preference or commitment. "
                    "Return facts only about what the speaker affirms. Quote a complete exact sentence verbatim. "
                    "Use a short exact phrase from the quote for its topic. Omit speculation, requests, and third-party claims.",
                },
                {"role": "user", "content": json.dumps({"statement": text})},
            ],
            schema=schema,
            max_tokens=240,
        )
        candidates = json.loads(raw)["facts"]
        sentences = {s.strip() for s in re.split(r"(?<=[.!?])\s+", text)}
        for item in candidates[:3]:
            quote, subject, kind = item["quote"], item["subject"], item["kind"]
            if (
                quote in sentences
                and re.search(r"\b(?:I|my|we|our)\b", quote, re.I)
                and isinstance(subject, str)
                and 1 <= len(subject) <= 70
                and subject in quote
                and kind in ("Commitment", "Self-description")
                and (kind, quote, subject) not in entries
            ):
                entries.append((kind, quote, subject))
    except (ValueError, RuntimeError, TypeError, KeyError):
        pass
    return entries


def _literal(text):
    # Keep quoted speech inert in readers that support Markdown, embeds and HTML.
    return "".join(c if c.isalnum() or c.isspace() else f"&#{ord(c)};" for c in text)


def _note(folder, filename, text):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        return
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(text.encode())
            f.flush()
            os.fsync(f.fileno())
            # Atomic and create-only, including if another reviewer exports concurrently.
            try:
                os.link(tmp, target)
            except FileExistsError:
                pass
        finally:
            tmp.unlink(missing_ok=True)


def review_one(db, vault, infer=None):
    row = db.execute("""SELECT t.* FROM transcripts t LEFT JOIN conversation_reviews r ON r.source=t.id
        WHERE t.role='user' AND (t.session IN ('native_voice','text') OR t.session LIKE 'visit/%')
        AND COALESCE(r.exported,0)=0 ORDER BY t.id LIMIT 1""").fetchone()
    if not row:
        return False
    profile = row["session"].removeprefix("visit/") if row["session"].startswith("visit/") else None
    person = db.execute("SELECT name FROM visitors WHERE id=?", (profile,)).fetchone() if profile else None
    source = row["id"]
    if db.execute("SELECT 1 FROM conversation_reviews WHERE source=?", (source,)).fetchone():
        entries = db.execute("SELECT kind,quote,subject FROM conversation_facts WHERE source=?", (source,)).fetchall()
    else:
        entries = reviewed_facts(row["text"], infer)
        with db:
            db.execute("INSERT OR IGNORE INTO conversation_reviews(source) VALUES(?)", (source,))
            db.executemany("INSERT OR IGNORE INTO conversation_facts VALUES(?,?,?,?)", [(source, *f) for f in entries])
    lines = [f"# Conversation {source}", "", row["recorded_at"], ""]
    if person:
        person_node = f"Person-{person['name']}-{profile}"
        _note(
            vault,
            person_node + ".md",
            f"# {person['name']}\n\nConfirmed introduction. Linked conversations appear in backlinks.\n",
        )
        lines += [f"Speaker: [[{person_node}|{person['name']}]]", ""]
    elif name := introduced_name(row["text"]):
        node = f"Introduction-{source}"
        _note(vault, node + ".md", f"# {name}\n\nSelf-reported name from one exchange.\n")
        lines += [f"Introduced as [[{node}|{name}]]", ""]
    else:
        lines += ["Speaker: unassigned", ""]
    lines += ["> " + _literal(row["text"]).replace("\n", "\n> "), ""]
    # insert_transcripts timestamps both sides of a submitted exchange together.
    answer = db.execute(
        "SELECT text FROM transcripts WHERE id=? AND session=? AND recorded_at=? AND role='assistant'",
        (source + 1, row["session"], row["recorded_at"]),
    ).fetchone()
    if answer:
        lines += ["Talos replied", "", "> " + _literal(answer[0]).replace("\n", "\n> "), ""]
    for kind, quote, subject in entries:
        lines += [f"- {kind}: {_literal(quote)}"]
        if subject:
            label = re.sub(r"[^\w -]+", "", subject.casefold())[:48].strip()
            node = "Topic-" + label + "-" + hashlib.sha256(subject.casefold().encode()).hexdigest()[:12]
            _note(vault, node + ".md", "# " + _literal(subject) + "\n")
            lines += [f"  - [[{node}|{_literal(subject)}]]"]
    _note(vault, f"Conversation-{source}.md", "\n".join(lines) + "\n")
    with db:
        db.execute("UPDATE conversation_reviews SET exported=1 WHERE source=?", (source,))
    return True


def recall(db, profile, query=""):
    session = visit_session(db, profile)
    match = fts_query(query)
    if match:
        relevant = db.execute(
            """SELECT f.quote,f.source FROM transcript_search s
            JOIN transcripts t ON t.id = s.rowid JOIN conversation_facts f ON f.source=t.id
            WHERE transcript_search MATCH ? AND t.session=? ORDER BY rank,t.id DESC LIMIT 3""",
            (match, session),
        ).fetchall()
        if relevant:
            return [dict(r) for r in relevant]
    rows = db.execute(
        """SELECT f.quote,f.source FROM conversation_facts f JOIN transcripts t ON t.id=f.source
                    WHERE t.session=? ORDER BY f.source DESC LIMIT 3""",
        (session,),
    ).fetchall()
    return [{"quote": r[0], "source": r[1]} for r in rows]

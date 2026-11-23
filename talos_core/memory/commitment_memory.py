"""Local, source-linked commitments from operator-selected files. No account credentials."""

import argparse
import csv
import email
import hashlib
import io
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

KINDS = ("email", "imessage", "whatsapp", "document", "calendar")
DAYS = r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{4}-\d{2}-\d{2}"
PROMISE = re.compile(
    r"\bI(?:['’]ll| will| promise to| agreed to)\s+([^.!?\n]{2,220})", re.I
)
DATE = re.compile(r"\b(" + DAYS + r")\b", re.I)
REVISED = re.compile(r"\b(" + DAYS + r") (?:is fine|works|instead|is better)\b", re.I)
DELIVERED = re.compile(
    r"\b(?:I (?:sent|delivered)|I['’]ve (?:sent|delivered)|attached is|here is|here['’]s) (.{2,180})",
    re.I,
)
PROMISE_REQUEST = re.compile(
    r"(?:please )?(?:(?:find|show me) what i promised|what did i promise) (.{1,70}?)"
    r"(?: about (.{1,80}?))?( and remind me tomorrow)?[.!?]*", re.I
)
BRIEF = re.compile(
    r"(?:hey talos[, ]+)?(?:what do i owe(?: people)?|(?:show|list) my commitments|give me my commitment brief)(?: right now| before tomorrow| by tomorrow| today)?[?.! ]*",
    re.I,
)


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS context_sources(
        id INTEGER PRIMARY KEY, path TEXT, kind TEXT, owner TEXT, topic TEXT,
        person TEXT, retain INTEGER, speak INTEGER, digest TEXT DEFAULT '', checked REAL DEFAULT 0,
        error TEXT DEFAULT '', selector TEXT DEFAULT '', UNIQUE(path,kind,person,topic))""")
    db.execute("""CREATE TABLE IF NOT EXISTS context_evidence(
        source INTEGER REFERENCES context_sources(id) ON DELETE CASCADE, seq INTEGER, stamp REAL,
        kind TEXT, detail TEXT, due TEXT, pointer TEXT, PRIMARY KEY(source,seq))""")
    db.execute("""CREATE TABLE IF NOT EXISTS context_links(
        revision_source INTEGER REFERENCES context_sources(id) ON DELETE CASCADE, revision_key TEXT,
        promise_source INTEGER REFERENCES context_sources(id) ON DELETE CASCADE, promise_key TEXT,
        PRIMARY KEY(revision_source,revision_key))""")
    db.execute(
        "CREATE TABLE IF NOT EXISTS personal_facts(session TEXT,subject TEXT,value TEXT,PRIMARY KEY(session,subject))"
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS coding_trials(id INTEGER PRIMARY KEY,stage TEXT,occurrences INTEGER,started REAL,status TEXT)"""
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS evidence_by_source ON context_evidence(source,kind)"""
    )


def select(db, path, kind, owner, topic, person, retain=True, speak=False, selector=""):
    path = Path(path).expanduser().resolve(strict=True)
    if (
        kind not in KINDS
        or not (path.is_file() or (kind == "document" and path.is_dir()))
        or not topic.strip()
        or not person.strip()
        or not owner.strip()
    ):
        raise ValueError("Choose a file, source type, owner, topic and contact.")
    with db:
        db.execute(
            """INSERT INTO context_sources(path,kind,owner,topic,person,retain,speak,selector)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path,kind,person,topic) DO UPDATE SET kind=excluded.kind, owner=excluded.owner,
            topic=excluded.topic, person=excluded.person, retain=excluded.retain, speak=excluded.speak,
            selector=excluded.selector,digest='',checked=0,error=''""",
            (
                str(path),
                kind,
                owner.strip(),
                topic.strip(),
                person.strip(),
                retain,
                speak,
                selector.strip(),
            ),
        )
        row = db.execute(
            "SELECT id FROM context_sources WHERE path=? AND kind=? AND person=? AND topic=?",
            (str(path), kind, person.strip(), topic.strip()),
        ).fetchone()
        db.execute(
            "DELETE FROM context_links WHERE revision_source=? OR promise_source=?",
            (row[0], row[0]),
        )
        db.execute("DELETE FROM context_evidence WHERE source=?", (row[0],))
    return row[0]


def messages(src, raw):
    kind, owner = src["kind"], src["owner"].casefold()
    if kind == "email":
        # A single EML has been individually selected. For an mbox require matching
        # participants and topic so unrelated threads never enter this project's graph.
        mailbox = raw.startswith(b"From ")
        chunks = re.split(rb"(?m)^From [^\n]*\n", raw)[1:] if mailbox else [raw]
        for i, chunk in enumerate(chunks, 1):
            msg = email.message_from_bytes(chunk, policy=policy.default)
            part = msg.get_body(preferencelist=("plain",))
            if not part or not msg.get("date"):
                if mailbox:
                    continue
                raise ValueError("Select a dated plain-text email.")
            senders = getaddresses(msg.get_all("from", []))
            outgoing = any(
                owner in (addr.casefold(), name.casefold()) for name, addr in senders
            )
            if mailbox:
                addresses = (
                    getaddresses(msg.get_all("to", []) + msg.get_all("cc", []))
                    if outgoing
                    else senders
                )
                if not any(
                    src["person"].casefold() in (name.casefold(), addr.casefold())
                    for name, addr in addresses
                ):
                    continue
                if (
                    src["topic"].casefold()
                    not in str(msg.get("subject", "")).casefold()
                ):
                    continue
            stamp = parsedate_to_datetime(str(msg["date"])).timestamp()
            text = re.split(
                r"(?im)^On .+ wrote:|^-+Original Message-+|^From:|^>",
                part.get_content(),
            )[0]
            yield stamp, outgoing, text, str(msg.get("Message-ID", f"message {i}"))
    elif kind == "imessage":
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if not {"date", "sender", "text"}.issubset(reader.fieldnames or ()):
            raise ValueError(
                "Use a date,sender,text CSV for the selected conversation."
            )
        for i, row in enumerate(reader, 2):
            if row["sender"].casefold() not in (
                owner,
                (src["selector"] or src["person"]).casefold(),
            ):
                continue
            yield (
                datetime.fromisoformat(row["date"]).timestamp(),
                row["sender"].casefold() == owner,
                row["text"],
                f"row {i}",
            )
    elif kind == "whatsapp":
        seen = False
        for i, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
            line = line.replace("\u200e", "").replace("\u202f", " ")
            m = re.match(
                r"^\[?(\d+[/.\-]\d+[/.\-]\d+),?\s+(\d+:\d+(?::\d+)?(?: [AP]M)?)\]?\s*(?:- )?([^:]+): (.+)$",
                line,
                re.I,
            )
            if not m:
                continue
            date, hour, who, body = m.groups()
            date = date.replace(".", "/").replace("-", "/")
            fmt = (
                "%Y/%m/%d"
                if len(date.split("/")[0]) == 4
                else ("%d/%m/%y" if len(date.split("/")[-1]) == 2 else "%d/%m/%Y")
            )
            clock = (
                ("%I:%M" if hour[-1:].upper() == "M" else "%H:%M")
                + (":%S" if hour.count(":") == 2 else "")
                + (" %p" if hour[-1:].upper() == "M" else "")
            )
            stamp = datetime.strptime(f"{date} {hour}", fmt + " " + clock).timestamp()
            seen = True
            if who.strip().casefold() not in (
                owner,
                (src["selector"] or src["person"]).casefold(),
            ):
                continue
            yield stamp, who.strip().casefold() == owner, body, f"line {i}"
        if not seen:
            raise ValueError("No supported WhatsApp messages in this export.")


def selected_chat(src):
    # A single selected chat, including outgoing messages. Attributed-body-only
    # messages require a supported export and are outside this text-only reader.
    uri = Path(src["path"]).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as chat:
        chat.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in chat.execute("PRAGMA table_info(message)")}
        exclude = "".join(f" AND coalesce(m.{name},0)=0" for name in ("date_retracted", "is_system_message")
                          if name in columns)
        rows = chat.execute(
            """SELECT m.date,m.is_from_me,m.text,m.guid FROM chat c
            JOIN chat_message_join cm ON cm.chat_id=c.rowid JOIN message m ON m.rowid=cm.message_id
            WHERE c.chat_identifier=? AND m.text IS NOT NULL""" + exclude + " ORDER BY m.date,m.rowid",
            (src["selector"] or src["person"],),
        )
        for stamp, outgoing, text, guid in rows:
            seconds = stamp / 1e9 if abs(stamp) > 1e12 else stamp
            yield 978307200 + seconds, bool(outgoing), text, str(guid)


def evidence(src, raw, mtime):
    if src["kind"] == "calendar":
        unfolded = re.sub(r"\r?\n[ \t]", "", raw.decode("utf-8-sig"))
        for i, event in enumerate(
            re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S), 1
        ):
            title = re.search(r"(?m)^SUMMARY:(.+)", event)
            start = re.search(r"(?m)^DTSTART(?:;[^:\n]+)?:(.+)", event)
            if title and start and src["topic"].casefold() in title[1].casefold():
                yield (
                    mtime,
                    "calendar",
                    title[1].strip() + " " + start[1].strip(),
                    "",
                    f"event {i}",
                )
        return
    if src["kind"] == "document":
        yield mtime, src["kind"], Path(src["path"]).name, "", "selected file"
        return
    for stamp, outgoing, body, pointer in (
        selected_chat(src) if raw is None else messages(src, raw)
    ):
        if outgoing and (m := PROMISE.search(body)):
            due = DATE.search(m[1])
            yield stamp, "promise", m[1].strip(), due[0] if due else "", pointer
        elif not outgoing and (m := REVISED.search(body)):
            yield stamp, "revision", m[0], m[1], pointer
        elif outgoing and (m := DELIVERED.search(body)):
            yield stamp, "delivery", m[0][:180], "", pointer


def refresh(db, src, now):
    path = Path(src["path"])
    digest, issue, rows = "", "", []
    try:
        if not src["retain"]:
            with db:
                db.execute(
                    "UPDATE context_sources SET checked=? WHERE id=? AND retain=0",
                    (now, src["id"]),
                )
            return
        if path.is_symlink() or not (
            path.is_file() or (src["kind"] == "document" and path.is_dir())
        ):
            raise ValueError("Selected source is no longer a regular file.")
        if src["kind"] == "imessage" and path.suffix.lower() in (".db", ".sqlite"):
            rows = list(evidence(src, None, 0))
            digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        elif src["kind"] == "document":
            if path.is_dir():
                rows = [
                    (p.stat().st_mtime, "document", p.name, "", p.name)
                    for p in path.iterdir()
                    if not p.is_symlink()
                    and not p.name.startswith(".")
                    and p.is_file()
                    and p.suffix.casefold()
                    in (
                        ".txt",
                        ".md",
                        ".csv",
                        ".xlsx",
                        ".docx",
                        ".pdf",
                        ".pages",
                        ".numbers",
                    )
                ]
                rows.sort()
                digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
            else:
                stamp = path.stat().st_mtime
                digest = str(stamp)
                rows = list(evidence(src, b"", stamp))
        else:
            with path.open("rb") as stream:
                raw = stream.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Select an export under 8 MB.")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != src["digest"]:
                rows = list(evidence(src, raw, path.stat().st_mtime))
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        sqlite3.Error,
    ) as exc:
        digest, issue = "", type(exc).__name__ + ": source unavailable or unsupported"
    with db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM context_sources WHERE id=?", (src["id"],)
        ).fetchone()
        if current is None or tuple(current) != tuple(src):
            return
        if digest != src["digest"] or issue:
            db.execute("DELETE FROM context_evidence WHERE source=?", (src["id"],))
            db.executemany(
                "INSERT INTO context_evidence VALUES(?,?,?,?,?,?,?)",
                ((src["id"], i, *r) for i, r in enumerate(rows, 1)),
            )
        db.execute(
            "UPDATE context_sources SET checked=?,digest=?,error=? WHERE id=?",
            (now, digest, issue, src["id"]),
        )


def coverage(db, now):
    counts = dict(db.execute("""SELECT CASE WHEN checked=0 THEN 'pending'
        WHEN error<>'' THEN 'unavailable' WHEN checked<? THEN 'due' ELSE 'current' END, count(*)
        FROM context_sources WHERE retain=1 GROUP BY 1""", (now - 300,)))
    return {key: counts.get(key, 0) for key in ("current", "due", "pending", "unavailable")}


def refresh_one(db, now):
    src = db.execute(
        "SELECT * FROM context_sources WHERE retain=1 AND checked<? ORDER BY checked,id LIMIT 1",
        (now - 300,),
    ).fetchone()
    if src:
        refresh(db, src, now)
    return bool(src)


def key(row):
    return hashlib.sha256(
        json.dumps([row["stamp"], row["kind"], row["detail"], row["pointer"]]).encode()
    ).hexdigest()


def resolve(db, revision_id, promise_id):
    def fetch(pair, kind):
        if (
            not isinstance(pair, list)
            or len(pair) != 3
            or not all(isinstance(i, str) for i in pair)
        ):
            raise ValueError("Select the dated statement and one promise.")
        row = db.execute(
            """SELECT s.topic,s.person,e.* FROM context_evidence e JOIN context_sources s
                         ON s.id=e.source WHERE e.source=? AND e.seq=? AND e.kind=?""",
            (*pair[:2], kind),
        ).fetchone()
        if not row or key(row) != pair[2]:
            raise ValueError("The evidence changed. Refresh the brief.")
        return row

    with db:
        db.execute("BEGIN IMMEDIATE")
        rev, promise = fetch(revision_id, "revision"), fetch(promise_id, "promise")
        if (rev["topic"], rev["person"]) != (
            promise["topic"],
            promise["person"],
        ) or promise["stamp"] > rev["stamp"]:
            raise ValueError(
                "These records do not describe the same contact, topic and time range."
            )
        db.execute(
            "INSERT OR REPLACE INTO context_links VALUES(?,?,?,?)",
            (rev["source"], key(rev), promise["source"], key(promise)),
        )


def due_date(value, stamp):
    if not value:
        return None
    if value[0].isdecimal():
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day = datetime.fromtimestamp(stamp).date()
    return day + timedelta(days=(weekdays.index(value.lower()) - day.weekday()) % 7)


def brief(db, spoken=False, cutoff=None):
    records = db.execute(
        """SELECT s.person,s.topic,s.path,s.speak,e.* FROM context_evidence e
        JOIN context_sources s ON s.id=e.source WHERE s.retain=1 AND (?=0 OR s.speak=1)
        ORDER BY e.stamp,e.source,e.seq""",
        (spoken,),
    ).fetchall()
    groups = {}
    for row in records:
        groups.setdefault((row["person"], row["topic"]), []).append(row)
    links = {
        (r[0], r[1]): (r[2], r[3]) for r in db.execute("SELECT * FROM context_links")
    }
    lines, sources, questions = [], [], []
    for (person, topic), rows in groups.items():
        promises = [r for r in rows if r["kind"] == "promise"]
        totals = {}
        for revision in (r for r in rows if r["kind"] == "revision"):
            totals[revision["stamp"]] = db.execute(
                """SELECT count(*) FROM context_evidence e JOIN context_sources s ON s.id=e.source
                WHERE e.kind='promise' AND s.person=? AND s.topic=? AND e.stamp<=?""",
                (person, topic, revision["stamp"]),
            ).fetchone()[0]
        for promise in promises:
            later = [
                r
                for r in rows
                if r["stamp"] >= promise["stamp"]
                and r["kind"] in ("delivery", "revision")
            ]
            revisions = [r for r in later if r["kind"] == "revision"]
            due = promise["due"]
            refs = [promise]
            for rev in revisions:
                # Include private promises when checking ambiguity, even for a spoken brief.
                total = totals[rev["stamp"]]
                chosen = links.get((rev["source"], key(rev)))
                if chosen == (promise["source"], key(promise)) or (
                    not chosen and total == 1
                ):
                    due = rev["due"]
                    refs.append(rev)
                elif not chosen and not questions and not spoken:
                    options = [
                        {
                            "id": [str(r["source"]), str(r["seq"]), key(r)],
                            "text": r["detail"],
                        }
                        for r in promises
                        if r["stamp"] <= rev["stamp"]
                    ]
                    questions.append(
                        {
                            "text": f"Which promise to {person} did “{rev['detail']}” change?",
                            "revision": [str(rev["source"]), str(rev["seq"]), key(rev)],
                            "options": options,
                        }
                    )
            deadline = due_date(due, refs[-1]["stamp"])
            if cutoff and deadline and deadline > cutoff:
                continue
            docs = [r for r in rows if r["kind"] in ("document", "calendar")]
            deliveries = [r for r in later if r["kind"] == "delivery"]
            line = f"{person} · {topic}: {promise['detail']}. Current deadline: {due or 'unspecified'}."
            for kind in ("document", "calendar"):
                matched = [r for r in docs if r["kind"] == kind]
                if matched:
                    line += (
                        " "
                        + ("Related file: " if kind == "document" else "Calendar: ")
                        + matched[-1]["detail"]
                        + "."
                    )
                    refs.append(matched[-1])
            if deliveries:
                line += " Possible delivery: “" + deliveries[-1]["detail"] + "”."
                refs.append(deliveries[-1])
            else:
                line += " No delivery found in the selected sources."
            lines.append(line)
            sources.extend(
                {
                    "title": f"{Path(r['path']).name} · {r['pointer']}",
                    "url": (
                        (Path(r["path"]) / r["detail"])
                        if r["kind"] == "document" and r["pointer"] == r["detail"]
                        else Path(r["path"])
                    ).as_uri(),
                }
                for r in refs
            )
    reply = " ".join(lines[:4] if spoken else lines) or (
        "No commitments from sources enabled for speech."
        if spoken
        else "No commitments found in the selected sources."
    )
    if questions and not spoken:
        reply += " " + questions[0]["text"]
    return {
        "reply": reply[:600] if spoken else reply,
        "sources": []
        if spoken
        else list({(s["url"], s["title"]): s for s in sources}.values()),
        "questions": questions,
    }


def main():
    from talos_core.memory.agent_memory import STATE_PATH, open_memory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=STATE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("select")
    add.add_argument("kind", choices=KINDS)
    add.add_argument("file", type=Path)
    for key in ("owner", "topic", "person"):
        add.add_argument("--" + key, required=True)
    add.add_argument("--selector", default="")
    add.add_argument("--speak", action="store_true")
    add.add_argument("--no-retain", action="store_true")
    sub.add_parser("sources")
    sub.add_parser("brief")
    sub.add_parser("refresh")
    sub.add_parser("forget").add_argument("id", type=int)
    args = parser.parse_args()
    with closing(open_memory(args.db)) as db:
        if args.command == "select":
            print(
                select(
                    db,
                    args.file,
                    args.kind,
                    args.owner,
                    args.topic,
                    args.person,
                    not args.no_retain,
                    args.speak,
                    args.selector,
                )
            )
        elif args.command == "forget":
            with db:
                db.execute("DELETE FROM context_sources WHERE id=?", (args.id,))
        elif args.command == "sources":
            for row in db.execute(
                "SELECT id,path,retain,speak,checked,error FROM context_sources ORDER BY id"
            ):
                print(dict(row))
        elif args.command == "brief":
            print(json.dumps(brief(db), indent=2, ensure_ascii=False))
        else:
            for row in db.execute("SELECT * FROM context_sources").fetchall():
                refresh(db, row, datetime.now(UTC).timestamp())


if __name__ == "__main__":
    main()

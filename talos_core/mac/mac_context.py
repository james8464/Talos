"""Private read-through search of enabled Mac apps. Receipts refer to original records."""

import argparse
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

APPS = ("Contacts", "Messages", "Mail", "WhatsApp")
INDEX_NAME = "personal-index-v2.sqlite3"
PREFIX = re.compile(
    r"^(?:search|look in) my (apps|messages|mail|contacts|whatsapp) for\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)
FAMILY = re.compile(
    r"^(?:what (?:do (?:my contacts|you) (?:say|know) about|is recorded about) my family|show my family relationships)[?.!]*$",
    re.IGNORECASE,
)


RELATION_QUERY = re.compile(
    r"who(?: is|[’']s) my (mother|mom|mum|father|dad|sister|brother|wife|husband|"
    r"spouse|partner|son|daughter|parent|child|sibling)[?.!]*", re.I
)
RELATION_ALIASES = {"mom": "mother", "mum": "mother", "dad": "father"}


def identifiers(value):
    """Match complete addresses or telephone numbers across app-specific formats."""
    emails = re.findall(r"[\w.+-]+@[\w.-]+", value.casefold())
    numbers = re.findall(r"(?<![\w])\+?\d[\d ().-]{5,}\d(?![\w])", value)
    return set(emails) | {re.sub(r"\D", "", number) for number in numbers}


def sources(library):
    book = library / "Application Support/AddressBook"
    return {
        "Contacts": [
            *book.glob("AddressBook-v*.abcddb"),
            *book.glob("Sources/*/AddressBook-v*.abcddb"),
        ],
        "Messages": [library / "Messages/chat.db"],
        "Mail": sorted(
            (library / "Mail").glob("V*/MailData/Envelope Index"),
            key=lambda p: (
                int(p.parents[1].name[1:]) if p.parents[1].name[1:].isdigit() else 0
            ),
            reverse=True,
        )[:1],
        "WhatsApp": [
            library
            / "Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
        ],
    }


def configure(state, apps):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not set(apps) <= set(APPS):
        raise ValueError("Unknown app source")
    with tempfile.NamedTemporaryFile(mode="w", dir=state, delete=False) as out:
        json.dump(list(dict.fromkeys(apps)), out)
    Path(out.name).replace(state / "mac-context.json")
    if (state / INDEX_NAME).exists():
        from talos_core.memory.personal_index import sync
        sync(state, Path.home() / "Library")


def enabled(state):
    try:
        return set(json.loads((state / "mac-context.json").read_text())) & set(APPS)
    except (OSError, ValueError, TypeError):
        return set()


def stream_rows(path, sql, parameters=()):
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        yield from db.execute(sql, parameters)


def read(path, sql):
    return list(stream_rows(path, sql))


def stream_messages(path, sql, parameters=(), *, timeout=0.5):
    """Apply the visibility flags available in this Messages database to rows aliased m."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        columns = {row[1].casefold() for row in db.execute("PRAGMA table_info(message)")}
        visible = ["m.text IS NOT NULL"]
        if "is_system_message" in columns:
            visible.append("m.is_system_message=0")
        if "date_retracted" in columns:
            visible.append("coalesce(m.date_retracted,0)=0")
        yield from db.execute(sql.format(visible=" AND ".join(visible)), parameters)


def read_messages(path, sql, parameters=(), *, timeout=0.5):
    return list(stream_messages(path, sql, parameters, timeout=timeout))


def contact_rows(path, limit=5000):
    return stream_rows(
        path,
        """SELECT r.Z_PK,trim(coalesce(r.ZFIRSTNAME,'')||' '||coalesce(r.ZLASTNAME,'')),
        coalesce(r.ZORGANIZATION,''),coalesce(r.ZNICKNAME,''),
        coalesce((SELECT group_concat(p.ZFULLNUMBER,';') FROM ZABCDPHONENUMBER p WHERE p.ZOWNER=r.Z_PK),''),
        coalesce((SELECT group_concat(e.ZADDRESS,';') FROM ZABCDEMAILADDRESS e WHERE e.ZOWNER=r.Z_PK),'')
        FROM ZABCDRECORD r WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL ORDER BY r.Z_PK LIMIT ?""", (limit,),
    )


def app_rows(app, path, limit=2000, text_limit=600):
    if app == "Messages":
        return stream_messages(
            path,
            """SELECT m.guid,coalesce(h.id,''),m.is_from_me,
            978307200+case when abs(m.date)>1000000000000 then m.date/1000000000 else m.date end,
            substr(m.text,1,coalesce(?,2147483647)) FROM message m LEFT JOIN handle h ON h.ROWID=m.handle_id
            WHERE {visible}
            ORDER BY m.date DESC LIMIT ?""", (text_limit, limit),
        )
    if app == "Mail":
        return stream_rows(
            path,
            """SELECT m.ROWID,coalesce(a.comment,'')||' '||coalesce(a.address,''),NULL,m.date_sent,
            substr(coalesce(s.subject,'')||' — '||coalesce(v.summary,''),1,coalesce(?,2147483647))
            FROM messages m LEFT JOIN addresses a ON a.ROWID=m.sender
            LEFT JOIN subjects s ON s.ROWID=m.subject LEFT JOIN summaries v ON v.ROWID=m.summary
            WHERE m.deleted=0 ORDER BY m.date_received DESC LIMIT ?""", (text_limit, limit),
        )
    return stream_rows(
        path,
        """SELECT m.Z_PK,coalesce(c.ZPARTNERNAME,'')||' '||coalesce(c.ZCONTACTJID,''),
        m.ZISFROMME,978307200+m.ZMESSAGEDATE,substr(m.ZTEXT,1,coalesce(?,2147483647))
        FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
        WHERE m.ZTEXT IS NOT NULL AND m.ZMESSAGETYPE=0 ORDER BY m.ZMESSAGEDATE DESC LIMIT ?""", (text_limit, limit),
    )


# A relationship phrase is meaningful only with an explicit Contacts Me-card label.
_FAMILY_PEER = re.compile(
    r"my (mother|father|mom|mum|dad|parent|sister|brother|sibling|spouse|partner|"
    r"wife|husband|daughter|son|child|grandmother|grandfather|cousin|aunt|uncle)", re.I
)


_FAMILY_MESSAGES = re.compile(
    r"(?:show(?: me)?|search|find) (?:my )?messages (from|with) " + _FAMILY_PEER.pattern + r"[.!?]*",
    re.I,
)


def family_message_lookup(role, selected, library, *, incoming_only=False):
    checked, unavailable = [], []

    def result(reply, matches=0):
        return {"reply": reply, "matches": matches, "private": True,
                "checked": checked, "unavailable": unavailable}

    if not {"Contacts", "Messages"} <= selected:
        return result("Enable Contacts and Messages to look up messages by family relationship.")
    paths = sources(library)
    books = paths["Contacts"]
    roles = {"mum": "mother", "mom": "mother", "dad": "father"}
    relation = roles.get(role, role)
    links = []
    try:
        if not books:
            raise OSError
        for book in books:
            for key, label, name in read(book, """
                    SELECT n.Z_PK,n.ZLABEL,n.ZNAME FROM ZABCDRELATEDNAME n
                    JOIN ZABCDRECORD r ON r.Z_PK=n.ZOWNER
                    WHERE r.ZCONTAINERWHERECONTACTISME IS NOT NULL AND n.ZNAME IS NOT NULL"""):
                label = (label or "").removeprefix("_$!<").removesuffix(">!$_").strip().casefold()
                if roles.get(label, label) == relation:
                    links.append((book, key, name.strip()))
        checked.append("Contacts")
        if len(links) != 1 or not links[0][2]:
            return result("I need one explicit matching relationship on the Contacts Me card.")
        source, link_id, name = links[0]
        cards = []
        for book in books:
            for key, first, last in read(book, "SELECT Z_PK,ZFIRSTNAME,ZLASTNAME FROM ZABCDRECORD"):
                full_name = " ".join(part.strip() for part in (first, last) if part and part.strip())
                if full_name.casefold() == name.casefold():
                    cards.append((book, key))
        if len(cards) != 1:
            return result("The name on the Contacts Me card needs to match exactly one contact.")
        book, key = cards[0]
        # A shared mailbox or family telephone cannot establish which person wrote.
        addresses, phones = set(), set()
        shared_addresses, shared_phones = set(), set()
        for directory in books:
            for owner, value in read(directory, "SELECT ZOWNER,ZADDRESS FROM ZABCDEMAILADDRESS"):
                if value and value.strip():
                    target = addresses if (directory, owner) == (book, key) else shared_addresses
                    target.add(value.strip().casefold())
            for owner, value in read(directory, "SELECT ZOWNER,ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                number = re.sub(r"\D", "", value or "")
                if number:
                    target = phones if (directory, owner) == (book, key) else shared_phones
                    target.add(number)
        addresses -= shared_addresses
        phones -= shared_phones
    except (sqlite3.Error, OSError):
        unavailable.append("Contacts")
        return result("Contacts is unavailable for this relationship lookup.")
    if not addresses and not phones:
        return result("The matching contact has no unique email address or phone number for Messages.")
    source_ref = source.relative_to(library / "Application Support/AddressBook")
    lines = [f"Contacts [{source_ref} · relationship {link_id}] {role}: {name}"]
    matches = 0
    try:
        for guid, peer, outgoing, stamp, body in app_rows("Messages", paths["Messages"][0]):
            if incoming_only and outgoing != 0:
                continue
            peer = peer.strip()
            if peer.casefold() not in addresses and not (
                re.fullmatch(r"[+0-9 ()\-.]+", peer) and re.sub(r"\D", "", peer) in phones
            ):
                continue
            matches += 1
            if matches <= 8:
                date = datetime.fromtimestamp(stamp or 0, UTC).date().isoformat()
                direction = "You" if outgoing == 1 else "Incoming"
                lines.append(f"Messages [{guid}] {date} · {direction} · {peer}: {body[:290]}")
        checked.append("Messages")
    except (sqlite3.Error, OSError, ValueError, OverflowError):
        unavailable.append("Messages")
        return result("Messages is unavailable for this relationship lookup.")
    if not matches:
        lines.append("No matching messages among the recent records searched.")
    return result("\n".join(lines), matches)


def lookup(query, apps, library, *, family=False, resolve_contacts=False, relation=None):
    terms = query.casefold().strip(" ?.").split()
    if not terms and not family:
        raise ValueError("Enter a name or search phrase.")
    paths = sources(library)
    contact_terms = {}
    hits = []
    relatives = set()
    unavailable = set()
    checked = set()
    capped = set()
    for app in APPS:
        if app not in apps and not (resolve_contacts and app == "Contacts"):
            continue
        for path in paths[app]:
            try:
                if app == "Contacts" and family:
                    rows = read(
                        path,
                        """SELECT r.ZFIRSTNAME,r.ZLASTNAME,n.ZLABEL,n.ZNAME,n.Z_PK
                        FROM ZABCDRELATEDNAME n JOIN ZABCDRECORD r ON r.Z_PK=n.ZOWNER
                        WHERE r.ZCONTAINERWHERECONTACTISME IS NOT NULL AND n.ZNAME IS NOT NULL""",
                    )
                    for first, last, label, name, key in rows:
                        label = re.sub(r"[^\w -]", "", label or "").strip("_")
                        if relation and RELATION_ALIASES.get(label.casefold(), label.casefold()) != relation:
                            continue
                        relatives.add(name.strip())
                        hits.append(
                            (
                                0,
                                app,
                                str(key),
                                f"{first or ''} {last or ''} · {label}: {name}",
                            )
                        )
                elif app == "Contacts":
                    for i, (key, name, org, nickname, phones, emails) in enumerate(contact_rows(path, 5001)):
                        if i == 5000:
                            capped.add(app)
                            break
                        fields = f"{name} {nickname} {org} {phones} {emails}".casefold()
                        matched = {term for term in terms if term in fields}
                        if not matched:
                            continue
                        if app in apps and matched.issuperset(terms):
                            hits.append((0, app, str(key), name + (" · " + org if org else "")))
                        for alias in identifiers(phones + ";" + emails):
                            contact_terms.setdefault(alias, []).append(matched)
                elif not family:
                    for i, (key, peer, outgoing, stamp, body) in enumerate(app_rows(app, path, 2001)):
                        if i == 2000:
                            capped.add(app)
                            break
                        fields = f"{peer} {body}".casefold()
                        missing = {term for term in terms if term not in fields}
                        if missing and not any(
                            matched.issuperset(missing)
                            for alias in identifiers(peer)
                            for matched in contact_terms.get(alias, ())
                        ):
                            continue
                        date = (
                            datetime.fromtimestamp(stamp or 0, UTC).date().isoformat()
                        )
                        direction = (
                            "You"
                            if outgoing == 1
                            else "Message"
                            if outgoing is None
                            else "Incoming"
                        )
                        hits.append(
                            (
                                stamp or 0,
                                app,
                                str(key),
                                f"{date} · {direction} · {peer.strip()}: {body[:290]}",
                            )
                        )
                checked.add(app)
            except (sqlite3.Error, OSError, ValueError, OverflowError):
                unavailable.add(app)
        if not paths[app]:
            unavailable.add(app)
    hits = sorted(set(hits), key=lambda h: (-h[0], h[1], h[2]))
    lines = [f"{app} [{key}] {body}" for _, app, key, body in hits[:8]]
    if not lines:
        lines = [
            f"No {relation} relationship found on the Contacts Me card."
            if relation else "No explicit family relationships found on the Contacts Me card."
            if family
            else "No matching records in the sources searched."
        ]
    if len(hits) > 8:
        lines.append(f"Showing 8 of {len(hits)} matching records.")
    if capped:
        lines.append("More records exist in " + ", ".join(sorted(capped)) +
                     ". Use ‘Search my memory for …’ to search the full enabled history.")
    if unavailable:
        lines.append("Unavailable sources: " + ", ".join(sorted(unavailable)) + ".")
    return {
        "reply": "\n".join(lines),
        "private": True,
        "matches": len(hits),
        **({"relatives": sorted(relatives)} if relation else {}),
        "checked": sorted(checked),
        "unavailable": sorted(unavailable),
    }


def outgoing_to(person, apps, library):
    """Resolve a full contact name before reading enabled direct conversations."""
    if "Contacts" not in apps:
        return
    paths = sources(library)
    identities = set()
    for path in paths["Contacts"]:
        try:
            for _, name, _, _, phones, emails in contact_rows(path, -1):
                if name.casefold() == person.strip().casefold():
                    identities.add(frozenset(identifiers(phones + ";" + emails)))
        except (OSError, sqlite3.Error):
            continue
    if len(identities) != 1:
        return
    aliases = identities.pop()
    if "Messages" in apps:
        for path in paths["Messages"]:
            try:
                rows = stream_rows(path, """SELECT m.guid,h.id,m.text FROM message m
                    JOIN chat_message_join cm ON cm.message_id=m.ROWID
                    JOIN chat_handle_join ch ON ch.chat_id=cm.chat_id
                    JOIN handle h ON h.ROWID=ch.handle_id
                    WHERE m.is_from_me=1 AND m.is_system_message=0 AND coalesce(m.date_retracted,0)=0
                    AND m.text IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM chat_handle_join other WHERE other.chat_id=ch.chat_id
                        AND other.handle_id<>ch.handle_id)
                    ORDER BY m.date DESC""")
                for key, peer, body in rows:
                    if not aliases.isdisjoint(identifiers(peer)):
                        yield body, f"Messages [{key}]", path
            except (OSError, sqlite3.Error):
                continue
    if "WhatsApp" in apps:
        for path in paths["WhatsApp"]:
            try:
                for key, peer, outgoing, _, body in app_rows("WhatsApp", path, -1):
                    jid = peer.rsplit(" ", 1)[-1]
                    number, _, domain = jid.partition("@")
                    if outgoing == 1 and body and domain == "s.whatsapp.net" and number in aliases:
                        yield body, f"WhatsApp [{key}]", path
            except (OSError, sqlite3.Error):
                continue


def room_answer(text, state, voice, library=None):
    feedback = re.fullmatch(r"(dismiss|restore) memory finding ([a-f0-9]{12}) for ([\w-]+)[?.!]?",
                            text.strip(), re.I)
    if feedback:
        if voice:
            return {"private": True, "reply": "Use the private message field to manage memory findings."}
        from talos_core.memory.memory_review import feedback as update_finding
        return {"private": True, "reply": update_finding(
            state, feedback[3], feedback[2].casefold(), restore=feedback[1].casefold() == "restore"
        )}

    review = re.fullmatch(r"show (?:my )?memory review for ([\w-]+)[?.!]?", text.strip(), re.I)
    if review:
        if voice:
            return {"private": True, "reply": "Use the private message field to search personal memory."}
        from talos_core.memory.memory_review import answer
        return {"private": True, "reply": answer(state, review[1])}

    connections = re.fullmatch(r"show (?:my )?memory connections for ([\w-]+)[?.!]?", text.strip(), re.I)
    if connections:
        if voice:
            return {"private": True, "reply": "Use the private message field to search personal memory."}
        from talos_core.memory.memory_connections import answer
        return {"private": True, "reply": answer(state, connections[1])}

    from talos_core.memory.knowledge_sources import APPS as KNOWLEDGE_APPS

    apps = {a.casefold(): a for a in (*APPS, "Files", *KNOWLEDGE_APPS)}
    names = "|".join(map(re.escape, apps))
    memory_query = re.fullmatch(rf"(?:search|look in) my (?:memory|(?P<app>{names}) memory) for\b(?P<query>.*)", text.strip(), re.I)
    if memory_query:
        if voice:
            return {"private": True, "reply": "Use the private message field to search personal memory."}
        from talos_core.memory.personal_index import search
        app = apps.get((memory_query["app"] or "").casefold())
        return search(state, library or Path.home() / "Library", memory_query["query"], app)
    match = PREFIX.fullmatch(text.strip())
    family = bool(FAMILY.fullmatch(text.strip()))
    family_messages = _FAMILY_MESSAGES.fullmatch(text.strip())
    if not match and not family and not family_messages:
        return None
    if voice:
        return {
            "reply": "Use the private message field to look up personal app records."
        }
    if match and not 1 <= len(match[2].strip(" ?.\t\n")) <= 160:
        return {
            "reply": "Enter a search phrase between 1 and 160 characters.",
            "private": True,
        }
    selected = enabled(state)
    if family_messages:
        return family_message_lookup(family_messages[2].casefold(), selected, library or Path.home() / "Library",
                                     incoming_only=family_messages[1].casefold() == "from")
    if match and match[1].casefold() == "messages":
        peer = _FAMILY_PEER.fullmatch(match[2].strip(" ?.").casefold())
        if peer:
            return family_message_lookup(peer[1], selected, library or Path.home() / "Library")
    resolve_contacts = "Contacts" in selected and not family
    if family:
        selected &= {"Contacts"}
    elif match[1].casefold() != "apps":
        selected &= {a for a in APPS if a.casefold() == match[1].casefold()}
    if not selected:
        return {"reply": "No matching Mac app sources are enabled.", "private": True}
    return lookup(
        "" if family else match[2],
        selected,
        library or Path.home() / "Library",
        family=family,
        resolve_contacts=resolve_contacts,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", nargs="*", choices=APPS, required=True)
    parser.add_argument(
        "--state", type=Path, default=Path(__file__).resolve().parents[2] / "state"
    )
    args = parser.parse_args()
    configure(args.state, args.enable)

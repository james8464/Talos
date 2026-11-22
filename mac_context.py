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
PREFIX = re.compile(
    r"^(?:search|look in) my (apps|messages|mail|contacts|whatsapp) for\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)
FAMILY = re.compile(
    r"^(?:what (?:do (?:my contacts|you) (?:say|know) about|is recorded about) my family|show my family relationships)[?.!]*$",
    re.IGNORECASE,
)


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


def enabled(state):
    try:
        return set(json.loads((state / "mac-context.json").read_text())) & set(APPS)
    except (OSError, ValueError, TypeError):
        return set()


def read(path, sql):
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(sql).fetchall()


def contact_rows(path):
    return read(
        path,
        """SELECT r.Z_PK,trim(coalesce(r.ZFIRSTNAME,'')||' '||coalesce(r.ZLASTNAME,'')),
        coalesce(r.ZORGANIZATION,''),coalesce(r.ZNICKNAME,''),
        coalesce((SELECT group_concat(p.ZFULLNUMBER,';') FROM ZABCDPHONENUMBER p WHERE p.ZOWNER=r.Z_PK),''),
        coalesce((SELECT group_concat(e.ZADDRESS,';') FROM ZABCDEMAILADDRESS e WHERE e.ZOWNER=r.Z_PK),'')
        FROM ZABCDRECORD r WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL ORDER BY r.Z_PK LIMIT 5000""",
    )


def app_rows(app, path):
    if app == "Messages":
        return read(
            path,
            """SELECT m.guid,coalesce(h.id,''),m.is_from_me,
            978307200+case when abs(m.date)>1000000000000 then m.date/1000000000 else m.date end,
            substr(m.text,1,600) FROM message m LEFT JOIN handle h ON h.ROWID=m.handle_id
            WHERE m.text IS NOT NULL AND m.is_system_message=0 AND coalesce(m.date_retracted,0)=0
            ORDER BY m.date DESC LIMIT 2000""",
        )
    if app == "Mail":
        return read(
            path,
            """SELECT m.ROWID,coalesce(a.comment,'')||' '||coalesce(a.address,''),NULL,m.date_sent,
            substr(coalesce(s.subject,'')||' — '||coalesce(v.summary,''),1,600)
            FROM messages m LEFT JOIN addresses a ON a.ROWID=m.sender
            LEFT JOIN subjects s ON s.ROWID=m.subject LEFT JOIN summaries v ON v.ROWID=m.summary
            WHERE m.deleted=0 ORDER BY m.date_received DESC LIMIT 2000""",
        )
    return read(
        path,
        """SELECT m.Z_PK,coalesce(c.ZPARTNERNAME,'')||' '||coalesce(c.ZCONTACTJID,''),
        m.ZISFROMME,978307200+m.ZMESSAGEDATE,substr(m.ZTEXT,1,600)
        FROM ZWAMESSAGE m JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
        WHERE m.ZTEXT IS NOT NULL AND m.ZMESSAGETYPE=0 ORDER BY m.ZMESSAGEDATE DESC LIMIT 2000""",
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


def lookup(query, apps, library, *, family=False, resolve_contacts=False):
    terms = query.lower().strip(" ?.").split()
    if not terms and not family:
        raise ValueError("Enter a name or search phrase.")
    paths = sources(library)
    aliases = set()
    hits = []
    unavailable = set()
    checked = set()
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
                        WHERE r.ZCONTAINERWHERECONTACTISME IS NOT NULL AND n.ZNAME IS NOT NULL LIMIT 100""",
                    )
                    for first, last, label, name, key in rows:
                        label = re.sub(r"[^\w -]", "", label or "").strip("_")
                        hits.append(
                            (
                                0,
                                app,
                                str(key),
                                f"{first or ''} {last or ''} · {label}: {name}",
                            )
                        )
                elif app == "Contacts":
                    for key, name, org, nickname, phones, emails in contact_rows(path):
                        if all(
                            t in f"{name} {nickname} {org} {phones} {emails}".casefold()
                            for t in terms
                        ):
                            if app in apps:
                                hits.append(
                                    (
                                        0,
                                        app,
                                        str(key),
                                        name + (" · " + org if org else ""),
                                    )
                                )
                            aliases.update(identifiers(phones + ";" + emails))
                elif not family:
                    for key, peer, outgoing, stamp, body in app_rows(app, path):
                        fields = f"{peer} {body}".casefold()
                        if not (
                            all(t in fields for t in terms)
                            or (aliases and not aliases.isdisjoint(identifiers(peer)))
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
            "No explicit family relationships found on the Contacts Me card."
            if family
            else "No matching records in the sources searched."
        ]
    if unavailable:
        lines.append("Unavailable sources: " + ", ".join(sorted(unavailable)) + ".")
    return {
        "reply": "\n".join(lines),
        "private": True,
        "matches": len(hits),
        "checked": sorted(checked),
        "unavailable": sorted(unavailable),
    }


def room_answer(text, state, voice, library=None):
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
        "--state", type=Path, default=Path(__file__).resolve().parent / "state"
    )
    args = parser.parse_args()
    configure(args.state, args.enable)

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
    if not match and not family:
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

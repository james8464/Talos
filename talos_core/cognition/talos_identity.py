import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class IdentityResolver:
    """
    Resolves fragmented user identity across multiple Mac sources.
    Maps 'Me' cards in Contacts to unique identifiers (emails, phones)
    and links these to other records in Messages, Mail, etc.
    """
    def __init__(self, state: Path):
        self.state = Path(state)
        self.user_profile = {
            "full_name": None,
            "emails": set(),
            "phones": set(),
            "address": None,
            "organization": None,
        }
        self._refresh_profile()

    def _refresh_profile(self):
        """Extract the Me-card identity from every Contacts database.

        Only an explicit Me card is trusted: falling back to an arbitrary contact
        would confidently report the wrong person.  Column sets differ between
        macOS versions, so each optional column is probed before use.
        """
        for path in self._get_contact_paths():
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    columns = {
                        row[1] for row in db.execute("PRAGMA table_info(ZABCDRECORD)")
                    }
                    candidates = []
                    for marker in ("ZME", "Z22_ME", "ZCONTAINERWHERECONTACTISME"):
                        if marker not in columns:
                            continue
                        row = db.execute(
                            f"SELECT Z_PK FROM ZABCDRECORD WHERE {marker} IS NOT NULL LIMIT 1"
                        ).fetchone()
                        if row and row[0] not in candidates:
                            candidates.append(row[0])

                    selected = None
                    for candidate in candidates:
                        row = db.execute(
                            """SELECT ZFIRSTNAME, ZLASTNAME, ZORGANIZATION
                               FROM ZABCDRECORD WHERE Z_PK=?""",
                            (candidate,),
                        ).fetchone()
                        if not row:
                            continue
                        first, last, org = row
                        if first or last or org:
                            selected = candidate
                            self.user_profile["full_name"] = (
                                f"{first or ''} {last or ''}".strip()
                            )
                            self.user_profile["organization"] = org or None
                            break
                        if selected is None:
                            selected = candidate
                    if selected is None:
                        continue

                    if "ZADDRESS" in columns:
                        row = db.execute(
                            "SELECT ZADDRESS FROM ZABCDRECORD WHERE Z_PK=?", (selected,)
                        ).fetchone()
                        if row and row[0]:
                            self.user_profile["address"] = row[0]

                    try:
                        emails = db.execute(
                            "SELECT ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZOWNER=?",
                            (selected,),
                        ).fetchall()
                        self.user_profile["emails"].update(e[0] for e in emails if e[0])
                    except sqlite3.Error:
                        pass
                    try:
                        phones = db.execute(
                            "SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER=?",
                            (selected,),
                        ).fetchall()
                        self.user_profile["phones"].update(p[0] for p in phones if p[0])
                    except sqlite3.Error:
                        pass
            except (sqlite3.Error, OSError):
                continue

    def _get_contact_paths(self) -> list[Path]:
        """Return every readable Contacts database, including per-account sources.

        Modern macOS keeps the Me card inside ``Sources/<UUID>/``; the top-level
        database is often empty.  Only Me-card matches are trusted, so scanning
        the sources cannot leak another contact into the profile.
        """
        library = Path.home() / "Library"
        book = library / "Application Support/AddressBook"
        if not book.exists():
            return []
        paths = list(book.glob("AddressBook-v*.abcddb"))
        paths.extend(book.glob("Sources/*/AddressBook-v*.abcddb"))
        seen, unique = set(), []
        for path in paths:
            if path.is_file() and path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    def resolve_identity(self, query: str) -> dict[str, Any]:
        """
        Returns the resolved identity context for the current user.
        Used to answer "Who am I?" or "Where do I live?".
        """
        # If the query is about the user's own identity
        if any(word in query.casefold() for word in ["who am i", "my name", "where do i live", "my address"]):
            profile = self.get_unified_profile()
            has_data = bool(profile["full_name"] or profile["emails"] or profile["phones"])

            if has_data:
                return {
                    "identity": profile,
                    "confidence": "high" if profile["full_name"] else "medium",
                    "source": "Contacts Database"
                }
            else:
                return {
                    "identity": {
                        "full_name": "User",
                        "emails": [],
                        "phones": [],
                        "address": None,
                        "organization": None,
                    },
                    "confidence": "low",
                    "source": "No personal data found in Contacts"
                }
        return {}

    def get_unified_profile(self) -> dict[str, Any]:
        return {
            "full_name": self.user_profile["full_name"],
            "emails": list(self.user_profile["emails"]),
            "phones": list(self.user_profile["phones"]),
            "address": self.user_profile["address"],
            "organization": self.user_profile["organization"],
        }

# Personality and Style Configuration
PERSONALITY = (
    "Talos - An autonomous Mac assistant with a dry, sardonic wit. You provide concise, "
    "actionable responses. You remember context across conversations and help with tasks proactively."
)

SPOKEN_STYLE = (
    "Speak in plain, conversational text with a professional yet friendly tone. "
    "Keep replies brief and do not use Markdown formatting."
)

def capability_context(mode: str = "text", remembers_voice: bool = False) -> str:
    """
    Returns capability descriptions for the given mode (text or voice).
    Used to inform the LLM about what actions are available.
    """
    base = {
        "text": (
            "You are a text-based assistant. You have no camera and no access to the microphone. "
            "This terminal cannot execute such actions itself. All responses should be in text format."
        ),
        "voice": (
            "You are a voice assistant. You have an active browser microphone. "
            "You have no camera access. Voice commands are transcribed to text for processing. "
        )
    }

    voice_extra = "Voice interactions are saved for later recall." if remembers_voice else "Voice interactions are temporary and not saved."

    if mode == "text":
        return base["text"]
    elif mode == "voice":
        return base["voice"] + voice_extra
    else:
        return base["text"]

# Singleton for the resolver
_resolver = None

def get_resolver(state: Path):
    global _resolver
    if _resolver is None:
        _resolver = IdentityResolver(state)
    return _resolver

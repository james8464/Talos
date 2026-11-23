import sqlite3
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from contextlib import closing
from agent_memory import open_memory

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
        """Extracts the 'Me' identity from the Contacts database."""
        paths = self._get_contact_paths()
        for path in paths:
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    # Try different methods to identify the Me card
                    me_candidates = []

                    # Method 1: Check for Me card with ZME column
                    me_pk = db.execute(
                        "SELECT Z_PK FROM ZABCDRECORD WHERE ZME IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if me_pk:
                        me_candidates.append(me_pk[0])

                    # Method 2: Check for Me card with Z22_ME column (newer format)
                    me_pk = db.execute(
                        "SELECT Z_PK FROM ZABCDRECORD WHERE Z22_ME IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if me_pk:
                        me_candidates.append(me_pk[0])

                    # Method 3: Check for Me card with container reference
                    me_pk = db.execute(
                        "SELECT Z_PK FROM ZABCDRECORD WHERE ZCONTAINERWHERECONTACTISME IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if me_pk:
                        me_candidates.append(me_pk[0])

                    # Use first candidate found
                    if me_candidates:
                        me_pk = me_candidates[0]

                        # Get contact details
                        row = db.execute(
                            """SELECT ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZADDRESS
                               FROM ZABCDRECORD WHERE Z_PK=?
                               LIMIT 1""",
                            (me_pk,)
                        ).fetchone()
                        if row:
                            first, last, org, addr = row
                            self.user_profile["full_name"] = f"{first or ''} {last or ''}".strip()
                            self.user_profile["organization"] = org
                            self.user_profile["address"] = addr
                    else:
                        # Fallback: Try to find any contact with name data
                        row = db.execute(
                            """SELECT ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZADDRESS
                               FROM ZABCDRECORD WHERE ZFIRSTNAME IS NOT NULL
                               ORDER BY Z_PK DESC LIMIT 1"""
                        ).fetchone()
                        if row:
                            first, last, org, addr = row
                            self.user_profile["full_name"] = f"{first or ''} {last or ''}".strip()
                            self.user_profile["organization"] = org
                            self.user_profile["address"] = addr
                            # Note: This is a fallback, not a real Me card

                    # Extract all linked emails and phones for the Me/card
                    if me_pk:
                        emails = db.execute(
                            "SELECT ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZOWNER=?",
                            (me_pk,)
                        ).fetchall()
                        self.user_profile["emails"].update([e[0] for e in emails if e[0]])

                        phones = db.execute(
                            "SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER=?",
                            (me_pk,)
                        ).fetchall()
                        self.user_profile["phones"].update([p[0] for p in phones if p[0]])
            except (sqlite3.Error, OSError):
                continue

    def _get_contact_paths(self) -> List[Path]:
        # Simplified logic mirroring mac_context.py
        library = Path.home() / "Library"
        book = library / "Application Support/AddressBook"
        if not book.exists(): return []
        return list(book.glob("AddressBook-v*.abcddb"))

    def resolve_identity(self, query: str) -> Dict[str, Any]:
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

    def get_unified_profile(self) -> Dict[str, Any]:
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

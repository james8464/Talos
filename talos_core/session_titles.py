"""Session titles adapted from Nous Research Hermes Agent. See LICENSE-Hermes."""

import re

MAX_TITLE_LENGTH = 100
_TITLE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_TITLE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TITLE_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]")


def sanitize_title(title: str | None) -> str | None:
    """The stored form of a title, or ``None`` when nothing printable remains.

    Lone surrogates become U+FFFD; ASCII controls (except tab, newline, return) and zero-width, bidi, object-replacement and interlinear code points are removed; whitespace runs collapse and the ends strip. A cleaned title longer than :data:`MAX_TITLE_LENGTH` raises ``ValueError``.
    """
    if not title:
        return None
    cleaned = _TITLE_INVISIBLE_RE.sub("", _TITLE_CONTROL_RE.sub("", _TITLE_SURROGATE_RE.sub("\ufffd", title)))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ValueError(f"Title too long ({len(cleaned)} chars, max {MAX_TITLE_LENGTH})")
    return cleaned


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS console_titles(session TEXT PRIMARY KEY,title TEXT UNIQUE NOT NULL)")


def display(db, session):
    row = db.execute("SELECT title FROM console_titles WHERE session=?", (session,)).fetchone()
    return row[0] if row else None


def set_title(db, session, raw):
    title = sanitize_title(raw)
    if not title:
        raise ValueError("A session title must contain printable text.")
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS console_sessions (name TEXT PRIMARY KEY)")
        if ((title == "text" and session != "text") or
                (f"console/{title}" != session and db.execute(
                    "SELECT 1 FROM console_sessions WHERE name=?", (title,)
                ).fetchone())):
            raise ValueError("That title is already a session ID.")
        existing = db.execute("SELECT session FROM console_titles WHERE title=?", (title,)).fetchone()
        if existing and existing[0] != session:
            raise ValueError("That title belongs to another session.")
        db.execute("INSERT INTO console_titles VALUES(?,?) ON CONFLICT(session) DO UPDATE SET title=excluded.title",
                   (session, title))
    return title

"""Explicit facts with session-scoped updates and immediate durable recall."""

import re

SUBJECT = r"([\w][\w ’'-]{0,60}?)"


def reply(db, text, session, *, remember, apply, follow_up):
    words = re.sub(
        r"^(?:(?:hey|okay) )?talos[, ]+", "", text.strip(), flags=re.I
    ).strip(" .!?")
    save = re.fullmatch(
        r"(remember(?: that)?|actually[, ]+)\s*my " + SUBJECT + r" is (.{1,240})",
        words,
        re.I,
    )
    query = re.fullmatch(r"(?:what|where) is my " + SUBJECT, words, re.I)
    forget = re.fullmatch(
        r"forget (?:where |what )?my " + SUBJECT + r"(?: is)?", words, re.I
    )
    if not (save or query or forget):
        return None
    key = " ".join((save[2] if save else (query or forget)[1]).casefold().split())
    row = (
        db.execute(
            "SELECT value FROM personal_facts WHERE session=? AND subject=?",
            (session, key),
        ).fetchone()
        if remember
        else None
    )
    if query:
        return f"You told me your {key} is {row[0]}." if row else None
    if save and save[1].lower().startswith("actually") and not row:
        return None
    if not remember:
        return "Turn on Remember voice transcripts to use durable facts for this room, or save this privately in the message field."
    if not apply or (
        follow_up and not (save and row and save[1].lower().startswith("actually"))
    ):
        return (
            "Please start a new addressed request to change saved memory."
            if apply
            else "Memory changes are off in shadow mode."
        )
    with db:
        if forget:
            db.execute(
                "DELETE FROM personal_facts WHERE session=? AND subject=?",
                (session, key),
            )
            return (
                "I removed that saved fact."
                if row
                else "I have no saved fact under that name."
            )
        db.execute(
            "INSERT INTO personal_facts VALUES(?,?,?) ON CONFLICT(session,subject) DO UPDATE SET value=excluded.value",
            (session, key, save[3]),
        )
    return f"I'll remember that your {key} is {save[3]}."

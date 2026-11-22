"""A single private, editable message draft in Talos's local room database."""

import re

_ADDRESS = r"(?:(?:hey )?talos[, ]+)?(?:(?:can|could|would) you )?(?:please )?"
_NEW = re.compile(_ADDRESS + r"draft (?:a |an )?(?:message|text|email)\b(.*)", re.I | re.S)
_CONTENT = re.compile(r"\s+(?:to|for) ([^\n:]{1,100}?)(?:\s+saying\s+|\s*:\s*)(.+)", re.I | re.S)
_MANAGE = re.compile(_ADDRESS + r"(show|review|discard|delete|send|change|update) my (?:message |text |email )?draft\b(.*)", re.I | re.S)


def draft_request(text):
    text = text.strip()
    if new := _NEW.fullmatch(text):
        content = _CONTENT.fullmatch(new[1])
        if content and content[1].strip() and content[2].strip():
            return "create", content[1].strip(), content[2].strip()
        return "help", "", ""
    if manage := _MANAGE.fullmatch(text):
        verb, rest = manage[1].casefold(), manage[2]
        if verb in ("change", "update"):
            body = re.fullmatch(r"\s+to\s+(.+)", rest, re.I | re.S)
            if body and body[1].strip():
                return "edit", "", body[1].strip()
        elif not rest.strip(" .!?"):
            return verb, "", ""
        return "help", "", ""
    return None


def handle(db, request, *, voice, direct, apply):
    operation, recipient, body = request

    def result(action, reply):
        return {"tool": "message_draft", "action": action, "reply": reply, "private": True}

    if not direct or (voice and operation != "create"):
        return result("blocked", "Use the private message field to review or change a draft.")
    if operation == "help":
        return result("help", "Try “Draft a message to Alex saying …” or “Change my message draft to …”.")
    if operation == "send":
        return result("blocked", "Review the draft in Talos and copy it into your messaging app to send.")
    if operation in ("show", "review"):
        row = db.execute("SELECT recipient, body FROM message_draft WHERE id=1").fetchone()
        if row:
            return result("review", f"Draft to {row[0]}\n{row[1]}")
    elif not apply:
        return result("blocked", "Draft changes are disabled in shadow mode.")
    else:
        with db:
            if operation == "create":
                saved = db.execute(
                    "INSERT INTO message_draft(id, recipient, body) VALUES (1, ?, ?) ON CONFLICT(id) DO NOTHING",
                    (recipient, body),
                )
                if saved.rowcount:
                    return result("drafted", "Draft saved in Talos. Type “Show my message draft” to review it.")
                return result("occupied", "A draft is already saved. Review or discard it in the private message field.")
            if operation == "edit":
                if db.execute("UPDATE message_draft SET body=? WHERE id=1", (body,)).rowcount:
                    return result("updated", "I updated the wording of your draft.")
            elif operation in ("discard", "delete") and db.execute("DELETE FROM message_draft WHERE id=1").rowcount:
                return result("discarded", "I discarded the saved draft.")
    return result("empty", "There is no saved message draft.")

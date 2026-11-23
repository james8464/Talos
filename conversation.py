"""Size limits for the in-memory conversation sent to the local model."""

import re

RECENT_MESSAGES = 12
MAX_MESSAGE_CHARS = 2_000
MAX_HISTORY_CHARS = 6_000


def limit_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the instructions and a short, user-led window of text messages."""
    instructions = messages[:1]
    recent = messages[max(1, len(messages) - RECENT_MESSAGES):]
    remaining = MAX_HISTORY_CHARS
    retained = []
    for message in reversed(recent):
        limit = min(remaining, MAX_MESSAGE_CHARS)
        if not limit:
            break
        content = message["content"][-limit:]
        retained.append({**message, "content": content})
        remaining -= len(content)
    while retained and retained[-1]["role"] != "user":
        retained.pop()
    return instructions + list(reversed(retained))


# OpenClaw src/shared/text/reasoning-tags.ts, leading scope for local model replies.
_REASON_TAG = re.compile(r"<\s*(/?)\s*(think|thinking|thought|antthinking|(?:antml|mm):(?:think|thinking|thought))\s*>", re.I)


def visible_model_text(content):
    if not isinstance(content, str):
        raise RuntimeError("The local model returned no text.")
    text = content.lstrip()
    while (opening := _REASON_TAG.match(text)) and not opening[1]:
        depth = 1
        for tag in _REASON_TAG.finditer(text, opening.end()):
            depth += -1 if tag[1] else 1
            if depth == 0:
                text = text[tag.end():].lstrip()
                break
        else:
            raise RuntimeError("The local model did not finish its visible answer.")
    # Some local templates consume the opening tag and leave just its closing boundary.
    # Keep literal examples inside Markdown code and structured JSON fields intact.
    if not text.startswith(("{", "[")):
        fence = ""
        for match in re.finditer(r"`+|<\s*/\s*(?:think|thinking|thought|antthinking|(?:antml|mm):(?:think|thinking|thought))\s*>", text, re.I):
            token = match.group()
            if token.startswith("`"):
                if not fence:
                    fence = token
                elif fence == token:
                    fence = ""
            elif not fence:
                text = text[match.end():].lstrip()
                break
    if not text.strip():
        raise RuntimeError("The local model returned no text.")
    return text

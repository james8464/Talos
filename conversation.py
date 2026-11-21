"""Size limits for the in-memory conversation sent to the local model."""

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

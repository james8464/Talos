"""Interpret quiet source-review replies using OpenClaw's heartbeat and silent-reply tokens."""

import json
import re

HEARTBEAT = "HEARTBEAT_OK"
MAX_ACK_CHARS = 300
TRAILING = re.compile(r"HEARTBEAT_OK([^\w]{0,4})$", re.ASCII)


def unwrapped(text):
    text = re.sub(r"<[^<>]*>", " ", text)
    text = re.sub("&nbsp;", " ", text, flags=re.I)
    return text.strip().strip("*`~_").strip()


def strip_edges(text):
    """Remove repeated edge markers, retaining punctuation following a trailing marker."""
    start = 0
    while text.startswith(HEARTBEAT, start):
        start += len(HEARTBEAT)
        while start < len(text) and text[start].isspace():
            start += 1
    end = len(text)
    changed = start > 0
    punctuation = []
    while end > start and text[end - 1].isspace():
        end -= 1
    while match := TRAILING.search(text, max(start, end - len(HEARTBEAT) - 4), end):
        punctuation.append(match[1].lstrip())
        end = match.start()
        while end > start and text[end - 1].isspace():
            end -= 1
        changed = True
    return text[start:end] + "".join(reversed(punctuation)), changed


def only_control(text):
    if not text.strip():
        return True
    text = unwrapped(text)
    if text.upper() == "NO_REPLY":
        return True
    rest, removed = strip_edges(text)
    return removed and re.fullmatch(r"[^\w]{0,4}", rest, re.ASCII) is not None


def review_content(raw):
    """Return substantive output for validation, or None for an explicit quiet review."""
    text = raw.strip()
    if not text:
        return raw  # An empty provider response does not verify a source excerpt.
    if text.startswith("{") and text.endswith("}"):
        return raw  # Most reviews are already structured. compare() validates them once.

    # Never let an acknowledgement outside a JSON proposal swallow a short proposal.
    # Decode the original bytes so any tags, tokens and indentation inside quotations survive.
    start = text.find("{")
    if start >= 0:
        try:
            proposal, end = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            return raw
        if (
            isinstance(proposal, dict)
            and only_control(text[:start])
            and only_control(text[end:])
        ):
            return text[start:end]
        return raw

    if only_control(text):
        return None
    if HEARTBEAT not in text:
        return raw
    rest, stripped = strip_edges(text)
    if not stripped:
        rest, stripped = strip_edges(unwrapped(text))
    if not stripped:
        return raw
    # OpenClaw counts UTF-16 units, including two for supplementary-plane characters.
    length = len(rest.encode("utf-16-le", errors="surrogatepass")) // 2
    return None if length <= MAX_ACK_CHARS else rest

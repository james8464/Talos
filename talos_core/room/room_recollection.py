"""Bounded, attributed recall from the active room conversation."""

import re
import unicodedata

from talos_core.memory.memory_ranking import tokens

# Hermes tools/session_search_tool.py::_scroll retrieves neighboring messages at a
# matching anchor. Talos searches only the turns supplied by this room session.
RECALL_MESSAGES = 48
_LAST_USER = re.compile(r"what did i just (?:say|ask|tell you)|repeat (?:what i just said|my last question)", re.I)
_LAST_REPLY = re.compile(r"(?:what did you just (?:say|ask)|(?:could you |can you )?(?:repeat|say) that(?: again)?|repeat your last (?:answer|question))", re.I)
_TOPIC = re.compile(
    r"(?:what did (i|you|we) (?:say|ask|mention|suggest|decide|discuss)(?: earlier)?"
    r"|(?:remind me |repeat )?what (i|you|we) (?:said|asked|mentioned|suggested|decided|discussed))"
    r" (?:about|regarding) (.+)", re.I)
_FILLER = {"a", "an", "the", "my", "your", "our", "of", "for", "in", "on", "and", "to", "earlier", "again"}


def _request(text):
    words = text.strip(" \t\n.!?").strip()
    if _LAST_USER.fullmatch(words):
        return "i", None
    if _LAST_REPLY.fullmatch(words):
        return "you", None
    match = _TOPIC.fullmatch(words)
    if match:
        return (match[1] or match[2]).lower(), [w for w in _words(match[3]) if w not in _FILLER]
    return None


def _words(text):
    return re.findall(r"\w+", unicodedata.normalize("NFC", text.casefold()))


def _matches(topic, *contents):
    # Dictation may produce "note book" in one utterance and "notebook" in the next.
    singles, pairs = set(), set()
    for content in contents:
        singles.update(tokens(content))
        words = _words(content)
        pairs.update(a + b for a, b in zip(words, words[1:]))
    reachable = {0}
    for i, word in enumerate(topic):
        if i not in reachable:
            continue
        if word in singles or word in pairs:
            reachable.add(i + 1)
        if i+1 < len(topic) and word + topic[i+1] in singles:
            reachable.add(i + 2)
    return bool(topic) and len(topic) in reachable


def _quote(whose, content, limit, topic=None):
    if len(content) > limit:
        start = 0
        if topic:
            # Keep the triggering words audible if they appeared late in long dictation.
            matches = re.finditer(r"\w+", content)
            anchor = next((m.start() for m in matches if m.group().casefold() in topic), 0)
            start = max(0, min(anchor - limit // 3, len(content) - limit + 1))
        end = min(len(content), start + limit - bool(start))
        if end < len(content):
            end -= 1
        content = ("…" if start else "") + content[start:end].strip() + ("…" if end < len(content) else "")
    return f'{whose} said, “{content}”'


def immediate_recall(text, history):
    request = _request(text)
    if request is None:
        return None
    speaker, topic = request
    window = history[-RECALL_MESSAGES:]
    for i in range(len(window) - 1, -1, -1):
        row = window[i]
        if row["role"] != "user" or ((topic is not None or speaker == "i") and _request(row["content"]) is not None):
            continue
        user = row["content"]
        following = window[i+1] if i+1 < len(window) else None
        reply = following["content"] if following and following["role"] == "assistant" else ""
        if speaker == "you" and not reply:
            continue
        # A short answer can omit the subject of the adjacent user's question.
        if topic is not None:
            evidence = (user,) if speaker == "i" else (user, reply)
            if not _matches(topic, *evidence):
                continue
        if speaker == "i" or not reply:
            return _quote("You", user, 385, topic)
        if speaker == "you":
            return _quote("I", reply, 385, topic)
        return _quote("You", user, 180, topic) + " " + _quote("I", reply, 190, topic)
    return ("I don’t have an earlier turn in this conversation." if topic is None else
            "I couldn’t find that topic in this conversation.")

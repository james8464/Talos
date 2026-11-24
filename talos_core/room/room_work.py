"""Explicit local work orders and briefings over the user's saved inbox."""

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from talos_core.memory.agent_memory import insert_decision
from talos_core.room.room_dialogue import decision, explicit_reminder, explicit_task

_NOTE = re.compile(r"(?:please )?(?:remember that|(?:save|make|take|keep) a note(?: that)?)[:, ]+(.+)", re.I)
_SCOPE = re.compile(r"for (?:the )?([^,]{1,70} (?:review|project|meeting|sync)),\s*", re.I)
# Split at the start of another instruction. Keep conjunctions inside item titles.
_BOUNDARY = re.compile(
    r"(?:[.!?]\s+|;\s*|,? +(?:and then|then) +)|(?:,? +and +|, +)(?=(?:please +)?"
    r"(?:add|put|create|remind|save|remember|make|take|keep|jot|nudge|notify|ping|email|send|delete|cancel|don't|do not)\b)", re.I)


def note_request(text):
    match = _NOTE.fullmatch(text.strip().rstrip(".!?"))
    if match and 0 < len(match[1].strip()) <= 400:
        return decision("save_note", match[1].strip())


def clauses(text):
    # Ignore command-like text inside a quoted title or note.
    masked = re.sub(r'"[^"]*(?:"|$)|“[^”]*(?:”|$)', lambda m: "_" * len(m[0]), text)
    parts = []
    start = 0
    for boundary in _BOUNDARY.finditer(masked):
        parts.append(text[start:boundary.start()].strip())
        start = boundary.end()
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def work_order(text):
    """Return all the explicit writes, or a clarification if a compound order is incomplete."""
    scope = _SCOPE.match(text)
    parts = clauses(text[scope.end():] if scope else text)
    if len(parts) < 2:
        return None
    if not (explicit_task(parts[0]) or note_request(parts[0]) or explicit_reminder(parts[0])):
        return None
    steps = []
    for part in parts:
        step = explicit_reminder(part) or explicit_task(part) or note_request(part)
        # In a compound request, "remind me in ten minutes" can refer to the preceding task.
        if steps and steps[-1]["tool"] == "create_task":
            previous = steps[-1]["detail"]
            match = re.fullmatch(r"(?:please )?(?:remind|nudge|notify|ping) me (?:about (?:it|that) )?in (.+?)[.!?]?", part, re.I)
            if match and (step is None or step["detail"].lower() in ("do it", "do that")):
                duration = re.split(r" to do (?:it|that)$", match[1], flags=re.I)[0]
                step = explicit_reminder(f"Remind me in {duration} to {previous}")
        if step and scope and scope[1].casefold() not in step["detail"].casefold():
            step["detail"] = f"{scope[1]} — {step['detail']}"
        if step is None or len(step["detail"]) > 400:
            return decision("ask", f"I haven't saved this request. Could you clarify the step “{part[:150]}”?")
        steps.append(step)
    return {"steps": steps}


def apply_order(db, steps, event, *, apply, archive, request_id):
    """Called inside the turn transaction. A retried order reuses its original receipts."""
    receipts = None
    if request_id is not None:
        if not isinstance(request_id, str):
            raise ValueError("request_id must be a UUID.")
        try:
            request_id = str(UUID(request_id))
        except ValueError as error:
            raise ValueError("request_id must be a UUID.") from error
        signature = sha256(json.dumps([event["kind"], steps, archive], sort_keys=True).encode()).hexdigest()
        old = db.execute("SELECT signature, receipts FROM room_batches WHERE request_id = ?", (request_id,)).fetchone()
        if old:
            if old["signature"] != signature:
                raise ValueError("This request ID already belongs to a different work order.")
            receipts = json.loads(old["receipts"])
    if receipts is None:
        receipts = []
        for step in steps:
            redacted = None if archive else {"disposition": "act", "tool": step["tool"]}
            item_id = insert_decision(db, event, step, apply=apply, receipt_proposal=redacted)
            if apply and not db.execute("SELECT 1 FROM inbox WHERE id = ?", (item_id,)).fetchone():
                raise RuntimeError("A local step was not applied.")
            receipts.append({"id": item_id, "tool": step["tool"], "action": "applied" if apply else "shadow"})
        if request_id is not None:
            db.execute("INSERT INTO room_batches VALUES (?, ?, ?)", (request_id, signature, json.dumps(receipts)))
        fresh = True
    else:
        fresh = False
    if receipts[0]["action"] == "shadow":
        reply = f"Previewed {len(steps)} steps. No items were saved."
    else:
        counts = []
        for tool, kind in (("save_note", "note"), ("create_task", "task"), ("set_reminder", "reminder")):
            count = sum(r["tool"] == tool for r in receipts)
            if count:
                counts.append(f"{count} {kind}" + ("s" if count != 1 else ""))
        reply = "Saved " + ", ".join(counts) + "."
        for step in steps:
            if step["tool"] == "set_reminder":
                minutes = step["remind_in_minutes"]
                due = (datetime.now(UTC) + timedelta(minutes=minutes)).astimezone()
                if minutes <= 60:
                    unit = "minute" if minutes == 1 else "minutes"
                    reply += f" Reminder set for {minutes} {unit} from now ({due:%a %d %b at %H:%M %Z})."
                else:
                    reply += f" Reminder set for {due:%a %d %b at %H:%M %Z}."
    return receipts, reply, fresh


def project_brief(db, text, private_context):
    match = re.fullmatch(r"(?:please )?(?:brief me on|(?:prepare|give me) (?:a |the )?brief(?:ing)? (?:on|for)) "
                         r"(.+?)[.!?]?", text.strip(), re.I)
    if not match:
        return None
    if not private_context:
        return decision("reply", "Type the briefing request in the message field to review saved items.")
    topic = match[1].strip()
    brief = saved_brief(db, topic)
    return decision("reply", brief["reply"])


def topic_terms(topic):
    generic = {"the", "a", "an", "of", "with", "on", "for", "and", "meeting", "weekly",
               "monthly", "daily", "sync", "review", "check", "in", "tomorrow", "call"}
    return list(dict.fromkeys(t for t in re.findall(r"[^\W_]+", topic.casefold()) if t not in generic))


def saved_brief(db, topic, *, exclude_topics=()):
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 160:
        raise ValueError("Use a briefing topic between 1 and 160 characters.")
    terms = topic_terms(topic)
    if not terms:
        return {"reply": "Give me a project or client name to find the relevant local records.", "items": [], "excerpts": []}
    query = " AND ".join(f'"{t}"' for t in terms)
    for other in exclude_topics:
        specific = set(topic_terms(other))
        if specific > set(terms):
            query += " NOT (" + " AND ".join(f'"{t}"' for t in sorted(specific)) + ")"
    rows = db.execute("""
        SELECT i.id, i.kind, i.text, i.due_at FROM inbox_search s JOIN inbox i ON i.id = s.rowid
        WHERE inbox_search MATCH ? AND i.acknowledged_at IS NULL
              AND i.kind IN ('task', 'note', 'reminder') ORDER BY i.id DESC LIMIT 9
    """, (query,)).fetchall()
    items = [{"id": r["id"], "kind": r["kind"], "text": r["text"],
              "due_at": r["due_at"] if r["kind"] == "reminder" else None} for r in rows[:8]]
    quotes = db.execute("""
        SELECT t.id, t.text, t.recorded_at FROM transcript_search s JOIN transcripts t ON t.id = s.rowid
        WHERE transcript_search MATCH ? AND t.role = 'user' AND t.session NOT LIKE 'visit/%' AND
              (lower(t.text) LIKE '%i will %' OR lower(t.text) LIKE '%i promised %' OR
               lower(t.text) LIKE '%i agreed %' OR lower(t.text) LIKE '%i''ll %' OR
               lower(t.text) LIKE '%i’ll %')
        ORDER BY t.id DESC LIMIT 4
    """, (query,)).fetchall()
    lines = [f"Local briefing for {topic}."]
    for item in items:
        label = {"note": "Note", "task": "Open task", "reminder": "Reminder"}[item["kind"]]
        lines.append(f"{label} #{item['id']}: {item['text']}")
    for quote in quotes[:3]:
        lines.append(f"Earlier words ({quote['recorded_at'][:10]}, transcript #{quote['id']}): {quote['text']}")
    if len(rows) > 8:
        lines.append("More saved items match. Showing the eight most recent.")
    if len(quotes) > 3:
        lines.append("More archived commitments match. Showing the three most recent.")
    if not items and not quotes:
        lines.append("I found no matching saved items or archived commitments.")
    return {"reply": "\n".join(lines), "items": items, "excerpts": [dict(q) for q in quotes[:3]]}


_PLAN_TOOLS = ("create_task", "save_note", "set_reminder")
_INTENT = {"create_task": r"\b(?:task|to[ -]?do|action item|my list)\b",
           "save_note": r"\b(?:note|remember|record|write down|jot down)\b",
           "set_reminder": r"\b(?:remind|nudge|notify|ping|alert) me\b"}


def reviewed_step(tool, source, detail, previous):
    if not re.search(_INTENT[tool], source, re.I):
        return None
    # Preserve exact wording from the request. The model can segment text, but it
    # cannot supply new names, amounts, or reminder times.
    exact = re.search(r"(?<!\w)" + re.escape(detail.strip()) + r"(?!\w)", source, re.I) if detail.strip() else None
    title = exact[0] if exact else source
    minutes = 0
    if tool == "save_note":
        parsed = note_request(source)
        title = parsed["detail"] if parsed else source
    elif tool == "create_task":
        parsed = explicit_task(source)
        if parsed:
            title = parsed["detail"]
    else:
        parsed = explicit_reminder(source)
        if parsed:
            return parsed
        delay = re.search(r"\bin ([a-z0-9 -]+? (?:minutes?|hours?|days?))\b", source, re.I)
        timing = explicit_reminder(f"Remind me in {delay[1]} to check") if delay else None
        if not timing:
            return None
        minutes = timing["remind_in_minutes"]
        if re.search(r"\b(?:about|to do) (?:it|that)\b", source, re.I):
            if not previous:
                return None
            title = previous["detail"]
        elif not exact or title.casefold() in delay[0].casefold():
            title = source
    return decision(tool, title, minutes) if len(title) <= 400 else None


def propose_work(generate, model, text):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 800:
        raise ValueError("Describe the work in 1 to 800 characters.")
    scope = _SCOPE.match(text)
    request = text[scope.end():] if scope else text
    parts = clauses(request)
    if len(parts) > 8:
        raise ValueError("Use up to eight instructions per plan.")
    step = {"type": "object", "additionalProperties": False, "properties": {
        "tool": {"type": "string", "enum": list(_PLAN_TOOLS)},
        "clause": {"type": "integer", "minimum": 1, "maximum": len(parts)},
        "detail": {"type": "string", "minLength": 1, "maxLength": 400},
    }, "required": ["tool", "clause", "detail"]}
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "steps": {"type": "array", "maxItems": len(parts), "items": step}}, "required": ["steps"]}
    prompt = ("Classify each numbered instruction as a local note, to-do, or timed reminder. "
              "Return its clause number and copy its item title verbatim into detail. "
              "Omit instructions that require other capabilities such as sending email. "
              "A task means the user asked for a to-do item. Do not invent missing information.")
    numbered = [{"clause": i, "instruction": part} for i, part in enumerate(parts, 1)]
    raw = generate([{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(numbered)}],
                   model=model, schema=schema, max_tokens=1200)
    try:
        candidates = json.loads(raw)["steps"]
        if not isinstance(candidates, list) or len(candidates) > len(parts):
            raise ValueError
        labels = {}
        for item in candidates:
            index, tool, detail = item["clause"], item["tool"], item["detail"]
            if (type(index) is not int or not 1 <= index <= len(parts) or index in labels or
                    tool not in _PLAN_TOOLS or not isinstance(detail, str)):
                raise ValueError
            labels[index] = (tool, detail)
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("The local model produced an unreadable work plan.") from error

    steps, remaining = [], []
    previous = None
    for i, part in enumerate(parts, 1):
        step = explicit_task(part) or explicit_reminder(part) or note_request(part)
        if not step:
            if i in labels:
                tool, detail = labels[i]
                step = reviewed_step(tool, part, detail, previous)
            elif re.search(_INTENT["set_reminder"], part, re.I):
                step = reviewed_step("set_reminder", part, "", previous)
        if step and scope and scope[1].casefold() not in step["detail"].casefold():
            step["detail"] = f"{scope[1]} — {step['detail']}"
        if step and len(step["detail"]) <= 400:
            steps.append(step)
            previous = step
        else:
            remaining.append(part)
            previous = None
    return steps, " / ".join(remaining)

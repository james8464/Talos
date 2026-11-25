"""Short, tool-aware conversational decisions for the native room client."""

import json
import math
import re
from datetime import datetime, time, timedelta
from decimal import Decimal, DecimalException
from operator import add, mul, sub, truediv

from talos_core.room.room_recollection import immediate_recall as immediate_recall
from talos_core.runtime.local_actions import LOCAL_WRITES, MAX_REMINDER_MINUTES

HEARD = "Yes, I can hear you."
VISIBLE = "I can see someone in the camera view."
EMPTY = "The camera isn't detecting anyone right now."
UNKNOWN = "I don't have a current camera reading."
IDENTITY = "I haven't confirmed who I'm speaking with."
TYPED = "That message arrived as text."
SENSOR_LINES = (HEARD, VISIBLE, EMPTY, UNKNOWN, IDENTITY, TYPED)


def sensor_answer(message, source, occupied):
    words = re.sub(r"[^\w ]+", " ", message.lower())
    words = " ".join(words.split())
    words = re.sub(r"^(?:(?:hey|ok|okay) )?(?:talos|tell us) ", "", words)
    words = re.sub(r" (?:talos|tell us)$", "", words)
    words = re.sub(r" (?:right )?now$", "", words)
    hearing = HEARD if source == "voice" else TYPED
    visibility = UNKNOWN if occupied is None else VISIBLE if occupied else EMPTY
    combined = re.fullmatch(r"(?:can|do) you (hear|see)(?: me)? and (?:can you |do you )?(hear|see) me", words)
    if combined and combined[1] != combined[2]:
        answers = {"hear": hearing, "see": visibility}
        return f"{answers[combined[1]]} {answers[combined[2]]}"
    if words in ("can you hear me", "did you hear me", "do you hear me",
                 "am i coming through", "can you hear my voice", "is my voice coming through"):
        return hearing
    if words in ("can you see me", "do you see me", "can you see anyone", "do you see anyone",
                 "is anyone in the room", "is there anyone in the room", "am i in the room"):
        return visibility
    if words in ("who am i", "do you recognize me", "do you recognise me",
                 "can you tell who i am", "do you know who is speaking"):
        return IDENTITY
    return None


_SMALL_NUMBERS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
]
_NUMBERS = {word: value for value, word in enumerate(_SMALL_NUMBERS)}
for tens, word in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2):
    _NUMBERS[word] = tens * 10
    for units in range(1, 10):
        _NUMBERS[f"{word} {_SMALL_NUMBERS[units]}"] = tens * 10 + units
_NUMBERS.update(a=1, an=1)


_REQUEST_PREFIX = r"(?:(?:hey[, ]+)?talos[, ]+)?(?:(?:could|can|would) you +)?(?:please +)?"
_DURATION = r"([a-z]+(?:[ -][a-z]+)?|[0-9]{1,5}) (minutes?|hours?|days?)"


def explicit_task(message):
    for pattern in (r"add (.+?) to my (?:to[ -]?do|task) list", r"put (.+?) on my (?:to[ -]?do|task) list",
                    r"(?:create|add) (?:a )?task (?:to |for )?(.+?)"):
        match = re.fullmatch(_REQUEST_PREFIX + pattern + r"[.!?]?", message.strip(), re.I)
        if match and 0 < len(match[1].strip()) <= 400:
            return decision("create_task", match[1].strip())
    return None


def reminder_subject(message):
    match = re.fullmatch(_REQUEST_PREFIX + r"remind me to (.+?)[.!?]?", message.strip(), re.I)
    if not match or len(match[1]) > 400:
        return None
    # A supplied calendar time needs the normal planner and its time lookup.
    if re.search(r"\b(?:at|by|on|tomorrow|today|tonight|next|after|before|every|this (?:evening|afternoon)|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                 match[1], re.I):
        return None
    return match[1]


def explicit_reminder(message):
    """Resolve an unambiguous duration locally. Leave other wording to the planner."""
    match = re.fullmatch(_REQUEST_PREFIX + r"remind me in " + _DURATION + r" to (.+?)[.!?]?",
                         message.strip(), re.I)
    if match is None:
        trailing = re.fullmatch(_REQUEST_PREFIX + r"remind me to (.+?) in " + _DURATION + r"[.!?]?",
                                message.strip(), re.I)
        if trailing is None:
            return None
        task, number, unit = trailing.groups()
    else:
        number, unit, task = match.groups()
    number = number.lower().replace("-", " ")
    amount = int(number) if number.isdecimal() else _NUMBERS.get(number, 0)
    minutes = amount * {"minute": 1, "hour": 60, "day": 1440}[unit.lower().rstrip("s")]
    if not 1 <= minutes <= MAX_REMINDER_MINUTES or len(task.strip()) > 400 or not task.strip():
        return None
    return decision("set_reminder", task.strip(), minutes)


_ABSOLUTE_DATE = r"(?P<date>today|tomorrow|tonight)"
_ABSOLUTE_TIME = r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<suffix>am|pm)?"


def _absolute_target(date_word, hour, minute, suffix, now):
    day = now.date() + (timedelta(days=1) if date_word == "tomorrow" else timedelta())
    if hour is None:
        hour = 20 if date_word == "tonight" else 9
        minute = 0
    hour, minute = int(hour), int(minute or 0)
    if suffix:
        if suffix.casefold() == "pm" and hour < 12:
            hour += 12
        elif suffix.casefold() == "am" and hour == 12:
            hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    target = datetime.combine(day, time(hour, minute), tzinfo=now.tzinfo)
    if target <= now and date_word != "tomorrow":
        target += timedelta(days=1)
    return target


def absolute_reminder(message, *, now=None):
    """Resolve a named clock time such as "tomorrow at 9" without a model."""
    text = message.strip()
    patterns = (
        (_REQUEST_PREFIX + r"remind me at " + _ABSOLUTE_TIME + r" " + _ABSOLUTE_DATE
         + r" to (?P<task>.+?)[.!?]?", "task"),
        (_REQUEST_PREFIX + r"remind me " + _ABSOLUTE_DATE
         + r"(?: at " + _ABSOLUTE_TIME + r")? to (?P<task>.+?)[.!?]?", "task"),
        (_REQUEST_PREFIX + r"remind me at " + _ABSOLUTE_TIME
         + r" to (?P<task>.+?)[.!?]?", "task"),
        (_REQUEST_PREFIX + r"remind me(?: to)? (?P<task>.+?) " + _ABSOLUTE_DATE
         + r"(?: at " + _ABSOLUTE_TIME + r")?[.!?]?", "task"),
        (_REQUEST_PREFIX + r"remind me(?: to)? (?P<task>.+?) at " + _ABSOLUTE_TIME + r"[.!?]?", "task"),
        (_REQUEST_PREFIX + r"set (?:a )?reminder for " + _ABSOLUTE_DATE
         + r"(?: at " + _ABSOLUTE_TIME + r")?[.!?]?", "date"),
    )
    for pattern, kind in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            continue
        data = match.groupdict()
        task = "Reminder" if kind == "date" else (data.get("task") or "")
        date_word = data.get("date")
        hour, minute, suffix = data.get("hour"), data.get("minute"), data.get("suffix")
        now = now or datetime.now().astimezone()
        target = _absolute_target(date_word, hour, minute, suffix, now)
        if target is None:
            return None
        minutes = math.ceil((target - now).total_seconds() / 60)
        task = task.strip(" .!?")
        if not 1 <= minutes <= MAX_REMINDER_MINUTES or not task or len(task) > 400:
            return None
        return decision("set_reminder", task, minutes)
    return None


def timer_request(message):
    """Resolve a simple countdown timer without the planner."""
    match = re.fullmatch(
        _REQUEST_PREFIX + r"(?:set |start )?(?:a )?timer(?: for)? " + _DURATION + r"[.!?]?",
        message.strip(), re.I,
    )
    if not match:
        return None
    number, unit = match.groups()
    number = number.lower().replace("-", " ")
    amount = int(number) if number.isdecimal() else _NUMBERS.get(number, 0)
    minutes = amount * {"minute": 1, "hour": 60, "day": 1440}[unit.lower().rstrip("s")]
    if not 1 <= minutes <= MAX_REMINDER_MINUTES:
        return None
    return decision("set_reminder", "Timer", minutes)


def decision(action, detail, minutes=0):
    disposition = "act" if action in LOCAL_WRITES else action
    return {"disposition": disposition, "tool": action if disposition == "act" else "none",
            "detail": detail, "goal": "Handle the room turn.", "reason": "Room dialogue decision.",
            "remind_in_minutes": minutes}


def conversational_correction(text):
    # Only a standalone correction can be acknowledged without asking the model.
    match = re.fullmatch(r"(?:(?:actually|no|sorry|wait)[, ]+)?i meant\s+([^.!?,;]+?)[.!]?", text.strip(), re.I)
    if not match or len(match[1]) > 120 or re.search(r"\b(?:and|so|but)\s+(?:can|could|would|should|why|how|tell|compare|explain)\b", match[1], re.I):
        return None
    return match[1].strip()



def corrected_recollection(text, history):
    """Answer retrospective questions from the speaker's words, without trusting earlier model replies."""
    if not re.match(r"^(?:what|which|where|when|who|how)\b", text.strip(), re.I) or not re.search(
        r"\b(?:did i|was i|had i|i (?:said|told you))\b", text, re.I
    ):
        return None
    statements = [m["content"] for m in history if m["role"] == "user"]
    for i in range(len(statements)-1, 0, -1):
        corrected = conversational_correction(statements[i])
        if not corrected:
            continue
        prev = statements[i-1]
        if len(prev) > 210 or "?" in prev:
            continue
        topics = set(re.findall(r"[^\W_]+", corrected.casefold())) - {"the","a","an","my","your","it","that","this"}
        question = set(re.findall(r"[^\W_]+", text.casefold()))
        if not topics & question:
            continue
        # Only substitute an unambiguous modifier of the same named thing. Other
        # corrections remain two attributed quotations with their original context.
        phrase = re.fullmatch(r"(?:the|a|an|my) ([^\W_]+) ([^\W_]+)", corrected, re.I)
        if phrase and re.match(r"^I ", prev):
            modifier, noun = phrase.groups()
            target = re.compile(r"\b(the|my|a|an) ([^\W_]+) (" + re.escape(noun) + r")\b", re.I)
            amended, replacements = target.subn(r"\g<1> " + modifier + r" \g<3>", prev, count=2)
            if replacements == 1:
                reported = re.sub(r"\bmy\b", "your", amended[2:].rstrip(".!?"), flags=re.I)
                if not re.search(r"\b(?:I|me|mine|myself|am|was)\b", reported, re.I):
                    return f"You said you {reported}."
        return f'You first said, “{prev.rstrip(".!?")},” then clarified that you meant {corrected}.'
    return None


def capability_answer(message, *, audio):
    """Answer capability questions from the room's real state, not the model."""
    words = re.sub(r"[^\w ]+", " ", (message or "").lower())
    words = " ".join(words.split())
    words = re.sub(r"^(?:(?:hey|ok|okay) )?(?:talos|tell us) ", "", words)
    audio_state = ("I can speak and play sound." if audio else
                   "Audio is muted, so I can't play sound or speak out loud.")
    if re.search(r"(?:what|which) (?:can|could) you (?:run|do) (?:in|on) the background\s*$", words) or \
            re.search(r"what (?:runs|works) in the background\s*$", words):
        return ("I can queue background work such as reminders and notes, and run "
                "bounded coding jobs in a sandbox. " + audio_state)
    if re.search(r"(?:can|could|do) you (?:play|make) (?:some )?(?:music|audio|sound|sounds|noise)\s*$", words) or \
            re.search(r"can you (?:talk|speak) (?:out loud|aloud)\s*$", words):
        return audio_state
    if re.search(r"(?:^| )(?:what can you do|what are you able to do|what are your capabilities|"
                 r"what can you help with|how can you help)\s*$", words):
        return ("I can answer questions, track reminders and notes, look things up, "
                "control Mac apps when asked, and verify small code changes in a sandbox. "
                + audio_state)
    return None


def spoken_problem(reply, text, prior):
    def words(value):
        return re.findall(r"\w+", value.casefold())
    spoken = words(reply)
    if any(len(phrase) >= 4 and spoken == phrase for phrase in (words(text), words(prior))):
        return "echo"
    if re.search(r"\b(?:I(?:['’]ve| have) been (?:looking|searching) for|I (?:went looking for|picked up|tried on|was wearing))\b", reply, re.I):
        return "claim"
    if re.search(r"\bI(?:['’]ve| have)? (?:just |already )?(?:sent|scheduled|booked|deleted|emailed|texted|"
                 r"(?:set|created|saved) (?:you |your |a |the )?(?:reminder|task|message|note))\b", reply, re.I):
        return "claim"
    return None


def spoken_reply(generate, messages, text, model):
    """Keep the model's scratch work and transcriber-style echoes out of playback."""
    schema = {"type": "object", "properties": {"talos_says": {"type": "string", "maxLength": 500}},
              "required": ["talos_says"], "additionalProperties": False}
    prior = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
    for attempt in range(2):
        raw = generate(messages, model=model, schema=schema, max_tokens=192)
        try:
            reply = json.loads(raw)["talos_says"].strip()
            if not 1 <= len(reply) <= 500:
                raise ValueError
            problem = spoken_problem(reply, text, prior)
        except (ValueError, TypeError, KeyError, AttributeError):
            problem = "format"
        if not problem:
            return reply
        if attempt == 0:
            guidance = {"format": "Return JSON containing only talos_says with one short spoken reply.",
                        "claim": "Answer using what the speaker reported. Only claim actions with a receipt.",
                        "echo": "Respond to the speaker's specific detail in your own words."}[problem]
            messages = with_spoken_instructions(messages, guidance)
    return "I couldn't put that answer into words. Could you rephrase it?"


def with_spoken_instructions(messages, guidance):
    """Add model guidance while preserving role order and the current request."""
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": messages[0]["content"] + " " + guidance}, *messages[1:]]
    return [{"role": "system", "content": guidance}, *messages]


def compact_decision(generate, messages, model, lookups, *, can_search=False, read_only=False,
                     request=""):
    if request:
        folded = request.casefold()
        arithmetic = bool(re.search(
            r"\d|[+\-*/×÷]|\b(?:calculate|plus|minus|times|divided|sum|total|come to|how much|how many)\b",
            folded,
        ))
        wants_write = bool(re.search(
            r"\b(?:remind|reminder|task|todo|note|remember|save|log|alert|add|create|delete|forget)\b",
            folded,
        ))
        choices = ["reply"]
        if not read_only:
            choices.append("ignore")
        if arithmetic:
            choices.append("calculate")
        if not read_only:
            if wants_write:
                choices += LOCAL_WRITES
            choices += lookups
    else:
        choices = ["reply", "calculate"] if read_only else ["reply", "ignore", "calculate", *LOCAL_WRITES, *lookups]
    if can_search:
        choices.append("search_web")
    schema = {"type": "object", "properties": {
        "action": {"type": "string", "enum": choices},
        "text": {"type": "string", "maxLength": 400},
        "minutes": {"type": "integer", "minimum": 1, "maximum": MAX_REMINDER_MINUTES},
    }, "required": ["action", "text"], "additionalProperties": False}
    if read_only:
        del schema["properties"]["minutes"]
    try:
        obj = json.loads(generate(messages, model=model, schema=schema, max_tokens=180))
        action, detail = obj["action"], obj["text"]
        minutes = obj.get("minutes", 0)
        valid_minutes = (type(minutes) is int and
                         (1 <= minutes <= MAX_REMINDER_MINUTES if action == "set_reminder" else minutes == 0))
        if (action not in choices or not isinstance(detail, str) or len(detail) > 400
                or (action != "ignore" and not detail.strip()) or not valid_minutes):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        messages = with_spoken_instructions(
            messages,
            "Your previous reply was rejected. Return exactly one JSON object with action and text. "
            "Use calculate only for a numeric arithmetic expression.",
        )
        try:
            obj = json.loads(generate(messages, model=model, schema=schema, max_tokens=180))
            action, detail = obj["action"], obj["text"]
            minutes = obj.get("minutes", 0)
            valid_minutes = (type(minutes) is int and
                             (1 <= minutes <= MAX_REMINDER_MINUTES if action == "set_reminder" else minutes == 0))
            if (action not in choices or not isinstance(detail, str) or len(detail) > 400
                    or (action != "ignore" and not detail.strip()) or not valid_minutes):
                raise ValueError
        except (KeyError, ValueError, TypeError) as retry_error:
            raise RuntimeError("Talos returned an invalid room response.") from retry_error
    if action == "calculate":
        operand = r"(-?[0-9]{1,12}(?:\.[0-9]{1,8})?)"
        if re.fullmatch(operand, detail.strip()):
            return decision("reply", detail.strip() + ".")
        formula = re.fullmatch(operand + r"\s*([+*/×÷-])\s*" + operand, detail.strip())
        if formula:
            left, symbol, right = formula.groups()
            operation = {"+": add, "-": sub, "*": mul, "×": mul, "/": truediv, "÷": truediv}[symbol]
            try:
                value = operation(Decimal(left), Decimal(right))
                spoken = {"+": "plus", "-": "minus", "*": "times", "×": "times",
                          "/": "divided by", "÷": "divided by"}[symbol]
                return decision("reply", f"{left} {spoken} {right} is {value:f}.")
            except DecimalException:
                pass
        return decision("reply", "I couldn't calculate that expression.")
    if action == "search_web":
        return decision("research", "")
    if action in lookups:
        return {**decision("look_up", detail.strip()), "tool": action}
    return decision(action, "" if action == "ignore" else detail.strip(), minutes)

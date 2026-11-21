"""Short, tool-aware conversational decisions for the native room client."""

import json
import re
from decimal import Decimal, DecimalException
from operator import add, mul, sub, truediv

from local_actions import LOCAL_WRITES, MAX_REMINDER_MINUTES

HEARD = "Yes, I can hear you."
VISIBLE = "I can see someone in the camera view."
EMPTY = "The camera isn't detecting anyone right now."
UNKNOWN = "I don't have a current camera reading."
IDENTITY = "I can't identify people by face or voice."
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
    if words in ("can you hear me", "did you hear me", "do you hear me"):
        return hearing
    if words in ("can you see me", "do you see me", "can you see anyone", "do you see anyone",
                 "is anyone in the room", "is there anyone in the room", "am i in the room"):
        return visibility
    if words in ("who am i", "do you recognize me", "do you recognise me"):
        return IDENTITY
    return None


_SMALL_NUMBERS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                  "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_NUMBERS = {word: value for value, word in enumerate(_SMALL_NUMBERS)}
for tens, word in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2):
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


def decision(action, detail, minutes=0):
    disposition = "act" if action in LOCAL_WRITES else action
    return {"disposition": disposition, "tool": action if disposition == "act" else "none",
            "detail": detail, "goal": "Handle the room turn.", "reason": "Room dialogue decision.",
            "remind_in_minutes": minutes}


def compact_decision(generate, messages, model, lookups, *, can_search=False):
    choices = ["reply", "ignore", "calculate", *LOCAL_WRITES, *lookups]
    if can_search:
        choices.append("search_web")
    schema = {"type": "object", "properties": {
        "action": {"type": "string", "enum": choices},
        "text": {"type": "string", "maxLength": 400},
        "minutes": {"type": "integer", "minimum": 1, "maximum": MAX_REMINDER_MINUTES},
    }, "required": ["action", "text"], "additionalProperties": False}
    try:
        obj = json.loads(generate(messages, model=model, schema=schema, max_tokens=180))
        action, detail = obj["action"], obj["text"]
        minutes = obj.get("minutes", 0)
        valid_minutes = (type(minutes) is int and
                         (1 <= minutes <= MAX_REMINDER_MINUTES if action == "set_reminder" else minutes == 0))
        if (action not in choices or not isinstance(detail, str) or len(detail) > 400
                or (action != "ignore" and not detail.strip()) or not valid_minutes):
            raise ValueError
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("Talos returned an invalid room response.") from error
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

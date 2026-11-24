"""Answer unambiguous spoken calculations locally, with scoped follow-up quantities."""

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from operator import add, mul, sub, truediv

from room_dialogue import _NUMBERS


# Factors are exact relative to a metre, second or gram.
UNITS = {}
for dimension, rows in {
    "length": (("meter", 1, "metre"), ("kilometer", 1000, "kilometre"), ("centimeter", ".01", "centimetre"),
               ("foot", ".3048", "feet"), ("inch", ".0254", "inches"), ("mile", "1609.344", "")),
    "time": (("second", 1, ""), ("minute", 60, ""), ("hour", 3600, ""), ("day", 86400, "")),
    "mass": (("gram", 1, ""), ("kilogram", 1000, ""), ("pound", "453.59237", "")),
}.items():
    for name, factor, alias in rows:
        plural = {"foot": "feet", "inch": "inches"}.get(name, name + "s")
        for word in {name, plural, alias, alias + "s" if alias.endswith("metre") else ""} - {""}:
            UNITS[word] = (dimension, Decimal(factor), name, plural)

OPERATIONS = {"plus": add, "minus": sub, "times": mul, "divided by": truediv}
SYMBOLS = {"+": "plus", "-": "minus", "×": "times", "*": "times", "/": "divided by", "÷": "divided by"}
CONTEXT = ("it", "that", "the result")
MISSING_NUMBER = "Which number should I use?"
MISSING_UNIT = "Which quantity should I convert?"


def number(text):
    if re.fullmatch(r"-?\d{1,12}(?:\.\d{1,8})?", text):
        return Decimal(text)
    return Decimal(_NUMBERS[text]) if text in _NUMBERS and text not in ("a", "an") else None


def display(value):
    # Eight significant digits in spoken approximations; retain exact integers.
    if value == value.to_integral_value():
        return str(int(value))
    with localcontext() as ctx:
        ctx.prec = 8
        rounded = +value
    digits = format(rounded, "f")
    digits = digits.rstrip("0").rstrip(".") if "." in digits else digits
    return ("about " if value != rounded else "") + digits


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str = ""

    def label(self):
        suffix = ""
        if self.unit:
            unit = UNITS[self.unit]
            suffix = " " + (unit[2] if abs(self.value) == 1 else unit[3])
        return display(self.value) + suffix


def evaluate(text, prior):
    text = re.sub(r"^(?:(?:hey[, ]+)?talos[, ]+)?(?:please )?", "", text.lower().strip()).rstrip(" .!?")
    text = re.sub(r"^(?:and |then )?", "", text)
    text = re.sub(r"^(?:what(?: is|'s|’s) |how much is |calculate |convert )", "", text)
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    text = re.sub(r"(?<=\d)\s*%\s+of\b", " percent of", text)
    text = re.sub(r"(?<=\d)\s*([+×*/÷-])\s*(?=-?\d)",
                  lambda m: " " + SYMBOLS[m[1]] + " ", text)
    if not text or len(text) > 130:
        return None
    conversion = re.fullmatch(r"(?:(.+?) )?(?:in|to) ([a-z]+)", text)
    if conversion and (target := UNITS.get(conversion[2])):
        source = conversion[1] or "that"
        if source in CONTEXT:
            if prior is None or not prior.unit:
                return MISSING_UNIT, None
            initial = prior
        else:
            size, _, unit = source.rpartition(" ")
            value = number(size)
            if value is None or unit not in UNITS:
                return None
            initial = Quantity(value, unit)
        original = UNITS[initial.unit]
        if original[0] != target[0]:
            return f"{original[3].capitalize()} measure {original[0]} and {target[3]} measure {target[0]}.", None
        result = Quantity(initial.value * original[1] / target[1], target[2])
        return f"{initial.label()} equals {result.label()}.", result
    split = re.fullmatch(r"split (.+?) (?:between|by|among) (.+?)(?: people| ways)?", text)
    if split:
        left, symbol, right = split[1], "divided by", split[2]
    else:
        binary = re.fullmatch(r"(.+?) (plus|minus|times|multiplied by|divided by|percent of|per cent of) (.+)", text)
        if not binary:
            return None
        left, symbol, right = binary.groups()
    divisor = number(right)
    if divisor is None:
        return None
    if left in CONTEXT:
        if prior is None:
            return MISSING_NUMBER, None
        initial = prior
    else:
        value = number(left)
        if value is None:
            return None
        initial = Quantity(value)
    if symbol == "divided by" and not divisor:
        return "Division by zero has no defined result.", None
    if symbol in ("percent of", "per cent of"):
        if initial.unit:
            return None
        value = initial.value * divisor / 100
    else:
        operation = OPERATIONS["times" if symbol == "multiplied by" else symbol]
        value = operation(initial.value, divisor)
    result = Quantity(value, initial.unit)
    return f"{initial.label()} {symbol} {display(divisor)} is {result.label()}.", result


def quantity_answer(text, history):
    """Reuse only an immediately preceding result produced by this same evaluator."""
    result = evaluate(text, None)
    if result is None or result[0] not in (MISSING_NUMBER, MISSING_UNIT):
        return result[0] if result else None
    prior = None
    # Walk complete turns in chronological order. A different subject breaks the chain.
    recent = history[-12:]
    for user, assistant in zip(recent, recent[1:]):
        if user.get("role") == "user" and assistant.get("role") == "assistant":
            recovered = evaluate(user["content"], prior)
            prior = recovered[1] if recovered and recovered[0] == assistant["content"] else None
    follow_up = evaluate(text, prior)
    return follow_up[0] if follow_up else None

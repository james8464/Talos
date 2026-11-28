#!/usr/bin/env python3
"""Held-out localization coverage: find UI string literals missing French.

Unlike localization_check.py (which validates catalog entries that exist), this
scans SwiftUI call sites for user-visible literals and asserts each has a
French entry. It catches surfaces the live traversal may not reach, such as
panels, menus, sheets, and error states.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "apps" / "macos" / "Talos"
CATALOG = ROOT / "Localizable.xcstrings"

# Constructors whose first string literal is user-visible.
PATTERN = re.compile(
    r'\b(?:Text|Button|Toggle|Picker|Section|LabeledContent|Label|TextField|'
    r'Stepper|Menu|Link|navigationTitle|help|accessibilityLabel|'
    r'accessibilityHint|confirmationDialog|alert)\s*\(\s*"((?:[^"\\]|\\.)*)"'
)
# Conditional and fallback titles bypass LocalizedStringKey when the ternary or
# nil-coalescing expression evaluates to String.
CONDITIONAL = re.compile(
    r'\b(Text|Button|Label|Toggle|Picker|Section|LabeledContent|Menu|Link|'
    r'accessibilityLabel|help)\s*\(\s*(?!LocalizedStringKey\b)[^()\n]*\?\s*'
    r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"'
)
FALLBACK = re.compile(
    r'\b(Text|Button|Label|Toggle|Picker|Section|LabeledContent|Menu|Link|'
    r'accessibilityLabel|help)\s*\(\s*(?!LocalizedStringKey\b)[^)\n]*\?\?\s*'
    r'"((?:[^"\\]|\\.)*)"'
)
LOCALIZED = re.compile(
    r'(?:String\(localized:|LocalizedStringKey\()\s*"((?:[^"\\]|\\.)*)"'
)
LOCALIZED_CONDITIONAL = re.compile(
    r'LocalizedStringKey\(\s*[^()\n]*\?\s*"((?:[^"\\]|\\.)*)"\s*:\s*'
    r'"((?:[^"\\]|\\.)*)"'
)
LOCALIZED_TERNARY = re.compile(
    r'\?\s*String\(localized:\s*"((?:[^"\\]|\\.)*)"\s*\)\s*:\s*'
    r'String\(localized:\s*"((?:[^"\\]|\\.)*)"\s*\)'
)
BRAND = re.compile(r'^(?:Talos(?: \d[\d.]*)?|MCP|Git|GitHub|Ollama|OpenAI|Anthropic|Gemini|'
                   r'OpenCode Go|macOS|iOS|API|HTTP|HTTPS|URL|SSE|JSON|JSON-RPC|'
                   r'Ed25519|SHA-256|base64|PDF|esc|Chrome|Safari|Finder|Keychain)$')
HAS_WORD = re.compile(r'[A-Za-z]{3,}')

catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["strings"]


def has_french(key: str) -> bool:
    entry = catalog.get(key, {})
    value = entry.get("localizations", {}).get("fr", {}).get("stringUnit", {}).get("value")
    return bool(value)


missing = []
seen = set()
for path in sorted(ROOT.rglob("*.swift")):
    if path.name in {"Localizable.xcstrings"}:
        continue
    text = path.read_text(encoding="utf-8")
    literals: list[tuple[str, int]] = []
    for match in PATTERN.finditer(text):
        literals.append((match.group(1), match.start()))
    for match in CONDITIONAL.finditer(text):
        literals.append((match.group(2), match.start()))
        literals.append((match.group(3), match.start()))
    for match in FALLBACK.finditer(text):
        literals.append((match.group(2), match.start()))
    for match in LOCALIZED.finditer(text):
        literals.append((match.group(1), match.start()))
    for match in LOCALIZED_CONDITIONAL.finditer(text):
        literals.append((match.group(1), match.start()))
        literals.append((match.group(2), match.start()))
    for match in LOCALIZED_TERNARY.finditer(text):
        literals.append((match.group(1), match.start()))
        literals.append((match.group(2), match.start()))
    for literal, offset in literals:
        if "\\(" in literal:
            continue
        # Swift applies escape sequences before the string becomes a lookup key.
        literal = (literal.replace("\\n", "\n").replace("\\t", "\t")
                   .replace('\\"', '"').replace("\\\\", "\\"))
        # Skip brand names, key caps, and non-prose tokens.
        if not HAS_WORD.search(literal) or BRAND.match(literal):
            continue
        if literal in seen:
            continue
        seen.add(literal)
        if not has_french(literal):
            line = text[:offset].count("\n") + 1
            missing.append((str(path.relative_to(REPO)), line, literal))

print(f"UI_LITERALS: {len(seen)}")
print(f"MISSING_FRENCH: {len(missing)}")
for path, line, literal in missing:
    print(f"  {path}:{line}  {literal!r}")
sys.exit(1 if missing else 0)

#!/usr/bin/env python3
"""Held-out localization coverage: find UI string literals missing Arabic.

Unlike localization_check.py (which validates catalog entries that exist), this
scans SwiftUI call sites for user-visible literals and asserts each has an
Arabic entry. It catches surfaces the live traversal may not reach, such as
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
BRAND = re.compile(r'^(?:Talos(?: \d[\d.]*)?|MCP|Git|GitHub|Ollama|OpenAI|Anthropic|Gemini|'
                   r'OpenCode Go|macOS|iOS|API|HTTP|HTTPS|URL|SSE|JSON|JSON-RPC|'
                   r'Ed25519|SHA-256|base64|PDF|esc|Chrome|Safari|Finder|Keychain)$')
HAS_WORD = re.compile(r'[A-Za-z]{3,}')

catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["strings"]


def has_arabic(key: str) -> bool:
    entry = catalog.get(key, {})
    value = entry.get("localizations", {}).get("ar", {}).get("stringUnit", {}).get("value")
    return bool(value and re.search(r'[\u0600-\u06FF]', value))


missing = []
seen = set()
for path in sorted(ROOT.rglob("*.swift")):
    if path.name in {"Localizable.xcstrings"}:
        continue
    text = path.read_text(encoding="utf-8")
    for match in PATTERN.finditer(text):
        literal = match.group(1)
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
        if not has_arabic(literal):
            line = text[: match.start()].count("\n") + 1
            missing.append((str(path.relative_to(REPO)), line, literal))

print(f"UI_LITERALS: {len(seen)}")
print(f"MISSING_ARABIC: {len(missing)}")
for path, line, literal in missing:
    print(f"  {path}:{line}  {literal!r}")
sys.exit(1 if missing else 0)

#!/usr/bin/env python3
"""Validate the String Catalog before building.

Checks every source key has an Arabic translation, Arabic text is present,
placeholders survive translation, and no non-brand key falls back to English.
"""
import json
import re
import sys
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "Talos" / "Localizable.xcstrings"
BRAND_ONLY = {"Git", "MCP", "GitHub", "Talos", "Ollama", "OpenAI", "Anthropic", "Gemini", "OpenCode Go", "Keychain", "Finder", "macOS", "API", "HTTP", "HTTPS", "URL", "SSE", "JSON-RPC", "Ed25519", "SHA-256", "base64"}
PLACEHOLDER = re.compile(r"%(?:\d+\$)?(?:lld|ld|d|@|f|s)")
ARABIC = re.compile(r"[\u0600-\u06FF]")

data = json.loads(CATALOG.read_text(encoding="utf-8"))
strings = data.get("strings", {})
errors = []
translated = 0

for key, entry in strings.items():
    localizations = entry.get("localizations", {})
    unit = localizations.get("ar", {}).get("stringUnit", {})
    value = unit.get("value")
    if not value:
        errors.append(f"missing ar translation: {key!r}")
        continue
    translated += 1
    if value.strip() == key.strip() and key not in BRAND_ONLY:
        errors.append(f"untranslated English fallback: {key!r}")
    if not ARABIC.search(value) and key not in BRAND_ONLY:
        errors.append(f"no Arabic script: {key!r} -> {value!r}")
    if sorted(PLACEHOLDER.findall(key)) != sorted(PLACEHOLDER.findall(value)):
        errors.append(
            f"placeholder mismatch: {key!r} {PLACEHOLDER.findall(key)} -> {value!r} {PLACEHOLDER.findall(value)}"
        )

print(f"CATALOG_KEYS: {len(strings)}")
print(f"ARABIC_TRANSLATIONS: {translated}")
if errors:
    print(f"FAILURES: {len(errors)}")
    for error in errors:
        print("  " + error)
    sys.exit(1)
print("FAILURES: 0")

#!/usr/bin/env python3
"""Validate the French String Catalog before building.

Checks every source key has a French translation, placeholders survive, and no
non-brand key silently falls back to English.
"""
import json
import re
import sys
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "Talos" / "Localizable.xcstrings"
LOCALE = "fr"
BRAND_ONLY = {
    "Git", "MCP", "GitHub", "Talos", "Ollama", "OpenAI", "Anthropic", "Gemini",
    "OpenCode Go", "Keychain", "Finder", "macOS", "API", "HTTP", "HTTPS", "URL",
    "SSE", "JSON-RPC", "Ed25519", "SHA-256", "base64", "PDF", "MCP",
}
# Words that are identical in English and French UI copy.
SAME_IN_FRENCH = {
    "Menu", "Date", "Audit", "Cloud", "Budget", "Total", "Maximum", "Minimum",
    "Version", "Document", "Documents", "Configuration", "Message", "Messages",
    "Session", "Sessions", "Profile", "Profiles", "Plugin", "Plugins",
    "Prompt", "Prompts", "Type", "Format", "Nom", "Simple", "Important",
    "Options", "Actions", "Éditeur", "Client", "Service", "Services",
    "Cloud · %@", "Plan", "Hooks", "Vision", "Images", "Shell", "Web", "Index",
    "Local", "Microphone", "Marketplace", "Arguments", "Port", "Hook", "Worker",
    "Source", "Signature", "Diagnostics", "Worktrees", "Fork", "Base",
    "Version %@", "%lld local", " · %lld cloud", "vision", "Assistant",
}
PLACEHOLDER = re.compile(r"%(?:\d+\$)?(?:lld|ld|d|@|f|s)")

data = json.loads(CATALOG.read_text(encoding="utf-8"))
strings = data.get("strings", {})
errors = []
translated = 0

for key, entry in strings.items():
    unit = entry.get("localizations", {}).get(LOCALE, {}).get("stringUnit", {})
    value = unit.get("value")
    if not value:
        errors.append(f"missing {LOCALE} translation: {key!r}")
        continue
    translated += 1
    if value.strip() == key.strip() and key not in BRAND_ONLY and key not in SAME_IN_FRENCH:
        errors.append(f"untranslated English fallback: {key!r}")
    if sorted(PLACEHOLDER.findall(key)) != sorted(PLACEHOLDER.findall(value)):
        errors.append(
            f"placeholder mismatch: {key!r} {PLACEHOLDER.findall(key)} -> {value!r} {PLACEHOLDER.findall(value)}"
        )

print(f"CATALOG_KEYS: {len(strings)}")
print(f"FRENCH_TRANSLATIONS: {translated}")
if errors:
    print(f"FAILURES: {len(errors)}")
    for error in errors:
        print("  " + error)
    sys.exit(1)
print("FAILURES: 0")

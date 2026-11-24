"""Terminal colour themes for Talos's ASCII interfaces."""

from __future__ import annotations

THEMES = {
    "cyan": {"accent": "\033[36m", "dim": "\033[2m", "ok": "\033[32m", "warn": "\033[33m"},
    "mono": {"accent": "", "dim": "", "ok": "", "warn": ""},
    "amber": {"accent": "\033[33m", "dim": "\033[2m", "ok": "\033[32m", "warn": "\033[31m"},
}
RESET = "\033[0m"


def names() -> tuple[str, ...]:
    return tuple(sorted(THEMES))


def color(text: str, role: str = "accent", theme: str = "cyan") -> str:
    if theme not in THEMES:
        raise ValueError(f"Unknown theme {theme!r}; choose {', '.join(names())}")
    code = THEMES[theme].get(role)
    if code is None:
        raise ValueError(f"Unknown role {role!r}")
    return f"{code}{text}{RESET}" if code else text

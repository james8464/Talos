"""Room-facing browser tasks: read a page or capture it.

The transport lives in :mod:`talos_core.mac.browser`; this module only parses a
plain-language request, enforces http(s), and returns a short text result.  No
audio is produced and page pixels are written to private state, never logged.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from talos_core.mac.browser import Browser, BrowserError, available

URL = r"(https?://[^\s<>]+)"


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:!?)\"'")


def read_page(url: str, *, limit: int = 700) -> dict:
    with Browser(headless=True, timeout=20) as browser:
        page = browser.open_page(url)
        snapshot = page.snapshot()
    text = " ".join(str(snapshot.text or "").split())
    return {
        "url": snapshot.url,
        "title": snapshot.title or snapshot.url,
        "text": text[:limit],
        "truncated": bool(snapshot.truncated) or len(text) > limit,
    }


def capture_page(url: str, state) -> str:
    folder = Path(state) / "screenshots"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / f"page-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.png"
    with Browser(headless=True, timeout=20) as browser:
        page = browser.open_page(url)
        page.screenshot(str(target), full_page=True)
    target.chmod(0o600)
    return str(target)


def command(text, state, *, limit: int = 700):
    """Answer a page-read or page-screenshot request, or return None."""
    words = " ".join(str(text).split())
    shot = re.fullmatch(
        rf"(?:please )?(?:screenshot|capture)(?: the)?(?: page| site)?"
        rf"(?: at| of| from)? ?{URL}[?.! ]*",
        words, re.I,
    )
    if shot:
        if not available():
            return "Browser automation is unavailable: " + _why()
        try:
            path = capture_page(_clean_url(shot.group(1)), state)
        except BrowserError as error:
            return f"Browser error: {error}"
        return f"Page screenshot saved to {path}"

    read = re.fullmatch(
        rf"(?:please )?(?:read|summarize|summarise|browse)(?: the)?(?: web ?page| page| site)?"
        rf"(?: at| to| of)? ?{URL}[?.! ]*",
        words, re.I,
    )
    if not read:
        return None
    if not available():
        return "Browser automation is unavailable: " + _why()
    try:
        result = read_page(_clean_url(read.group(1)), limit=limit)
    except BrowserError as error:
        return f"Browser error: {error}"
    body = result["text"] or "The page loaded with no readable text."
    suffix = " …" if result["truncated"] else ""
    return f"{result['title']}: {body}{suffix}"[:1100]


def _why() -> str:
    try:
        from talos_core.mac.browser import availability

        return availability().message
    except (OSError, RuntimeError):
        return "no supported browser was found."

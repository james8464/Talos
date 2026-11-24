"""Room-facing browser tasks: read a page or capture it.

The transport lives in :mod:`talos_core.mac.browser`; this module only parses a
plain-language request, enforces http(s), and returns a short text result.  No
audio is produced and page pixels are written to private state, never logged.
"""

from __future__ import annotations

import json
import re
import time
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


def click_text(url: str, text: str, *, limit: int = 500) -> dict | None:
    """Click the first visible link/button whose label contains ``text``."""
    wanted = " ".join(str(text).split())[:120]
    if not wanted:
        return None
    script = f"""(() => {{
      const wanted = {json.dumps(wanted.casefold())};
      const nodes = Array.from(document.querySelectorAll(
        'a, button, [role="button"], input[type="submit"], input[type="button"]'));
      const target = nodes.find((el) => {{
        if (!el || el.offsetParent === null) return false;
        const label = (el.innerText || el.value || el.getAttribute('aria-label') || '')
          .replace(/\s+/g, ' ').trim().toLowerCase();
        return label.includes(wanted);
      }});
      if (!target) return {{ok: false}};
      const label = (target.innerText || target.value || wanted).trim();
      target.click();
      return {{ok: true, label: label}};
    }})()"""
    with Browser(headless=True, timeout=20) as browser:
        page = browser.open_page(url)
        clicked = page.eval_js(script)
        if not isinstance(clicked, dict) or not clicked.get("ok"):
            return None
        time.sleep(0.35)
        snapshot = page.snapshot()
    body = " ".join(str(snapshot.text or "").split())
    return {
        "url": snapshot.url,
        "title": snapshot.title or snapshot.url,
        "label": str(clicked.get("label") or wanted),
        "text": body[:limit],
        "truncated": bool(snapshot.truncated) or len(body) > limit,
    }


def type_into(url: str, field: str, value: str, *, limit: int = 500) -> dict | None:
    """Type into the first matching input/textarea and return the page result."""
    wanted = " ".join(str(field).split())[:80].casefold()
    value = str(value)
    if not wanted or not value:
        return None
    script = f"""(() => {{
      const wanted = {json.dumps(wanted)};
      const value = {json.dumps(value)};
      const label = (el) => ((el.getAttribute('aria-label') || el.getAttribute('placeholder')
        || el.name || el.id || '') + ' ' + (el.getAttribute('type') || ''))
        .replace(/\s+/g, ' ').trim().toLowerCase();
      const nodes = Array.from(document.querySelectorAll(
        'input:not([type="hidden"]), textarea, [contenteditable="true"]'));
      let target = nodes.find((el) => el.offsetParent !== null && label(el).includes(wanted));
      if (!target && nodes.length === 1 && nodes[0].offsetParent !== null) target = nodes[0];
      if (!target) return {{ok: false}};
      const shown = (target.getAttribute('aria-label') || target.getAttribute('placeholder')
        || target.name || target.id || 'field').trim();
      target.focus();
      if (target.isContentEditable) target.textContent = value;
      else target.value = value;
      target.dispatchEvent(new Event('input', {{bubbles: true}}));
      target.dispatchEvent(new Event('change', {{bubbles: true}}));
      return {{ok: true, label: shown}};
    }})()"""
    with Browser(headless=True, timeout=20) as browser:
        page = browser.open_page(url)
        typed = page.eval_js(script)
        if not isinstance(typed, dict) or not typed.get("ok"):
            return None
        time.sleep(0.2)
        snapshot = page.snapshot()
    body = " ".join(str(snapshot.text or "").split())
    return {
        "url": snapshot.url,
        "title": snapshot.title or snapshot.url,
        "label": str(typed.get("label") or wanted),
        "characters": len(value),
        "text": body[:limit],
        "truncated": bool(snapshot.truncated) or len(body) > limit,
    }


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

    type_web = re.fullmatch(
        rf'type (?:"([^"]+)"|(.+?)) into (?:the )?(.+?) on {URL}[?.! ]*',
        words, re.I,
    )
    if type_web:
        if not available():
            return "Browser automation is unavailable: " + _why()
        value = (type_web.group(1) or type_web.group(2) or "").strip()
        field = re.sub(r" (?:field|box|input)$", "", type_web.group(3).strip(), flags=re.I)
        url = _clean_url(type_web.group(4))
        if not value or not field:
            return "Tell me what to type and which field to use."
        try:
            result = type_into(url, field, value, limit=limit)
        except BrowserError as error:
            return f"Browser error: {error}"
        if result is None:
            return f"I found no field matching {field!r} on {url}."
        suffix = " …" if result["truncated"] else ""
        return (f"Typed {result['characters']} characters into {result['label']} on "
                f"{result['title']}: {result['text'] or 'no readable text'}{suffix}")[:1100]

    click = re.fullmatch(
        rf"(?:please )?click (?:the )?(.+?) on {URL}[?.! ]*",
        words, re.I,
    )
    if click:
        if not available():
            return "Browser automation is unavailable: " + _why()
        label, url = click.group(1).strip(), _clean_url(click.group(2))
        try:
            result = click_text(url, label, limit=limit)
        except BrowserError as error:
            return f"Browser error: {error}"
        if result is None:
            return f"I found no clickable text matching {label!r} on {url}."
        suffix = " …" if result["truncated"] else ""
        return (f"Clicked {result['label']} on {result['title']}: "
                f"{result['text'] or 'no readable text'}{suffix}")[:1100]

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

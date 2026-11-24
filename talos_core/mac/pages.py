"""Create real Apple Pages documents through the app's AppleScript model.

This is the write path for Pages. It is separate from the read-only native app
adapters and from the textutil ``.docx`` fallback: the user explicitly asked for
a Pages document, so Talos drives Pages itself and saves a ``.pages`` package.
The call is argv-only, bounded by a timeout, and never produces audio.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
PAGES_APPS = (
    Path("/Applications/Pages Creator Studio.app"),
    Path("/Applications/Pages.app"),
)
BUNDLE = "com.apple.Pages"
MAX_TEXT = 20_000


class PagesError(RuntimeError):
    pass


def available() -> bool:
    return any(path.is_dir() for path in PAGES_APPS)


def _apple(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def script(path: Path, title: str, body: str) -> str:
    title = " ".join(str(title).split())[:160] or "Talos document"
    paragraphs = [title]
    paragraphs.extend(line for line in str(body).splitlines() if line.strip())
    text = "\n".join(paragraphs)[:MAX_TEXT]
    return (
        'tell application "Pages"\n'
        "  activate\n"
        "  set doc to make new document\n"
        "  tell doc\n"
        f'    set body text to "{_apple(text)}"\n'
        "  end tell\n"
        f'  save doc in POSIX file "{_apple(str(path))}"\n'
        "  close doc saving no\n"
        "end tell\n"
    )


def _osascript(body: str, *, timeout: int = 30) -> None:
    try:
        completed = subprocess.run(
            [OSASCRIPT, "-e", body], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PagesError(f"Pages automation failed: {error}") from error
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300]
        raise PagesError(detail or f"osascript exited {completed.returncode}")


def create(state, title: str, body: str = "", *, open_after: bool = False,
           timeout: int = 30) -> dict:
    if not available():
        raise PagesError("Pages is not installed.")
    folder = Path(state) / "designs"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = folder / f"pages-{stamp}.pages"
    _osascript(script(target, title, body), timeout=timeout)
    if not target.is_file():
        raise PagesError("Pages reported success without saving a document.")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    result = {"path": str(target), "kind": "pages", "opened": False}
    if open_after:
        try:
            subprocess.run([OPEN, "-b", BUNDLE, str(target)], check=False, timeout=15)
            result["opened"] = True
        except (OSError, subprocess.SubprocessError):
            pass
    return result

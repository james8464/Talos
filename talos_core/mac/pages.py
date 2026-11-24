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


def append_script(source: Path, text: str) -> str:
    clean = " ".join(str(text).split())[:4000]
    return (
        'tell application "Pages"\n'
        "  activate\n"
        f'  set doc to open POSIX file "{_apple(str(source))}"\n'
        "  tell doc\n"
        f'    set body text to (body text & return & "{_apple(clean)}")\n'
        "  end tell\n"
        "  save doc\n"
        "  close doc saving no\n"
        "end tell\n"
    )


def append(state, source, text, *, timeout: int = 30) -> dict:
    source = Path(source).resolve()
    designs = (Path(state) / "designs").resolve()
    if designs not in source.parents:
        raise PagesError("Only documents inside the Talos design folder can be edited.")
    if source.suffix.lower() != ".pages" or not source.is_file():
        raise PagesError("The source is not a Pages document.")
    clean = " ".join(str(text).split())[:4000]
    if not clean:
        raise PagesError("There is no text to append.")
    _osascript(append_script(source, clean), timeout=timeout)
    if not source.is_file():
        raise PagesError("Pages did not save the document.")
    return {"path": str(source), "kind": "pages-append", "characters": len(clean)}


def export_script(source: Path, target: Path) -> str:
    return (
        'tell application "Pages"\n'
        "  activate\n"
        f'  set doc to open POSIX file "{_apple(str(source))}"\n'
        f'  export doc to POSIX file "{_apple(str(target))}" as PDF\n'
        "  close doc saving no\n"
        "end tell\n"
    )


def export_pdf(state, source, *, target=None, timeout: int = 45) -> dict:
    source = Path(source).resolve()
    designs = (Path(state) / "designs").resolve()
    if designs not in source.parents:
        raise PagesError("Only documents inside the Talos design folder can be exported.")
    if source.suffix.lower() != ".pages" or not source.is_file():
        raise PagesError("The source is not a Pages document.")
    destination = Path(target).resolve() if target else source.with_suffix(".pdf")
    if destination.parent != designs:
        raise PagesError("The PDF must stay inside the Talos design folder.")
    _osascript(export_script(source, destination), timeout=timeout)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise PagesError("Pages did not write a PDF.")
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return {"path": str(destination), "kind": "pdf", "source": str(source)}


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

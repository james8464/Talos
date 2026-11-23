"""Local, source-linked imports for Talos's private memory search."""

import json
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from personal_files import DATALESS, MAX_TEXT, OFFICE_TEXT, document_text, records

APPS = ("Calendar", "Reminders", "Notes", "Bookmarks", "Mail archive", "Projects",
        "Cloud documents", "Confirmations", "OCR")
PREFIX = "talos:knowledge:"
CONFIRMATION = re.compile(r"\b(?:booking|reservation|order|tracking|ticket|itinerary)\s+(?:reference|number|confirmation|confirmed|id)\b", re.I)


class NoteText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def apple_notes():
    script = """
    const notes = Application('Notes').notes;
    const ids = notes.id(), names = notes.name(), bodies = notes.body();
    JSON.stringify(ids.map((id, i) => ({id, title: names[i], body: bodies[i]})));
    """
    result = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                            capture_output=True, text=True, timeout=90, check=True)
    for note in json.loads(result.stdout):
        parser = NoteText()
        parser.feed(str(note["body"])[:MAX_TEXT])
        yield "apple-notes:" + note["id"], note["title"] + "\n" + " ".join(parser.parts)


def bookmarks(path):
    def walk(item):
        if isinstance(item, list):
            for entry in item:
                yield from walk(entry)
        elif isinstance(item, dict):
            url = item.get("URLString") or item.get("url")
            title = item.get("name") or (item.get("URIDictionary") or {}).get("title")
            if isinstance(url, str):
                parts = urlsplit(url)
                if parts.scheme in {"http", "https"} and parts.hostname:
                    clean = urlunsplit((parts.scheme, parts.hostname, parts.path, "", ""))
                    yield clean, f"{title or ''} {clean}"
            for key in ("Children", "children", "roots"):
                children = item.get(key)
                yield from walk(list(children.values()) if isinstance(children, dict) else children)
    try:
        if path.stat().st_size > 32 * MAX_TEXT:
            return
        with path.open("rb") as stream:
            yield from walk(plistlib.load(stream) if path.suffix == ".plist" else json.load(stream))
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return


def mail(path):
    try:
        if getattr(path.stat(), "st_flags", 0) & DATALESS:
            return
        with path.open("rb") as stream:
            count = int(stream.readline(32))
            if not 0 < count <= 8 * 1024 * 1024:
                return
            message = BytesParser(policy=policy.default).parsebytes(stream.read(count))
        headers = " ".join(str(message.get(key, "")) for key in ("From", "To", "Date", "Subject", "Message-ID"))
        body = message.get_body(preferencelist=("plain",))
        text = body.get_content()[:MAX_TEXT] if body else ""
        yield "message", f"{headers}\n{text}"
        for number, attachment in enumerate(message.iter_attachments()):
            name = attachment.get_filename()
            if name:
                data = attachment.get_payload(decode=True)
                excerpt = ""
                suffix = Path(name).suffix.casefold()
                if data and attachment.get_content_maintype() == "text":
                    excerpt = data[:MAX_TEXT].decode("utf-8", errors="replace")
                elif data and (suffix == ".pdf" or suffix in OFFICE_TEXT):
                    with tempfile.TemporaryDirectory(prefix="talos-attachment-") as directory:
                        extracted = Path(directory) / ("attachment" + suffix)
                        extracted.write_bytes(data)
                        excerpt = document_text(extracted, extracted.stat(), shutil.which("pdftotext"))
                yield f"attachment-{number}", f"{name}\n{headers}\n{excerpt}"
    except (OSError, ValueError, LookupError, TypeError):
        return


def exports(root, kind):
    for key, body in records(root, (kind,)):
        yield key, body
    folder = root / kind
    if not folder.is_dir():
        return
    for path in folder.glob("*.jsonl"):
        if path.is_symlink():
            continue
        with path.open(encoding="utf-8", errors="replace") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    body = data.get("text") if set(data) == {"id", "text"} else None
                    yield str(data.get("id") or f"{path.name}:{number}"), (
                        body if isinstance(body, str) else json.dumps(data, ensure_ascii=False)
                    )[:MAX_TEXT]


def project_history(home):
    root = home / "Developer"
    if not root.is_dir():
        return
    for repo in root.iterdir():
        if repo.is_symlink() or not (repo / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "log", "-n", "1000", "--format=%H%x09%aI%x09%an%x09%s"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
                encoding="utf-8", errors="replace",
            )
            for line in result.stdout.splitlines():
                sha, _, details = line.partition("\t")
                if re.fullmatch(r"[0-9a-f]{40,64}", sha):
                    yield f"{repo.name}:{sha}", f"{repo.name} {details}"
        except (OSError, subprocess.TimeoutExpired):
            continue


def collect(state, home, kind):
    yield from exports(state / "knowledge-imports", kind)
    library = home / "Library"
    if kind == "Bookmarks":
        yield from bookmarks(library / "Safari/Bookmarks.plist")
        for browser in ("Google/Chrome", "BraveSoftware/Brave-Browser", "Microsoft Edge"):
            for path in (library / "Application Support" / browser).glob("*/Bookmarks"):
                yield from bookmarks(path)
    elif kind == "Mail archive":
        for path in (library / "Mail").glob("V*/*/**/*.emlx"):
            if stat.S_ISREG(path.lstat().st_mode):
                for key, body in mail(path):
                    yield f"{path.relative_to(library)}:{key}", body
    elif kind == "Projects":
        yield from project_history(home)
    elif kind == "Cloud documents":
        yield from records(library, ("Mobile Documents/com~apple~CloudDocs", "CloudStorage"))

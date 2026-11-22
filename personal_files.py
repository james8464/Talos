"""Collect readable personal documents for Talos's local search index."""

import os
import re
import shutil
import stat
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SOURCE = "talos:home-documents"
FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music")
TEXT = {".txt", ".md", ".rtf", ".csv", ".tsv", ".ics"}
DOCUMENTS = TEXT | {".pdf", ".docx", ".xlsx", ".pptx", ".pages", ".numbers", ".keynote",
                    ".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov", ".mp3", ".m4a"}
SKIP = {"node_modules", "__pycache__", "build", "dist", "venv"}
SENSITIVE = ("password", "credential", "secret", "recovery", "keychain", "private-key")
MAX_TEXT = 128 * 1024
DATALESS = getattr(stat, "UF_DATALESS", 0x40000000)
MAX_PDF = 25 * 1024 * 1024
MAX_XML = 2 * 1024 * 1024
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OFFICE_TEXT = {
    ".docx": (r"word/document\.xml", WORD_NAMESPACE + "t"),
    ".pptx": (r"ppt/slides/slide\d+\.xml",
              "{http://schemas.openxmlformats.org/drawingml/2006/main}t"),
    ".xlsx": (r"xl/(?:sharedStrings|worksheets/sheet\d+)\.xml",
              "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"),
}


def office_text(path, suffix):
    pattern, tag = OFFICE_TEXT[suffix]
    parts = []
    remaining = MAX_XML
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if not re.fullmatch(pattern, entry.filename) or entry.file_size > remaining:
                continue
            remaining -= entry.file_size
            try:
                root = ET.fromstring(archive.read(entry))
            except ET.ParseError:
                continue
            parts.extend(node.text for node in root.iter(tag) if node.text)
            if suffix == ".xlsx":
                numeric = tag[:-1] + "v"
                cell = tag[:-1] + "c"
                for item in root.iter(cell):
                    if item.get("t", "n") == "n" and (value := item.findtext(numeric)):
                        parts.append(value)
    return " ".join(parts)[:MAX_TEXT]


def document_text(path, info, pdf_reader):
    if getattr(info, "st_flags", 0) & DATALESS:
        return ""
    suffix = path.suffix.lower()
    if suffix in TEXT:
        with path.open("rb") as stream:
            data = stream.read(MAX_TEXT)
        return "" if b"\0" in data else data.decode("utf-8", errors="replace")
    if suffix == ".pdf" and pdf_reader and info.st_size <= MAX_PDF:
        try:
            result = subprocess.run(
                [pdf_reader, "-f", "1", "-l", "15", "-q", str(path), "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
            )
            if result.returncode == 0:
                return result.stdout[:MAX_TEXT].decode("utf-8", errors="replace").strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if suffix in OFFICE_TEXT and info.st_size <= MAX_PDF:
        try:
            return office_text(path, suffix)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            pass
    return ""


def candidates(home, roots=FOLDERS):
    for folder in roots:
        root = home / folder
        if root.is_symlink():
            continue
        for directory, folders, names in os.walk(root):
            folders[:] = [name for name in folders if not skip(name) and name not in SKIP]
            for name in names:
                path = Path(directory) / name
                if skip(name) or path.suffix.lower() not in DOCUMENTS or path.is_symlink():
                    continue
                try:
                    info = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    relative = str(path.relative_to(home))
                    yield relative, path, info
                except OSError:
                    continue


def indexed_body(relative, path, info, pdf_reader):
    content = document_text(path, info, pdf_reader)
    return f"{relative}\n{content}" if content else relative


def records(home, roots=FOLDERS):
    pdf_reader = shutil.which("pdftotext")
    for relative, path, info in candidates(home, roots):
        try:
            yield relative, indexed_body(relative, path, info, pdf_reader)
        except OSError:
            continue


def skip(name):
    return name.startswith(".") or any(word in name.casefold() for word in SENSITIVE)

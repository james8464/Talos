"""Local skill workshop: validate, discover and create agent skill files."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,60}")
DESCRIPTION = re.compile(r".{5,400}", re.S)


def validate(text: str) -> dict:
    if not isinstance(text, str):
        raise TypeError("Skill text must be a string.")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("A skill needs YAML frontmatter.")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("Unclosed skill frontmatter.") from error
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        if key.strip() in ("name", "description"):
            fields[key.strip()] = value.strip()
    if not NAME.fullmatch(fields.get("name", "")):
        raise ValueError("Skill name must be 1–60 letters, digits, spaces, _ or -.")
    if not DESCRIPTION.fullmatch(fields.get("description", "")):
        raise ValueError("Skill description must contain 5–400 characters.")
    return {"name": fields["name"], "description": fields["description"],
            "body": "\n".join(lines[end + 1:]).strip()}


def create(state: Path | str, name: str, description: str, body: str = "") -> Path:
    folder = Path(state) / "skills"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "skill"
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body.strip()}\n"
    validate(text)
    fd, temporary = tempfile.mkstemp(prefix=".skill-", dir=folder)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        destination = folder / f"{slug}.md"
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


def discover(state: Path | str) -> list[dict]:
    folder = Path(state) / "skills"
    found = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else ():
        try:
            found.append({"path": str(path), **validate(path.read_text(encoding="utf-8"))})
        except (OSError, ValueError):
            continue
    return found


def find(state: Path | str, query: str) -> dict | None:
    """Return the single skill matching a name or description, if unambiguous."""
    words = " ".join(str(query).split()).casefold()
    if not words:
        return None
    found = discover(state)
    exact = [item for item in found if item["name"].casefold() == words]
    if exact:
        return exact[0]
    partial = [
        item for item in found
        if words in item["name"].casefold() or words in item["description"].casefold()
    ]
    return partial[0] if len(partial) == 1 else None

"""Read-only GitHub status and explicitly confirmed local publish."""

from __future__ import annotations

import subprocess
from pathlib import Path


class PublishRefused(RuntimeError):
    pass


def _git(root: Path, *args, runner):
    completed = runner(["git", "-C", str(root), *args], capture_output=True,
                       text=True, timeout=30, check=False)
    return completed.returncode, (completed.stdout or "").strip(), (completed.stderr or "").strip()


def status(root: Path | str, *, runner=subprocess.run) -> dict:
    root = Path(root)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", runner=runner)
    dirty = _git(root, "status", "--porcelain", runner=runner)
    remote = _git(root, "remote", "get-url", "origin", runner=runner)
    return {"branch": branch[1] if branch[0] == 0 else "",
            "dirty": bool(dirty[1]), "remote": remote[1] if remote[0] == 0 else ""}


def publish(root: Path | str, message: str, *, confirm: bool = False,
            runner=subprocess.run) -> dict:
    if not confirm:
        raise PublishRefused("Publishing needs explicit confirm=True.")
    if not isinstance(message, str) or not 3 <= len(message.strip()) <= 200:
        raise ValueError("Use a commit message of 3–200 characters.")
    root = Path(root)
    for args in (("add", "-A"), ("commit", "-m", message.strip()), ("push",)):
        code, _out, _err = _git(root, *args, runner=runner)
        if code != 0:
            raise RuntimeError(f"git {args[0]} failed")
    return {"published": True, "message": message.strip(), "status": status(root, runner=runner)}

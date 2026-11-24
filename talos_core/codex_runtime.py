"""Explicitly confirmed wrapper around the local Codex CLI runtime."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class RuntimeRefused(RuntimeError):
    pass


def run(prompt: str, *, root: Path | str = ".", model: str | None = None,
        confirm: bool = False, runner=subprocess.run, timeout: int = 300) -> dict:
    if not confirm:
        raise RuntimeRefused("Codex runtime needs explicit confirm=True.")
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is not installed.")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 8000:
        raise ValueError("Prompt must be 1–8,000 characters.")
    argv = [executable, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
            "--ephemeral", "-"]
    if model:
        argv[2:2] = ["--model", model]
    completed = runner(argv, cwd=str(root), input=prompt, capture_output=True,
                       text=True, timeout=timeout, check=False)
    return {"ok": completed.returncode == 0, "returncode": completed.returncode,
            "output": (completed.stdout or "")[:8000],
            "error": (completed.stderr or "")[:2000]}

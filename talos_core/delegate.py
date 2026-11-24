"""Run an explicitly confirmed registered subagent without a shell."""

from __future__ import annotations

import subprocess
from pathlib import Path

from talos_core import subagents


class DelegateRefused(RuntimeError):
    pass


def delegate(state: Path | str, name: str, task: str, *, confirm: bool = False,
             runner=subprocess.run, timeout: int = 180) -> dict:
    if not confirm:
        raise DelegateRefused("Delegation needs explicit confirm=True.")
    argv = subagents.command_for(state, name, task)
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Delegated agent failed: {error}") from error
    return {"agent": name, "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": (completed.stdout or "").strip()[:8000],
            "error": (completed.stderr or "").strip()[:2000]}

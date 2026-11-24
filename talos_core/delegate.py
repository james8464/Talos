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


def delegate_many(state, tasks, *, confirm: bool = False, workers: int = 2,
                  runner=subprocess.run, timeout: int = 180) -> list[dict]:
    """Run registered subagents concurrently, isolating failures per task."""
    from concurrent.futures import ThreadPoolExecutor

    if not confirm:
        raise DelegateRefused("Delegation needs explicit confirm=True.")
    if not isinstance(tasks, (list, tuple)) or not tasks:
        raise ValueError("Provide at least one (name, task) pair.")
    jobs = []
    for item in tasks:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Each task must be a (name, task) pair.")
        name, task = item
        jobs.append((subagents.command_for(state, str(name), str(task)), str(name)))
    count = max(1, min(int(workers), 4, len(jobs)))

    def one(job):
        argv, name = job
        try:
            completed = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return {"agent": name, "ok": False, "returncode": None,
                    "output": "", "error": f"Delegated agent failed: {error}"}
        return {"agent": name, "ok": completed.returncode == 0,
                "returncode": completed.returncode,
                "output": (completed.stdout or "").strip()[:8000],
                "error": (completed.stderr or "").strip()[:2000]}

    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="talos-delegate") as pool:
        return list(pool.map(one, jobs))

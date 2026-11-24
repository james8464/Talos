"""Safe inventory of Talos-owned processes and listeners.

Reports labels and PIDs only, never command-line contents, so prompts or private
paths do not leak. ``stop_stale`` is deliberately limited to known test/UI
leftovers; it never stops the room service, workers, or audio processes.
"""

from __future__ import annotations

import os
import signal
import subprocess

PATTERNS = (
    ("room service", ("run_talos.py",), False, False),
    ("source observer", ("source_observer",), False, False),
    ("worker", ("talos_core.self_improvement.", "talos_core.memory.",
                "talos_core.runtime."), False, False),
    ("stale GUI", ("talos_core.cli gui",), True, False),
    ("live harness", ("talos_live_features",), True, False),
    ("headless browser", ("talos-browser-",), True, False),
    ("cursor overlay", ("talos-cursor-overlay",), False, False),
    ("audio", ("afplay", "/usr/bin/say", "kokoro", "AVSpeech"), False, True),
)
STOPPABLE = frozenset({"stale GUI", "live harness", "headless browser"})
IGNORED_EXECUTABLES = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
    "ps", "pgrep", "lsof", "rg", "grep",
})


def classify(args: str):
    if not isinstance(args, str):
        return None
    executable = args.strip().split(None, 1)[0].rsplit("/", 1)[-1].casefold() if args.strip() else ""
    if executable in IGNORED_EXECUTABLES:
        return None
    for label, words, stale, audio in PATTERNS:
        if any(word in args for word in words):
            return {"label": label, "stale": stale, "audio": audio}
    return None


def _ps(runner=subprocess.run) -> list[dict]:
    try:
        completed = runner(["ps", "-axo", "pid=,ppid=,args="], capture_output=True,
                           text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        found = classify(parts[2])
        if not found:
            continue
        rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), **found})
    return rows


def _ports(runner=subprocess.run) -> list[dict]:
    try:
        completed = runner(["lsof", "-nP", "-iTCP:8765", "-iTCP:8766", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    found = {}
    for line in (completed.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        pid, name = parts[1], parts[8]
        if not pid.isdigit():
            continue
        for port in ("8765", "8766"):
            if name.endswith(":" + port):
                found[port] = int(pid)
    return [{"port": int(port), "pid": pid} for port, pid in sorted(found.items())]


def inventory(*, runner=subprocess.run) -> dict:
    processes = _ps(runner)
    return {
        "processes": processes,
        "ports": _ports(runner),
        "stale": [item["pid"] for item in processes if item["stale"]],
        "audio": [item["pid"] for item in processes if item["audio"]],
    }


def stop_stale(inventory_data: dict | None = None) -> list[int]:
    data = inventory_data or inventory()
    stopped = []
    for item in data.get("processes", []):
        if item.get("label") not in STOPPABLE:
            continue
        try:
            os.kill(int(item["pid"]), signal.SIGTERM)
            stopped.append(int(item["pid"]))
        except (OSError, ValueError):
            continue
    return stopped


def summary(data: dict) -> str:
    counts = {}
    for item in data.get("processes", []):
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    parts = [f"{name} x{count}" if count > 1 else name for name, count in sorted(counts.items())]
    ports = ", ".join(f"{item['port']} (pid {item['pid']})" for item in data.get("ports", []))
    return (f"Processes: {'; '.join(parts) or 'none'} · ports: {ports or 'none'}"
            f" · stale: {len(data.get('stale', []))} · audio: {len(data.get('audio', []))}")

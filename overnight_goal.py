"""Independent Mac-side quota watcher for the overnight Talos improvement goal."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from agent_memory import open_memory
from idle_lab import experiment, power_and_idle

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
FREE_ROUTE = "https://gateway.respan.ai/v1"
FREE_MODEL = "zai/glm-4.5-flash"


def _window_observations(event, now):
    if not isinstance(event, dict) or event.get("type") != "event_msg":
        return {}
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return {}
    rates = payload.get("rate_limits")
    if not isinstance(rates, dict) or rates.get("limit_id") != "codex":
        return {}
    try:
        stamp = datetime.fromisoformat(event["timestamp"]).timestamp()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return {}
    if not 0 <= now - stamp <= 900:
        return {}
    windows = {}
    for key in ("primary", "secondary"):
        item = rates.get(key)
        if not isinstance(item, dict):
            continue
        used = item.get("used_percent")
        reset = item.get("resets_at")
        if (type(used) in (int, float) and 0 <= used <= 100
                and type(reset) in (int, float) and now < reset < float("inf")):
            windows[key] = (stamp, (100 - used, reset))
    return windows


def quota_from_event(event, now):
    windows = _window_observations(event, now)
    if windows:
        return next(iter(windows.values()))[0], [window for _, window in windows.values()]


def _latest_windows(now):
    root = Path.home() / ".codex/sessions"
    days = sorted(root.glob("20[0-9][0-9]/[0-1][0-9]/[0-3][0-9]"), reverse=True)[:2]
    files = []
    for day in days:
        for path in day.glob("*.jsonl"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= now - 900:
                files.append((modified, path))
    latest = {}
    for _, path in sorted(files, reverse=True)[:6]:
        try:
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - 1048576))
                for line in reversed(stream.read(1048576).splitlines()):
                    if b'"rate_limits"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    for key, observation in _window_observations(event, now).items():
                        if key not in latest or observation[0] > latest[key][0]:
                            latest[key] = observation
        except OSError:
            continue
    return {key: latest[key][1] for key in ("primary", "secondary") if key in latest}


def latest_quota(now):
    return list(_latest_windows(now).values())


def free_edit(stage, original):
    config = json.loads((Path.home() / ".openclaude/settings.json").read_text()).get(
        "env", {}
    )
    route = os.getenv("OPENAI_BASE_URL") or config.get("OPENAI_BASE_URL", "")
    model = os.getenv("OPENAI_MODEL") or config.get("OPENAI_MODEL")
    if route.rstrip("/") != FREE_ROUTE or model != FREE_MODEL:
        raise RuntimeError("Configured free route changed")
    cli = shutil.which("openclaude")
    if not cli:
        raise RuntimeError("OpenClaude is unavailable")
    prompt = (
        "Review this Talos Python source for "
        + stage
        + ". Offer one small correctness or speed improvement. "
        "Return one JSON object with strings before and after. before must exactly match a unique excerpt. "
        "Use empty strings if no useful improvement. No tools.\n" + original
    )
    with tempfile.TemporaryDirectory(prefix="talos-cloud-review-") as tmp:
        result = subprocess.run(
            [
                cli,
                "-p",
                "--bare",
                "--system-prompt",
                "Review the supplied Python source. Return just the requested JSON object.",
                "--model",
                model,
                "--tools",
                "",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--disable-slash-commands",
                "--effort",
                "low",
                "--json-schema",
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "before": {"type": "string"},
                            "after": {"type": "string"},
                        },
                        "required": ["before", "after"],
                        "additionalProperties": False,
                    }
                ),
                "--output-format",
                "json",
                prompt,
            ],
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            cwd=tmp,
            timeout=105,
            check=True,
        )
    envelope = json.loads(result.stdout)
    if envelope.get("is_error") or set(envelope.get("modelUsage", {})) != {model}:
        raise RuntimeError("Free reviewer reported a provider error")
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured
    text = (
        str(envelope.get("result", ""))
        .strip()
        .removeprefix("```json")
        .removesuffix("```")
        .strip()
    )
    return json.loads(text)


def check(state, until, now=None, *, quota=_latest_windows):
    return monitor(state, until, now=now, quota=quota, watch=False)


def monitor(state, until, *, now=None, quota=_latest_windows, watch=False):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "overnight.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "another check is active"
        while True:
            checked = time.time() if now is None else now
            outcome = check_locked(state, until, checked, quota)
            if not watch:
                return outcome
            print(outcome, flush=True)
            if checked >= until:
                return outcome
            time.sleep(min(300, max(0, until - time.time())))


def check_locked(state, until, now, quota):
    path = state / "overnight-status.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    try:
        windows = quota(now) if now < until else []
    except OSError:
        windows = []
    # Old callers supply anonymous (remaining, reset) pairs. The log reader
    # uses stable window names even when primary and secondary reset together.
    if not isinstance(windows, dict):
        by_reset = {}
        for remaining, reset in windows:
            key = str(reset)
            if key not in by_reset or remaining < by_reset[key][0]:
                by_reset[key] = (remaining, reset)
        windows = by_reset
    low = {key: reset for key, reset in prior.get("low_windows", {}).items() if reset > now}
    for key, (remaining, reset) in windows.items():
        if remaining < 3:
            low[key] = reset
        else:
            low.pop(key, None)
    # Earlier status files only recorded the furthest reset, without a window name.
    legacy = prior.get("legacy_handoff_until", 0)
    if "low_windows" not in prior:
        legacy = prior.get("handoff_until", 0)
    if legacy <= now or {"primary", "secondary"} <= windows.keys():
        legacy = 0
    lease = min(until, max([legacy, *low.values()]))
    status = {
        "checked_at": now,
        "ends_at": until,
        "remaining_percent": min((w[0] for w in windows.values()), default=None),
        "low_windows": low,
        "legacy_handoff_until": legacy,
        "handoff_until": lease,
        "last_review": prior.get("last_review", 0),
        "reviews": prior.get("reviews", 0),
    }
    outcome = (
        "ended"
        if now >= until
        else "quota unavailable"
        if not windows
        else "Codex budget available"
    )
    if now < min(lease, until):
        outcome = "free reviewer on standby"
        due = now - status["last_review"] >= 3600
        if due and not power_and_idle():
            outcome = "waiting for AC power and an idle Mac"
        elif due:
            db_path = state / "overnight-lab.sqlite"
            stages = (
                "conversation latency",
                "memory retrieval",
                "speech synthesis",
            )
            stage = stages[status["reviews"] % len(stages)]
            with closing(open_memory(db_path)) as db, db:
                trial = db.execute(
                    "INSERT INTO coding_trials(stage,occurrences,started,status) VALUES(?,1,?,'started')",
                    (stage, now),
                ).lastrowid
            status["last_review"] = now
            status["reviews"] += 1
            status["outcome"] = status["last_review_status"] = "review started"
            save_status(path, status)
            experiment(db_path, trial, "free-review", reviewer=free_edit)
            with closing(open_memory(db_path)) as db:
                outcome = db.execute(
                    "SELECT status FROM coding_trials WHERE id=?", (trial,)
                ).fetchone()[0]
    status["outcome"] = outcome
    if (
        status["last_review"] == prior.get("last_review")
        and "last_review_status" in prior
    ):
        status["last_review_status"] = prior["last_review_status"]
    elif status["last_review"]:
        status["last_review_status"] = outcome
    save_status(path, status)
    return outcome


def save_status(path, status):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
        json.dump(status, tmp)
    Path(tmp.name).replace(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--until", type=float, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    outcome = monitor(STATE, args.until, watch=args.watch)
    if not args.watch or outcome == "another check is active":
        print(outcome)

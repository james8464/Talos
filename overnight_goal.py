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


def quota_from_event(event, now):
    if event.get("type") != "event_msg":
        return None
    payload = event.get("payload") or {}
    rates = payload.get("rate_limits") or {}
    if payload.get("type") != "token_count" or rates.get("limit_id") != "codex":
        return None
    stamp = datetime.fromisoformat(event["timestamp"]).timestamp()
    if not 0 <= now - stamp <= 900:
        return None
    windows = []
    for key in ("primary", "secondary"):
        item = rates.get(key) or {}
        used = item.get("used_percent")
        reset = item.get("resets_at", 0)
        if (
            type(used) in (int, float)
            and 0 <= used <= 100
            and isinstance(reset, (float, int))
            and reset > now
        ):
            windows.append((100 - used, reset))
    if not windows:
        return None
    return stamp, windows


def latest_quota(now):
    root = Path.home() / ".codex/sessions"
    # Only inspect a bounded tail of recent session logs, retaining quota metadata alone.
    days = sorted(root.glob("20[0-9][0-9]/[0-1][0-9]/[0-3][0-9]"), reverse=True)[:2]
    files = sorted(
        (f for day in days for f in day.glob("*.jsonl")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:6]
    observations = []
    for path in files:
        try:
            if path.stat().st_mtime < now - 900:
                continue
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 1048576))
                for line in reversed(stream.readlines()):
                    if b'"rate_limits":{' not in line:
                        continue
                    try:
                        obs = quota_from_event(json.loads(line), now)
                    except (ValueError, KeyError, TypeError):
                        continue
                    if obs:
                        observations.append(obs)
                        break
        except OSError:
            continue
    return max(observations, key=lambda x: x[0])[1] if observations else []


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


def check(state, until, now=None, *, quota=latest_quota):
    return monitor(state, until, now=now, quota=quota, watch=False)


def monitor(state, until, *, now=None, quota=latest_quota, watch=False):
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
    lease = min(until, prior.get("handoff_until", 0))
    if windows:
        lease = min(
            until,
            max((reset for remaining, reset in windows if remaining < 3), default=0),
        )
    status = {
        "checked_at": now,
        "ends_at": until,
        "remaining_percent": min((w[0] for w in windows), default=None),
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

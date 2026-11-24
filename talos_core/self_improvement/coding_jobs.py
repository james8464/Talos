"""Bounded background coding jobs.

A job records one operator request against one source file. The runner drafts a
minimal before/after plus regression, verifies it in the existing checked-patch
sandbox, and applies it only when the job allows writes. Every job keeps its
diff, regression and report under ``state/coding-jobs/<id>/``.

The runner is deliberately narrow: one file, one model call, a wall-clock
budget, no app launches, no audio, and no network beyond the local model already
used by the room.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from talos_core.self_improvement import section_patches

FOLDER = "coding-jobs"
MAX_REQUEST = 800
MAX_TARGET_BYTES = 1_500_000
MIN_SECONDS = 5


def _now() -> float:
    return time.monotonic()


def _folder(state: Path) -> Path:
    return Path(state) / FOLDER


def _write(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _safe_target(root: Path, target: str) -> Path:
    candidate = Path(target)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Target must be a relative path inside the project.")
    path = (Path(root) / candidate).resolve()
    if Path(root).resolve() not in path.parents:
        raise ValueError("Target must be a relative path inside the project.")
    if not path.is_file():
        raise ValueError("Target file does not exist.")
    if path.stat().st_size > MAX_TARGET_BYTES:
        raise ValueError("Target file is too large for one bounded job.")
    return path


def start(state: Path | str, request: str, target: str, *, root: Path | str,
          budget: int = 300, apply: bool = True) -> dict:
    """Queue one job without touching the target. Returns the stored record."""
    request = (request or "").strip()
    if not request:
        raise ValueError("A request is required.")
    if len(request) > MAX_REQUEST:
        raise ValueError(f"Keep the request under {MAX_REQUEST} characters.")
    if not isinstance(budget, int) or not MIN_SECONDS <= budget <= 3600:
        raise ValueError("Budget must be between 5 and 3600 seconds.")
    _safe_target(Path(root), target)
    folder = _folder(Path(state))
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    job_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
    record = {
        "id": job_id,
        "request": request,
        "target": target,
        "apply": bool(apply),
        "budget": budget,
        "status": "queued",
        "created": time.time(),
        "folder": str(folder / job_id),
    }
    (folder / job_id).mkdir(mode=0o700)
    _write(folder / job_id / "job.json", record)
    return record


def load(state: Path | str, job_id: str) -> dict:
    if not job_id or not all(c.isalnum() or c in "-_" for c in job_id):
        raise ValueError("Invalid job id.")
    path = _folder(Path(state)) / job_id / "job.json"
    if not path.is_file():
        raise ValueError("Unknown job.")
    return json.loads(path.read_text(encoding="utf-8"))


def jobs(state: Path | str) -> list[dict]:
    folder = _folder(Path(state))
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*/job.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def _prompt(record: dict, source: str) -> str:
    return (
        section_patches.PATCH_INSTRUCTION
        + f"\nREQUEST: {record['request']}\n"
        + f"TARGET: {record['target']}\n"
        + f"\nSOURCE EXCERPT:\n{source[:12000]}"
    )


def run(state: Path | str, job_id: str, *, root: Path | str, infer, apply: bool | None = None,
        budget: int | None = None, clock=_now) -> dict:
    """Run one queued job to a terminal status. Never raises for a bad model reply."""
    state, root = Path(state), Path(root)
    record = load(state, job_id)
    if record.get("status") != "queued":
        return record
    folder = Path(record["folder"])
    apply = record.get("apply", True) if apply is None else apply
    limit = int(budget if budget is not None else record.get("budget", 300))
    deadline = clock() + max(MIN_SECONDS, limit)

    with (folder / "job.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return record
        record.update({"status": "running", "started": time.time()})
        _write(folder / "job.json", record)

        def finish(status: str, message: str, **extra) -> dict:
            record.update({"status": status, "message": message[:400],
                           "finished": time.time(), **extra})
            _write(folder / "job.json", record)
            _write(folder / "report.json", {
                "id": record["id"], "status": status, "message": message[:400],
                "target": record["target"], "apply": bool(apply),
                **extra,
            })
            return record

        try:
            target = _safe_target(root, record["target"])
            source = target.read_text(encoding="utf-8")
            before_sha = hashlib.sha256(source.encode()).hexdigest()
            (folder / "source.sha256").write_text(before_sha + "\n")

            if deadline - clock() < MIN_SECONDS:
                return finish("deferred", "budget exhausted before drafting")

            values = infer(_prompt(record, source))
            if deadline - clock() < MIN_SECONDS:
                return finish("deferred", "budget exhausted after drafting")
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except ValueError:
                    return finish("declined", "model returned no valid patch")
            if not isinstance(values, dict) or set(values) != set(section_patches.PATCH_FIELDS):
                return finish("declined", "model returned no valid patch")
            if not any(isinstance(v, str) and v for v in values.values()):
                return finish("declined", "model abstained")

            try:
                candidate = section_patches.checked_patch(
                    root, {"id": record["id"], "source": record["target"]},
                    source, source, {k: str(v) for k, v in values.items()})
            except (OSError, ValueError, SyntaxError) as error:
                return finish("declined", f"patch rejected: {error}")
            if candidate is None:
                return finish("declined", "model abstained")
            diff, regression, test_path = candidate
            (folder / "change.diff").write_text(diff, encoding="utf-8")
            (folder / "regression.py").write_text(regression, encoding="utf-8")

            timeout = max(MIN_SECONDS, min(60, int(deadline - clock())))
            if apply:
                outcome = section_patches.apply_verified_patch(
                    root, state, diff, extra_regression=regression, timeout=timeout)
                verification = outcome.get("verification", {})
                status = "applied" if outcome.get("applied") else (
                    "rolled_back" if outcome.get("rolled_back") else "declined")
                return finish(status, outcome.get("error") or f"patch {status}",
                              diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
                              tests_run=verification.get("tests_run", []),
                              stage=verification.get("stage"),
                              before_sha256=before_sha,
                              after_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                              test_path=test_path)
            verification = section_patches.verify_patch_in_sandbox(
                root, diff, extra_regression=regression, timeout=timeout)
            status = "verified" if verification.get("ok") else "declined"
            outcome = "passed" if verification.get("ok") else "rejected"
            return finish(status, f"sandbox {outcome} at {verification.get('stage')}",
                diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
                tests_run=verification.get("tests_run", []),
                stage=verification.get("stage"),
                before_sha256=before_sha,
                after_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                test_path=test_path)
        except Exception as error:  # a failed job must never take down the worker
            return finish("error", f"{type(error).__name__}: {error}")


def run_due(state: Path | str, *, root: Path | str, infer, apply: bool | None = None,
            limit: int = 1) -> list[dict]:
    """Run up to ``limit`` queued jobs in creation order."""
    state = Path(state)
    folder = _folder(state)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        pending = [job for job in jobs(state) if job.get("status") == "queued"]
        pending.sort(key=lambda job: job.get("created", 0))
        return [run(state, job["id"], root=root, infer=infer, apply=apply)
                for job in pending[:max(1, limit)]]

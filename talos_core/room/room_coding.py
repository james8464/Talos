"""Typed room commands for bounded, verify-only coding work.

The room never applies a coding change. These commands queue a job or a
dependency-ordered plan, run it with the local model, and report the sandbox
result. Voice input is not routed here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from talos_core.self_improvement import coding_jobs

COMMAND = re.compile(
    r"^(?:talos[, ]+)?(?:please\s+)?coding\s+(job|plan|jobs|status)\b[:\s]*(.*)$",
    re.I,
)
JOB_BUDGET = 120
PLAN_BUDGET = 120


def local_infer(model: str):
    from talos_core.self_improvement import initiative_coder

    return initiative_coder.local_infer(model)


def local_infer_many(model: str):
    return coding_jobs.local_infer_many(model)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jobs_reply(state: Path) -> dict:
    rows = coding_jobs.jobs(state)[-5:]
    if not rows:
        return {"reply": "No coding jobs have been queued."}
    return {"reply": "\n".join(
        f"{row['id']} · {row['status']} · {row.get('target') or row.get('targets')}"
        for row in rows)}


def _job_reply(body: str, state: Path, root: Path, model: str) -> dict:
    if ":" not in body:
        return {"reply": "Use: coding job <relative path>: <request>."}
    target_text, request = body.split(":", 1)
    target_text, request = target_text.strip().strip('"'), request.strip()
    if not request:
        return {"reply": "Add a request after the target path."}
    target = (root / target_text).resolve()
    before = _hash(target) if target.is_file() else None
    job = coding_jobs.start(state, request, target_text, root=root,
                            budget=JOB_BUDGET, apply=False)
    result = coding_jobs.run(state, job["id"], root=root, infer=local_infer(model))
    unchanged = before is not None and target.is_file() and _hash(target) == before
    return {"reply": (
        f"Coding job {job['id']} {result.get('status')}: {result.get('message', '')[:240]}. "
        f"Target unchanged: {'yes' if unchanged else 'no'}."
    )}


def _plan_reply(body: str, state: Path, root: Path, model: str) -> dict:
    path = (root / body.strip().strip('"')).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        return {"reply": "Name a plan JSON file inside the project."}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        steps = payload.get("steps") if isinstance(payload, dict) else payload
        plan = coding_jobs.start_plan(state, steps, root=root,
                                      budget=PLAN_BUDGET, apply=False)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {"reply": f"Plan rejected: {error}"}
    targets = [step["target"] for step in plan["steps"]]
    paths = [root / target for target in targets if (root / target).is_file()]
    before = {str(path): _hash(path) for path in paths}
    result = coding_jobs.run_plan(
        state, plan["id"], root=root, infer=local_infer(model),
        infer_many=local_infer_many(model),
    )
    unchanged = all((root / target).is_file() and _hash(root / target) == before[str(root / target)]
                    for target in targets if str(root / target) in before)
    statuses = ", ".join(f"{step['id']}={step['status']}" for step in result["steps"])
    return {"reply": (
        f"Coding plan {plan['id']} {result.get('status')}: {statuses}. "
        f"Targets unchanged: {'yes' if unchanged else 'no'}."
    )}


def handle(text: str, *, state: Path | str, root: Path | str, model: str) -> dict | None:
    match = COMMAND.match((text or "").strip())
    if not match:
        return None
    state, root = Path(state), Path(root)
    kind, body = match.group(1).lower(), match.group(2).strip()
    try:
        if kind in ("jobs", "status"):
            return _jobs_reply(state)
        if kind == "job":
            return _job_reply(body, state, root, model)
        return _plan_reply(body, state, root, model)
    except (OSError, ValueError) as error:
        return {"reply": f"Coding request rejected: {error}"}

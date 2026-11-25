"""Turn one free-form request into a small, validated coding plan.

The model chooses the files and order; this module bounds the shape, rejects
unknown files and cycles, and retries once with the validation error. Nothing
is executed here.
"""

from __future__ import annotations

import json
from pathlib import Path

from talos_core.self_improvement import coding_jobs

MAX_GENERATED_STEPS = 3
MAX_STEP_TARGETS = 3
MAX_CATALOG = 400


def catalog(root: Path) -> list[str]:
    root = Path(root)
    found = []
    for pattern in ("talos_core/**/*.py", "tests/test_*.py", "agent.py"):
        for path in root.glob(pattern):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
                found.append(str(path.relative_to(root)))
    return sorted(dict.fromkeys(found))[:MAX_CATALOG]


def _schema(max_steps: int) -> dict:
    step = {
        "type": "object",
        "required": ["id", "targets", "request", "needs"],
        "properties": {
            "id": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "string"},
                        "minItems": 1, "maxItems": MAX_STEP_TARGETS},
            "request": {"type": "string"},
            "needs": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["steps"],
        "properties": {"steps": {"type": "array", "items": step,
                                 "minItems": 1, "maxItems": max_steps}},
        "additionalProperties": False,
    }


def _prompt(request: str, files: list[str]) -> str:
    return (
        f"Plan the smallest bounded code change for the request. Return JSON with "
        f"1-{MAX_GENERATED_STEPS} steps. Each step has id, targets (1-{MAX_STEP_TARGETS} "
        "files from the catalog), request and needs (earlier step ids). Later steps may "
        "depend on earlier ids; never create a cycle. Use only catalog paths. "
        "Do not edit tests unless the request needs a test change.\n"
        f"REQUEST: {request[:800]}\n"
        f"CATALOG:\n{chr(10).join(files)[:12000]}"
    )


def default_infer(model: str):
    from agent import model_text

    def infer(prompt: str) -> str:
        return model_text(
            [{"role": "system",
              "content": "Produce a minimal dependency-ordered coding plan. Return only JSON."},
             {"role": "user", "content": prompt}],
            model=model, max_tokens=700, schema=_schema(MAX_GENERATED_STEPS),
        )

    return infer


def _parse_plan(raw, *, root: Path, completed=(),
                max_steps: int = MAX_GENERATED_STEPS) -> list[dict]:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    steps = payload["steps"] if isinstance(payload, dict) else None
    if not isinstance(steps, list):
        raise ValueError("Return a steps array.")
    if len(steps) > max_steps:
        raise ValueError(f"Use at most {max_steps} steps.")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Each step must be an object.")
        targets = step.get("targets")
        if isinstance(targets, str):
            targets = [targets]
        if targets is None:
            single = step.get("target")
            targets = [single] if isinstance(single, str) else None
        if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_STEP_TARGETS:
            raise ValueError(f"Each step needs 1-{MAX_STEP_TARGETS} targets.")
        ident, request, needs = (step.get("id"), step.get("request"),
                                 step.get("needs") or [])
        step.clear()
        step.update({"id": ident, "targets": targets,
                     "request": request, "needs": needs})
    return coding_jobs.validate_steps(steps, root=root, max_steps=max_steps,
                                      completed=completed)


def generate(request: str, *, root: Path | str, infer, attempts: int = 2) -> dict:
    """Return a validated plan dict or raise ValueError after ``attempts`` tries."""
    request = (request or "").strip()
    if not request:
        raise ValueError("A plan request is required.")
    root = Path(root)
    files = catalog(root)
    if not files:
        raise ValueError("No source files are available to plan against.")
    prompt = _prompt(request, files)
    error = ""
    for _ in range(max(1, attempts)):
        raw = infer(prompt if not error else prompt + "\nThe previous plan was rejected: " + error)
        try:
            return {"steps": _parse_plan(raw, root=root)}
        except (KeyError, TypeError, ValueError, SyntaxError) as caught:
            error = str(caught)[:300]
    raise ValueError(f"Plan generation failed: {error}")


def repair(goal: str, *, root: Path | str, failed: dict, completed: list[dict],
           error: str, infer, attempts: int = 2) -> dict:
    """Revise only the remaining work after one step failed verification."""
    root = Path(root)
    files = catalog(root)
    done = ", ".join(f"{item['id']} ({item.get('status')})" for item in completed) or "none"
    prompt = (
        "Revise the remaining steps of a coding plan. Do not repeat completed steps. "
        f"The failed step was {failed['id']} on {failed.get('targets') or failed.get('target')} "
        f"for this request: {failed.get('request', '')[:400]}.\n"
        f"Completed steps: {done}.\n"
        f"Failure output:\n{error[:1500]}\n"
        f"Return JSON with 1-{MAX_GENERATED_STEPS} replacement steps using the same fields "
        f"(id, targets, request, needs). They may depend on completed ids. Use only catalog paths.\n"
        f"ORIGINAL REQUEST: {(goal or '')[:800]}\nCATALOG:\n"
        f"{chr(10).join(files)[:12000]}"
    )
    failure = ""
    for _ in range(max(1, attempts)):
        raw = infer(prompt if not failure else prompt + "\nThe previous plan was rejected: " + failure)
        try:
            return {"steps": _parse_plan(raw, root=root,
                                         completed={item["id"] for item in completed})}
        except (KeyError, TypeError, ValueError, SyntaxError) as caught:
            failure = str(caught)[:300]
    raise ValueError(f"Plan repair failed: {failure}")

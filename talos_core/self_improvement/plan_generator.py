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
            payload = json.loads(raw) if isinstance(raw, str) else raw
            steps = payload["steps"] if isinstance(payload, dict) else None
            if not isinstance(steps, list):
                raise ValueError("Return a steps array.")
            if len(steps) > MAX_GENERATED_STEPS:
                raise ValueError(f"Use at most {MAX_GENERATED_STEPS} steps.")
            for step in steps:
                if isinstance(step, dict):
                    targets = step.get("targets")
                    if isinstance(targets, str):
                        targets = [targets]
                    if targets is None:
                        single = step.get("target")
                        targets = [single] if isinstance(single, str) else None
                    if isinstance(targets, list) and len(targets) > MAX_STEP_TARGETS:
                        raise ValueError(f"Use at most {MAX_STEP_TARGETS} targets per step.")
                    ident, request, needs = (step.get("id"), step.get("request"),
                                             step.get("needs") or [])
                    step.clear()
                    step.update({"id": ident, "targets": targets,
                                 "request": request, "needs": needs})
            cleaned = coding_jobs.validate_steps(steps, root=root)
            return {"steps": cleaned}
        except (KeyError, TypeError, ValueError, SyntaxError) as caught:
            error = str(caught)[:300]
    raise ValueError(f"Plan generation failed: {error}")

"""Review Talos source and append actionable requests to a local text queue."""

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from feature_targets import FILE, TARGETS, proposal_ledger
from agent import model_text
from local_runtime import check_ollama_model
from overnight_goal import free_review

ROOT = Path(__file__).resolve().parent


def append(stream, event):
    stream.seek(0, 2)
    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def system_idea(prompt, state):
    swift = shutil.which("swift")
    if sys.platform != "darwin" or swift is None:
        return None
    try:
        response = subprocess.run(
            [swift, "-module-cache-path", str(state / "swift-review-cache"),
             str(ROOT / "tools/local_feature_review.swift")],
            input=prompt[:6500], capture_output=True, text=True, timeout=90, check=True)
        answer = response.stdout.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return json.loads(answer)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def offline_idea(root, previous):
    """Use verifiable source observations when local inference is unavailable."""
    for filename in TARGETS:
        source = (root / filename).read_text()
        if not filename.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            has_limit = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and re.search(r"\bLIMIT \d+", n.value, re.I) for n in ast.walk(node))
            if has_limit and filename != "personal_index.py":
                title = f"Expose result coverage when {filename}:{node.name} reaches its query limit."
                if title not in previous:
                    return filename, {"feature": title, "acceptance": "With more fixture records than the limit, distinguish truncated results from a complete search."}
    return None, None


def queue_entries(stream):
    proposed, outcomes = proposal_ledger(stream, repair=True)
    return proposed, set(outcomes)


def review_excerpt(path, budget=10000):
    source = path.read_text()
    if len(source) <= budget or path.suffix != ".py":
        return source[:budget]
    lines = source.splitlines(keepends=True)
    functions = {n.name: n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not functions:
        return source[:budget]
    # Lead with the entrypoint that actually coordinates the module's helpers.
    calls = {name: {x.func.id for x in ast.walk(node) if isinstance(x, ast.Call) and
                    isinstance(x.func, ast.Name) and x.func.id in functions and x.func.id != name}
             for name, node in functions.items()}
    entry = max(functions, key=lambda name: (len(calls[name]), not name.startswith("_")))
    order = [entry, *(name for name in functions if name in calls[entry])]
    excerpt = "\n# … separate source excerpts …\n".join(
        "".join(lines[functions[name].lineno-1:functions[name].end_lineno]) for name in order)
    return excerpt[:budget]


def review(state, model, *, infer=None, complete=None, decline=None, cloud=False, pending=False):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / FILE
    with ExitStack() as files:
        if not (complete or pending or decline):
            reviewer = files.enter_context((state / "feature-review.lock").open("a"))
            try:
                fcntl.flock(reviewer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "review already active"
        stream = files.enter_context(open(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8"))
        fcntl.flock(stream, fcntl.LOCK_EX)
        proposals, outcomes = proposal_ledger(stream, repair=True)
        finished = set(outcomes)
        if pending:
            return "\n".join(json.dumps(item, ensure_ascii=False) for key, item in proposals.items()
                             if key not in finished) or "No pending requests."
        if complete or decline:
            ident, detail = complete or decline
            status, field = ("done", "verification") if complete else ("declined", "reason")
            detail = " ".join(detail.split())
            if ident not in proposals or not detail:
                raise ValueError(f"Supply an existing proposal ID and {field}.")
            if ident in outcomes:
                if outcomes[ident]["status"] != status:
                    raise ValueError(f"Proposal is already {outcomes[ident]['status']}.")
            else:
                append(stream, dict(status=status, id=ident, time=time.time(), **{field: detail[:500]}))
                if complete and proposals[ident].get("origin") == "local-qwen-source-review":
                    from talos_activity import record
                    record(state, "Model", f"Verified implementation of local proposal {ident} · {detail}")
            return "completion recorded" if complete else "decline recorded"
        if len(proposals.keys() - finished) >= 5:
            return "pending requests available"
        fcntl.flock(stream, fcntl.LOCK_UN)
        target = TARGETS[(len(proposals) + (int(time.time() // 3600) if proposals else 0)) % len(TARGETS)]
        path = ROOT / target
        if path.resolve() != path:
            raise ValueError("Expected a project source file.")
        source = review_excerpt(path)
        previous = [p["feature"] for p in list(proposals.values())[-20:]]
        prompt = ("Review this Talos source. Propose one new user-visible feature or concrete accuracy "
                  "improvement grounded in the code, and an observable acceptance test. Prefer better personal "
                  "recall, desktop actions, recognition or self-maintenance. Return JSON with strings "
                  "feature and acceptance. No tools. Previous ideas: " + json.dumps(previous)
                  + "\n" + target + "\n" + source)
        origin = "local-model"
        try:
            if infer is None:
                check_ollama_model(model)
                infer = model_text
            result = json.loads(infer([{"role": "user", "content": prompt}], model=model, max_tokens=350,
                                      schema={"type": "object", "required": ["feature", "acceptance"],
                                              "properties": {k: {"type": "string"} for k in ("feature", "acceptance")}}))
        except (RuntimeError, ValueError, OSError):
            origin = "apple-on-device-model"
            result = system_idea(prompt, state)
            if result is None and cloud:
                origin = "free-respan"
                try:
                    result = free_review("Propose one feature and its acceptance test for this Talos source. "
                                         "Return JSON strings feature and acceptance.\n" + target + "\n" + source,
                                         ("feature", "acceptance"))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    result = None
            if result is None:
                origin = "offline-source-check"
                target, result = offline_idea(ROOT, {p["feature"] for p in proposals.values()})
                if result is None:
                    return "no new single-file maintenance suggestions"
        if not isinstance(result, dict) or not all(isinstance(result.get(k), str) and
                5 <= len(result[k].strip()) <= 700 for k in ("feature","acceptance")):
            raise ValueError("Model returned an incomplete feature request.")
        title = " ".join(result["feature"].split())
        ident = hashlib.sha256(title.casefold().encode()).hexdigest()[:16]
        fcntl.flock(stream, fcntl.LOCK_EX)
        proposals, finished = queue_entries(stream)
        if len(proposals.keys() - finished) >= 5:
            return "pending requests available"
        if ident in proposals:
            return "duplicate proposal"
        append(stream, dict(status="proposed", id=ident, origin=origin, source=target, feature=title,
                            acceptance=result["acceptance"].strip(), time=time.time()))
        return "proposal queued"


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", type=Path, default=ROOT / "state")
    cli.add_argument("--model", default="qwen3:4b")
    action = cli.add_mutually_exclusive_group()
    action.add_argument("--pending", action="store_true")
    cli.add_argument("--free-respan", action="store_true")
    action.add_argument("--decline", nargs=2, metavar=("ID", "REASON"))
    action.add_argument("--complete", nargs=2, metavar=("ID", "VERIFICATION"))
    args = cli.parse_args()
    os.environ.setdefault("TALOS_OLLAMA_PORT", "11435")
    print(review(args.state, args.model, complete=args.complete, decline=args.decline, cloud=args.free_respan, pending=args.pending))

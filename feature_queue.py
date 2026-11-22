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

from feature_targets import FILE, TARGETS, change_events
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
    proposals, finished = {}, set()
    for event in change_events(stream, repair=True):
        if event["status"] == "proposed":
            proposals[event["id"]] = event
        elif event["status"] == "done":
            finished.add(event["id"])
    return proposals, finished


def review(state, model, *, infer=None, complete=None, cloud=False, pending=False):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / FILE
    with ExitStack() as files:
        if not (complete or pending):
            reviewer = files.enter_context((state / "feature-review.lock").open("a"))
            try:
                fcntl.flock(reviewer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "review already active"
        stream = files.enter_context(open(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8"))
        fcntl.flock(stream, fcntl.LOCK_EX)
        proposals, finished = queue_entries(stream)
        if pending:
            return "\n".join(json.dumps(item, ensure_ascii=False) for key, item in proposals.items()
                              if key not in finished) or "No pending requests."
        if complete:
            ident, evidence = complete
            if ident not in proposals or not evidence.strip():
                raise ValueError("Supply an existing proposal ID and verification result.")
            if ident not in finished:
                append(stream, dict(status="done", id=ident, verification=evidence[:500], time=time.time()))
            return "completion recorded"
        if len(proposals.keys() - finished) >= 5:
            return "pending requests available"
        fcntl.flock(stream, fcntl.LOCK_UN)
        target = TARGETS[(len(proposals) + (int(time.time() // 3600) if proposals else 0)) % len(TARGETS)]
        path = ROOT / target
        if path.resolve() != path:
            raise ValueError("Expected a project source file.")
        source = path.read_text()
        previous = [p["feature"] for p in list(proposals.values())[-20:]]
        prompt = ("Review this Talos source. Propose one new user-visible feature or concrete accuracy "
                  "improvement grounded in the code, and an observable acceptance test. Prefer better personal "
                  "recall, desktop actions, recognition or self-maintenance. Return JSON with strings "
                  "feature and acceptance. No tools. Previous ideas: " + json.dumps(previous)
                  + "\n" + target + "\n" + source[:10000])
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
                                         "Return JSON strings feature and acceptance.\n" + target + "\n" + source[:10000],
                                         ("feature", "acceptance"))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    result = None
            if result is None:
                origin = "offline-source-check"
                target, result = offline_idea(ROOT, {p["feature"] for p in proposals.values()})
                if result is None:
                    return "local model unavailable; no new offline observations"
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
    cli.add_argument("--pending", action="store_true")
    cli.add_argument("--free-respan", action="store_true")
    cli.add_argument("--complete", nargs=2, metavar=("ID", "VERIFICATION"))
    args = cli.parse_args()
    os.environ.setdefault("TALOS_OLLAMA_PORT", "11435")
    print(review(args.state, args.model, complete=args.complete, cloud=args.free_respan, pending=args.pending))

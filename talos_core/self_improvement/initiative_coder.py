"""Turn a decomposed initiative leaf into a verified source patch.

Section patches start from upstream evidence. Initiatives start from a plan
leaf. This worker bridges the gap: it reads the leaf's target file, asks a
local/cloud model for a minimal before/after plus regression, uses the existing
checked-patch builder, verifies in the sandbox, and applies only when verified.

Every run leaves a receipt with hashes, tests run, verification stage and
before/after state, so autonomy is auditable rather than a claim.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from talos_core.self_improvement import section_patches
from talos_core.self_improvement.feature_targets import FILE as PROPOSALS_FILE
from talos_core.self_improvement.feature_targets import proposal_ledger

LOCK = "initiative-coder.lock"
DB = "initiative-coder.sqlite3"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


AUTO_ORIGINS = ("initiative:operator-goal", "initiative:health", "initiative:model")


def _load_proposals(state: Path, origins: tuple[str, ...] = AUTO_ORIGINS) -> dict[str, dict]:
    path = state / PROPOSALS_FILE
    if not path.is_file():
        return {}
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        proposed, finished = proposal_ledger(stream)
    return {key: item for key, item in proposed.items()
            if key not in finished and str(item.get("origin", "")) in origins}


def _append_outcome(state: Path, ident: str, status: str, detail: str) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "feature-review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (state / PROPOSALS_FILE).open("a", encoding="utf-8") as stream:
            os.chmod(stream.fileno(), 0o600)
            stream.write(json.dumps({"status": status, "id": ident, "time": time.time(),
                                     ("verification" if status == "done" else "reason"):
                                     detail[:800]}, ensure_ascii=False) + "\n")


def _leaf(plan_path: Path) -> dict | None:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    nodes = plan.get("nodes", {})
    for ident in plan.get("leaves", ()):
        node = nodes.get(ident, {})
        files = [str(name) for name in node.get("files", ()) if str(name).endswith(".py")]
        if files:
            return {"id": ident, "title": node.get("title", ""),
                    "files": files, "acceptance": node.get("acceptance", "")}
    return None


def local_infer(model: str):
    """Return a JSON-constrained local-model callable for one patch."""
    from agent import model_text

    schema = {
        "type": "object",
        "required": list(section_patches.PATCH_FIELDS),
        "properties": {key: {"type": "string"} for key in section_patches.PATCH_FIELDS},
        "additionalProperties": False,
    }

    def infer(prompt: str) -> str:
        return model_text(
            [{"role": "system", "content": "Implement the requested small change. Return only JSON."},
             {"role": "user", "content": prompt}],
            model=model, max_tokens=1000, schema=schema,
        )

    return infer


def _prompt(item: dict, leaf: dict, excerpt: str) -> str:
    return (
        section_patches.PATCH_INSTRUCTION
        + f"\nFEATURE: {item.get('feature', '')}\nACCEPTANCE: {leaf.get('acceptance') or item.get('acceptance', '')}\n"
        + f"\nTARGET: {leaf['files'][0]}\n"
        + "The regression_test must import every changed symbol from its real module path "
        + f"`{leaf['files'][0][:-3].replace('/', '.')}` (for `agent.py`, the module is `agent`).\n"
        + f"\nSOURCE EXCERPT:\n{excerpt[:9000]}"
    )


def _format_regression(text: str) -> str:
    """Normalize a generated unittest with Ruff when available."""
    executable = shutil.which("ruff")
    if not executable:
        return text
    try:
        completed = subprocess.run(
            [executable, "format", "-"], input=text, capture_output=True,
            text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return text
    return completed.stdout if completed.returncode == 0 and completed.stdout.strip() else text


def _supersede(state: Path, root: Path, proposals: dict[str, dict], applied: str,
               target: str) -> None:
    """Close other queued initiatives for the same target so they cannot regress it."""
    for other, item in proposals.items():
        if other == applied:
            continue
        plan_path = Path(str(item.get("plan_path") or ""))
        leaf = _leaf(plan_path) if plan_path.is_file() else None
        if leaf is None or leaf["files"][0] != target:
            continue
        _append_outcome(state, other, "declined",
                        f"superseded by {applied}; re-plan against the new source")
        generated = root / "tests" / f"test_section_{other}.py"
        try:
            if generated.is_file():
                generated.unlink()
        except OSError:
            pass


def _parse(raw) -> dict | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or set(raw) != set(section_patches.PATCH_FIELDS):
        return None
    if not all(isinstance(raw.get(key), str) and len(raw[key]) <= 12000
               for key in section_patches.PATCH_FIELDS):
        return None
    return {key: raw[key] for key in section_patches.PATCH_FIELDS}


def run_once(state: Path | str, root: Path | str, *, infer, apply: bool = True,
             limit: int = 1, origins: tuple[str, ...] = AUTO_ORIGINS) -> dict:
    """Draft and verify one initiative leaf. ``infer`` returns model JSON."""
    state, root = Path(state), Path(root)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / LOCK).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": False, "reason": "initiative coder already active"}
        proposals = _load_proposals(state, origins)
        if not proposals:
            return {"ok": False, "reason": "no queued initiative"}
        results = []
        ordered = sorted(
            proposals.items(),
            key=lambda pair: (0 if pair[1].get("origin") == "initiative:operator-goal" else 1,
                              -float(pair[1].get("time", 0))),
        )
        for ident, item in ordered[:max(1, limit)]:
            plan_path = Path(str(item.get("plan_path") or ""))
            leaf = _leaf(plan_path) if plan_path.is_file() else None
            if leaf is None:
                _append_outcome(state, ident, "declined", "no planned source leaf")
                continue
            target = root / leaf["files"][0]
            if not target.is_file() or target.stat().st_size > 1_500_000:
                _append_outcome(state, ident, "declined", "target missing or oversized")
                continue
            original = target.read_text(encoding="utf-8")
            before_sha = _digest(target)
            raw = infer(_prompt(item, leaf, original))
            values = _parse(raw)
            if values is not None:
                values["regression_test"] = _format_regression(values["regression_test"])
            if values is None:
                _append_outcome(state, ident, "declined", "model returned no valid patch")
                continue
            item_for_patch = {"id": ident, "source": leaf["files"][0]}
            try:
                candidate = section_patches.checked_patch(
                    root, item_for_patch, original, original, values)
            except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
                _append_outcome(state, ident, "declined", f"patch rejected: {error}")
                continue
            if candidate is None:
                _append_outcome(state, ident, "declined", "model abstained")
                continue
            diff, regression, _test_path = candidate
            folder = state / "section-patches" / ident
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            (folder / "change.diff").write_text(diff, encoding="utf-8")
            (folder / "regression.py").write_text(regression, encoding="utf-8")
            receipt = {
                "id": ident,
                "origin": item.get("origin"),
                "plan": item.get("plan"),
                "target": leaf["files"][0],
                "before_sha256": before_sha,
                "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
                "created": time.time(),
                "applied": False,
            }
            if apply:
                outcome = section_patches.apply_verified_patch(
                    root, state, diff, extra_regression=regression)
                verification = outcome.get("verification", {})
                receipt.update({
                    "applied": outcome.get("applied", False),
                    "rolled_back": outcome.get("rolled_back", False),
                    "sandbox_verified": verification.get("ok", False),
                    "tests_run": verification.get("tests_run", []),
                    "verification_stage": verification.get("stage"),
                    "after_sha256": _digest(target),
                })
            else:
                verification = section_patches.verify_patch_in_sandbox(
                    root, diff, extra_regression=regression)
                receipt.update({
                    "sandbox_verified": verification.get("ok", False),
                    "tests_run": verification.get("tests_run", []),
                    "verification_stage": verification.get("stage"),
                })
            (folder / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            if receipt.get("applied"):
                _supersede(state, root, proposals, ident, leaf["files"][0])
                _append_outcome(state, ident, "done",
                                f"verified and applied {leaf['files'][0]} "
                                f"({', '.join(receipt.get('tests_run', [])) or 'syntax only'})")
            elif receipt.get("sandbox_verified"):
                _append_outcome(state, ident, "declined", "patch verified but apply disabled")
            else:
                _append_outcome(state, ident, "declined",
                                f"sandbox rejected at {receipt.get('verification_stage')}")
            results.append({"id": ident, "target": leaf["files"][0],
                            "receipt": str(folder / "receipt.json"), **receipt})
        return {"ok": any(item.get("applied") for item in results), "results": results}

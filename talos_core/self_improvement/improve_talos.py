"""Draft a checked patch for Talos without editing the running room assistant."""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from talos_core.memory.agent_memory import STATE_PATH, open_memory

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATTERNS = ("*.py", "talos_core/**/*.py", "mac/*.swift", "mac/Info.plist", "tests/test_*.py", "tests/*.swift",
                   "README.md", "pyproject.toml", "talos_core/scheduling/*.ts", "talos_core/scheduling/*.mjs")


def draft_patch(issue, *, model=None, cloud=False, root=ROOT):
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Install the Codex CLI to draft a patch.")
    with tempfile.TemporaryDirectory(prefix="talos-review-") as directory:
        snapshot = Path(directory)
        for pattern in SOURCE_PATTERNS:
            for source in root.glob(pattern):
                if source.resolve() != source or not source.is_file():
                    continue
                destination = snapshot / source.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        response = snapshot / "proposal.diff"
        command = [codex, "exec", "--sandbox", "read-only", "--ignore-user-config",
                   "--skip-git-repo-check", "--ephemeral", "-o", str(response)]
        environment = {k: v for k, v in os.environ.items() if k != "RESPAN_API_KEY"}
        if not cloud:
            port = int(os.environ.get("TALOS_OLLAMA_PORT", "11435"))
            environment["CODEX_OSS_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
            model = model or "qwen3:4b"
            command += ["--oss", "--local-provider", "ollama"]
        if model:
            command += ["--model", model]
        prompt = ("Inspect the Talos source in the working directory. Draft the smallest fix and a "
                  "regression test for the operator's issue. Return only a git unified diff using "
                  "a/ and b/ paths with accurate hunk line counts. Do not run the app or access "
                  "any files outside this source snapshot. Issue:\n" + issue)
        subprocess.run([*command, "-"], input=prompt, text=True, cwd=snapshot,
                       check=True, timeout=600, env=environment)
        patch = response.read_bytes()
        # Process data with git, never execute a candidate or apply it to the live app.
        subprocess.run(["git", "apply", "--check", "--whitespace=error", "-"],
                       cwd=snapshot, input=patch, check=True)
        outdir = root / "state" / "proposals"
        outdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="talos-", suffix=".patch", dir=outdir, delete=False) as out:
            out.write(patch)
            return Path(out.name)


def observed_issue(path=STATE_PATH):
    with closing(open_memory(path)) as db:
        row = db.execute("""SELECT stage, occurrences, worst_ms FROM room_slow_stages
            WHERE occurrences >= 2 ORDER BY occurrences DESC, worst_ms DESC LIMIT 1""").fetchone()
        if row is not None:
            return (f"Investigate slow {row['stage']} in native room mode. It crossed the latency "
                    f"threshold {row['occurrences']} times with a maximum of {row['worst_ms']} ms. "
                    "Provide a measurable regression check.")
        has_critiques = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_critiques'"
        ).fetchone()
        if has_critiques:
            critique = db.execute(
                """SELECT kind, COUNT(*) AS hits FROM turn_critiques
                   WHERE resolved IN (0, 1)
                   GROUP BY kind HAVING COUNT(*) >= 2
                   ORDER BY hits DESC, kind ASC LIMIT 1"""
            ).fetchone()
            if critique is not None:
                return (f"Investigate recurring {critique['kind']} turn critiques ({critique['hits']} occurrences) "
                        "and add a deterministic safeguard with a unit regression test.")
    from talos_core.self_improvement import self_diagnosis
    diag_db = Path(path).parent / self_diagnosis.DB
    if diag_db.is_file():
        with closing(self_diagnosis.connect(Path(path).parent)) as diag:
            incident = diag.execute(
                """SELECT area, kind, hits FROM incidents
                   WHERE status != 'worker recovered' AND hits >= 2
                   ORDER BY hits DESC, last_seen DESC LIMIT 1"""
            ).fetchone()
        if incident is not None:
            return (f"Investigate recurring {incident['area']} incident ({incident['kind']}, "
                    f"{incident['hits']} hits) and provide a focused fix with a unit test.")
    raise RuntimeError("No repeated slow stage recorded yet. Supply an issue to investigate.")


def run_improvement_cycle(state=None, *, root=ROOT, apply_patches=False):
    """Run one closed-loop self-improvement pass: incident promotion, policy tuning, and sandbox patch verification."""
    import json
    import time

    from talos_core.self_improvement import idle_lab, section_patches, self_diagnosis

    state_dir = Path(state) if state is not None else STATE_PATH.parent
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = state_dir / "agent.sqlite3"

    with closing(open_memory(db_path)) as db:
        promoted = self_diagnosis.promote_incidents_to_proposals(state_dir, db)
        policy_outcome = idle_lab.evaluate_policy_overrides(db, state_dir)

    verified_candidates = []
    patches_dir = state_dir / "section-patches"
    if patches_dir.is_dir():
        for folder in sorted(patches_dir.iterdir()):
            if not folder.is_dir() or folder.name.endswith(".tmp"):
                continue
            diff_path = folder / "change.diff"
            receipt_path = folder / "receipt.json"
            if not diff_path.is_file() or not receipt_path.is_file():
                continue
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if receipt.get("sandbox_verified") is not None and not apply_patches:
                continue
            regression_path = folder / "regression.py"
            extra_reg = regression_path.read_text(encoding="utf-8") if regression_path.is_file() else None
            diff_text = diff_path.read_text(encoding="utf-8")
            if apply_patches and not receipt.get("applied"):
                outcome = section_patches.apply_verified_patch(
                    root, state_dir, diff_text, extra_regression=extra_reg
                )
                verification = outcome["verification"]
                receipt["sandbox_verified"] = verification["ok"]
                receipt["regression_executed"] = verification["ok"]
                receipt["tests_run"] = verification.get("tests_run", [])
                receipt["applied"] = outcome["applied"]
                if outcome.get("backup_dir"):
                    receipt["backup_dir"] = str(outcome["backup_dir"])
            else:
                verification = section_patches.verify_patch_in_sandbox(
                    root, diff_text, extra_regression=extra_reg
                )
                receipt["sandbox_verified"] = verification["ok"]
                receipt["regression_executed"] = verification["ok"]
                receipt["tests_run"] = verification.get("tests_run", [])
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            verified_candidates.append(
                {
                    "folder": folder.name,
                    "ok": bool(receipt.get("sandbox_verified")),
                    "applied": bool(receipt.get("applied")),
                    "tests_run": receipt.get("tests_run", []),
                }
            )

    from talos_core.self_improvement.decomposition import plan_task
    plans = []
    for proposal in promoted:
        try:
            plan = plan_task(
                state_dir,
                proposal["feature"],
                proposal.get("acceptance", ""),
                acceptance=proposal.get("acceptance", ""),
                files=(proposal.get("source", ""),) if proposal.get("source") else (),
            )
        except (OSError, ValueError, KeyError):
            continue
        plans.append({"proposal": proposal["id"], "root": plan["root"],
                      "leaves": len(plan["leaves"]), "path": plan["path"]})
    summary = {
        "checked_at": time.time(),
        "proposals_promoted": [item["id"] for item in promoted],
        "plans": plans,
        "policy_action": policy_outcome.get("action", "none"),
        "candidates_verified": verified_candidates,
    }
    report_path = state_dir / "improvement-cycle.json"
    fd, tmp_name = tempfile.mkstemp(prefix=".improve-", dir=str(state_dir))
    try:
        os.chmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, report_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return summary


def main():
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", nargs="?", help="A specific reproducible problem or behavior to improve.")
    parser.add_argument("--cloud", action="store_true", help="Use the configured Codex cloud account.")
    parser.add_argument("--model", help="Select an installed local model or an available cloud model.")
    parser.add_argument("--cycle", action="store_true", help="Run one closed-loop self-improvement and sandbox verification pass.")
    parser.add_argument("--apply-verified", action="store_true", help="Apply sandbox-verified section patches with rollback backups.")
    args = parser.parse_args()
    try:
        if args.cycle:
            print(json.dumps(run_improvement_cycle(apply_patches=args.apply_verified)))
            return 0
        print(draft_patch(args.issue or observed_issue(), cloud=args.cloud, model=args.model))
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"No checked patch produced. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

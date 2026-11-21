"""Draft a checked patch for Talos without editing the running room assistant."""

import argparse
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from agent_memory import STATE_PATH, open_memory

ROOT = Path(__file__).resolve().parent
SOURCE_PATTERNS = ("*.py", "mac/*.swift", "mac/Info.plist", "tests/test_*.py", "tests/*.swift",
                   "README.md", "pyproject.toml")


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
                destination.parent.mkdir(exist_ok=True)
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
    if row is None:
        raise RuntimeError("No repeated slow stage recorded yet. Supply an issue to investigate.")
    return (f"Investigate slow {row['stage']} in native room mode. It crossed the latency "
            f"threshold {row['occurrences']} times with a maximum of {row['worst_ms']} ms. "
            "Provide a measurable regression check.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", nargs="?", help="A specific reproducible problem or behavior to improve.")
    parser.add_argument("--cloud", action="store_true", help="Use the configured Codex cloud account.")
    parser.add_argument("--model", help="Select an installed local model or an available cloud model.")
    args = parser.parse_args()
    try:
        print(draft_patch(args.issue or observed_issue(), cloud=args.cloud, model=args.model))
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"No checked patch produced. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

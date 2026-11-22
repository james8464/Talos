"""Inventory the local reference trees and queue source-backed console parity work."""

import argparse
import ast
from collections import Counter
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from feature_queue import append, queue_entries
from feature_targets import FILE

ROOT = Path(__file__).resolve().parent
REGISTRIES = {
    "Hermes": ("hermes-src", "hermes_cli/commands.py"),
    "OpenClaw": ("openclaw-src", "src/auto-reply/commands-registry.shared.ts"),
}
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".build", ".ruff_cache"}
# Start with user-visible console behaviors that can be replayed locally.
REPLAYS = {
    "title": "Set a title, restart the console, resume by title and verify that history and session ID are unchanged.",
    "export-session": "Export the current session as HTML; verify roles, chronological ordering and literal rendering of markup.",
    "commands": "Open the command browser and verify its entries dispatch to the documented commands.",
    "context": "Inspect a populated and empty session and verify the reported context categories against the actual model input.",
    "whoami": "Check the displayed identity and access level in each supported console mode.",
}


def inventory(root, state):
    """Hash regular files without following links or loading upstream code."""
    trees = {"Talos": root, **{key: state / folder for key, (folder, _) in REGISTRIES.items()}}
    if any(not tree.is_dir() or tree.is_symlink() for tree in trees.values()):
        raise ValueError("Both local upstream reference trees are required.")
    result = Counter()
    with closing(sqlite3.connect(state / "upstream-inventory.sqlite3")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS files(tree TEXT,path TEXT,bytes INTEGER,sha256 TEXT,"
                   "PRIMARY KEY(tree,path)) WITHOUT ROWID")
        db.execute("DELETE FROM files")
        for name, tree in trees.items():
            excluded = SKIP | ({"state", "models", "graphify-out", "generated_images"} if name == "Talos" else set())
            for parent, dirs, names in os.walk(tree, onerror=raise_walk_error):
                dirs[:] = [d for d in dirs if d not in excluded and not (Path(parent) / d).is_symlink()]
                for filename in names:
                    path = Path(parent) / filename
                    if path.is_symlink() or not path.is_file() or filename == ".DS_Store":
                        continue
                    with path.open("rb") as stream:
                        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                        size = os.fstat(stream.fileno()).st_size
                    db.execute("INSERT INTO files VALUES (?,?,?,?)",
                               (name, path.relative_to(tree).as_posix(), size, checksum))
                    result[name] += 1
    return dict(result)


def raise_walk_error(error):
    raise error


def registry(tree, text):
    if tree == "Hermes":
        for call in ast.walk(ast.parse(text)):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "CommandDef" and len(call.args) >= 2):
                name, description = call.args[:2]
                if all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in (name, description)):
                    yield name.value, description.value, call.lineno
    else:
        for match in re.finditer(r'defineBuiltinCommand\(\s*"([\w-]+)",\s*"([^"\n]+)"', text):
            yield match[1], match[2], text.count("\n", 0, match.start()) + 1


def command_names(root):
    tree = ast.parse((root / "agent.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "interactive_chat")
    return {node.value.value[1:] for node in ast.walk(function) if isinstance(node, ast.MatchValue)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            and node.value.value.startswith("/")}


def parity_status(root, state, project=None):
    """Read only literal command declarations and local dispatch names."""
    present = command_names(root)
    lines = ["Console command names in the pinned sources · behavioral parity requires separate tests"]
    for name, (folder, rel) in REGISTRIES.items():
        if project and project.lower() != name.lower():
            continue
        try:
            text = (state / folder / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            lines.append(f"{name} · reference registry unavailable")
            continue
        upstream = {command for command, _, _ in registry(name, text)}
        missing = sorted(upstream - present)
        lines.append(f"{name} · {len(upstream & present)}/{len(upstream)} names dispatch locally · {len(missing)} missing")
        if project and missing:
            lines.append("Missing · " + ", ".join("/" + command for command in missing))
    return "\n".join(lines)


def propose(root, state):
    present = command_names(root)
    candidates = []
    for project, (folder, rel) in REGISTRIES.items():
        source = state / folder / rel
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        sha = hashlib.sha256(text.encode()).hexdigest()
        for name, description, line in registry(project, text):
            if name not in present:
                priority = list(REPLAYS).index(name) if name in REPLAYS else len(REPLAYS)
                candidates.append((priority, name, project, rel, line, sha, description))
    with open(os.open(state / FILE, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        proposed, finished = queue_entries(stream)
        if len(proposed.keys() - finished) >= 5:
            return "Pending requests available"
        for _, name, project, rel, line, sha, description in sorted(candidates):
            ident = hashlib.sha256(f"console:{project}:{name}:{sha}".encode()).hexdigest()[:16]
            if ident in proposed:
                continue
            event = dict(status="proposed", id=ident, origin="upstream-source-comparison",
                         source="agent.py", feature=f"Support /{name} in the Talos console. {description}",
                         upstream=project, reference=f"{rel}:{line}", registry_sha256=sha,
                         acceptance=REPLAYS.get(name, f"Replay /{name} against the pinned {project} implementation and "
                                                "Talos. Compare its documented inputs, outcomes and failure behavior."),
                         time=time.time())
            append(stream, event)
            return json.dumps(event, ensure_ascii=False)
    return "No new console proposal"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true", help="Rebuild the full local file inventory.")
    parser.add_argument("--compare", action="store_true", help="Ask the local model to compare one source excerpt.")
    parser.add_argument("--status", action="store_true", help="Show live console command name coverage.")
    parser.add_argument("--model", default="qwen3:4b")
    args = parser.parse_args()
    state = ROOT / "state"
    if args.status:
        print(parity_status(ROOT, state))
    elif args.compare:
        from upstream_comparison import compare
        os.environ.setdefault("TALOS_OLLAMA_PORT", "11435")
        try:
            print(compare(ROOT, state, args.model))
        except (ValueError, RuntimeError, OSError) as error:
            parser.exit(1, f"Source comparison deferred: {error}\n")
    else:
        print(json.dumps(inventory(ROOT, state)) if args.inventory else propose(ROOT, state))

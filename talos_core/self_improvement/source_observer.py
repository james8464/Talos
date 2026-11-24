"""Watch Talos source changes and record content-free check receipts.

The observer is a thin watchdog around the pieces that already exist:

* source files are read with the same bounded, no-symlink reader as
  :mod:`talos_core.self_improvement.self_repair`;
* lint and regression checks reuse :mod:`talos_core.self_improvement.health`;
* failures become diagnosis incidents through ``health.report_failures`` so the
  existing self-repair loop can pick them up;
* the worker uses the same lock convention as the other supervised workers;
* capability coverage is refreshed occasionally with
  :mod:`talos_core.self_improvement.capability_audit`.

Receipts contain only relative paths, digests, check names, and pass/fail
summaries. Source text, test output, and private state are never recorded.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from talos_core.cognition.conversation_activity import conversation_busy
from talos_core.runtime.talos_activity import lock_state, record
from talos_core.self_improvement import health
from talos_core.self_improvement.repair_contracts import digest, read_regular
from talos_core.self_improvement.self_repair import load, save

ROOT = Path(__file__).resolve().parents[2]
STORE = "source-observer"
WATCH_LOCK = "source-observer-watch"
POLL_SECONDS = 20
DEBOUNCE_SECONDS = 8
MAX_SOURCE_BYTES = 2_000_000
MAX_TEST_BYTES = 512_000
MAX_TARGETED_CHANGES = 8
MAX_TARGETED_TESTS = 6
TARGETED_TIMEOUT = 300
FULL_TIMEOUT = 900
AUDIT_INTERVAL = 6 * 3600
EXCLUDED = {
    ".git", ".venv", "venv", "state", "models", "generated_images",
    "graphify-out", "__pycache__", ".ruff_cache",
}
SOURCE_GLOBS = ("talos_core/**/*.py", "tests/**/*.py", "tools/**/*.py")
SOURCE_FILES = ("agent.py", "run_talos.py", "pyproject.toml")


def _store(state: Path) -> Path:
    folder = state / STORE
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve() != folder.absolute():
        raise ValueError("Unexpected observer state path")
    return folder


def _relative(path: Path, root: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if set(relative.parts) & EXCLUDED:
        return None
    return relative.as_posix()


def source_files(root: Path) -> list[Path]:
    """Return ordinary Python/config files under the watched source trees."""
    found: dict[str, Path] = {}
    for pattern in SOURCE_GLOBS:
        for path in root.glob(pattern):
            relative = _relative(path, root)
            if relative and not path.is_symlink() and path.is_file():
                found[relative] = path
    for name in SOURCE_FILES:
        path = root / name
        if path.is_file() and not path.is_symlink():
            found[name] = path
    return [found[name] for name in sorted(found)]


def snapshot(root: Path) -> dict[str, str | None]:
    """Fingerprint watched files by relative path. No file content is retained."""
    root = Path(root).resolve()
    values: dict[str, str | None] = {}
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            values[relative] = digest(read_regular(path, MAX_SOURCE_BYTES))
        except (OSError, ValueError):
            # A file that vanished or changed type still participates in the
            # revision so the next observation reports it as a change.
            values[relative] = None
    return values


def revision(fingerprint: dict[str, str | None]) -> str:
    payload = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    return digest(payload)


def diff(previous: dict[str, str | None], current: dict[str, str | None]) -> dict[str, list[str]]:
    """Return added, modified, and removed relative paths."""
    return {
        "added": sorted(set(current) - set(previous)),
        "modified": sorted(path for path in set(previous) & set(current)
                           if previous[path] != current[path]),
        "removed": sorted(set(previous) - set(current)),
    }


def changed_paths(changes: dict[str, list[str]]) -> list[str]:
    return sorted({path for paths in changes.values() for path in paths})


def _test_text(path: Path) -> str:
    try:
        return read_regular(path, MAX_TEST_BYTES).decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return ""


def tests_for(path: str, root: Path) -> list[str]:
    """Pick existing regression files that name the changed module.

    Exact naming wins; otherwise tests that mention the module token are used.
    An empty result means the caller should run the full suite.
    """
    root = Path(root).resolve()
    tests = root / "tests"
    if not tests.is_dir():
        return []
    name = Path(path).name
    if path.startswith("tests/"):
        return [path] if (root / path).is_file() else []
    if not name.endswith(".py"):
        return []
    stem = Path(name).stem
    if not stem or stem == "__init__":
        return []
    exact = tests / f"test_{stem}.py"
    if exact.is_file():
        return [exact.relative_to(root).as_posix()]
    named = sorted(
        candidate.relative_to(root).as_posix()
        for candidate in tests.glob(f"test_*{stem}*.py")
        if candidate.is_file() and not candidate.is_symlink()
    )
    if named:
        return named
    pattern = re.compile(rf"\b{re.escape(stem)}\b")
    matched = []
    for candidate in sorted(tests.glob("test_*.py")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if pattern.search(_test_text(candidate)):
            matched.append(candidate.relative_to(root).as_posix())
            if len(matched) >= MAX_TARGETED_TESTS:
                break
    return matched


def select_tests(changes: dict[str, list[str]], root: Path) -> tuple[str, list[str]]:
    """Return ``("targeted"|"full", test files)`` for the change set."""
    paths = changed_paths(changes)
    if not paths or len(paths) > MAX_TARGETED_CHANGES:
        return "full", []
    if any(path.startswith("tests/") and path in changes["removed"] for path in paths):
        return "full", []
    targets: set[str] = set()
    for path in paths:
        found = tests_for(path, root)
        if not found:
            return "full", []
        targets.update(found)
    if not targets:
        return "full", []
    return "targeted", sorted(targets)[:MAX_TARGETED_TESTS]


def _run_targeted(root: Path, files: list[str], runner: Callable,
                  timeout: int) -> health.Check:
    started = time.monotonic()
    failed = []
    for name in files:
        command = [sys.executable, "-m", "unittest", "discover",
                   "-s", "tests", "-p", Path(name).name]
        try:
            completed = runner(command, cwd=root, capture_output=True,
                               text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            failed.append(f"{name}: {type(error).__name__}")
            continue
        if completed.returncode:
            tail = [line for line in (completed.stderr or "").splitlines() if line.strip()]
            summary = next((line for line in reversed(tail)
                            if line.startswith(("OK", "FAILED"))), "")
            failed.append(f"{name}: {summary or f'exit {completed.returncode}'}")
    detail = f"{len(files)} targeted test file(s)"
    if failed:
        detail += " · " + "; ".join(failed[:4])
    else:
        detail += " · passed"
    return health.Check("tests", not failed, detail,
                        int((time.monotonic() - started) * 1000))


def run_checks(root: Path, changes: dict[str, list[str]], *, runner: Callable = subprocess.run,
               timeout: int = TARGETED_TIMEOUT, full_timeout: int = FULL_TIMEOUT
               ) -> tuple[list[health.Check], str, list[str]]:
    """Run lint plus targeted or full regressions. Check items stay out of receipts."""
    lint = health.check_ruff(root, runner=runner)
    lint.items = []
    mode, files = select_tests(changes, root)
    if mode == "targeted":
        tests = _run_targeted(root, files, runner, timeout)
    else:
        tests = health.check_tests(root, timeout=full_timeout, runner=runner)
        tests.items = []
    return [lint, tests], mode, files


def check_dict(check: health.Check) -> dict:
    return {"name": check.name, "ok": check.ok, "detail": check.detail}


def audit_capabilities(root: Path, state: Path) -> dict:
    """Refresh the existing capability report; failures never block a receipt."""
    try:
        from talos_core.self_improvement import capability_audit

        return capability_audit.report(root, state)["summary"]
    except (OSError, ValueError, RuntimeError, ImportError, AttributeError, TypeError):
        return {}


def receipt(previous_revision: str, fingerprint: dict[str, str | None],
            changes: dict[str, list[str]], checks: list[health.Check], *,
            mode: str, files: list[str], now: float,
            capabilities: dict | None = None) -> dict:
    ident = revision(fingerprint)[:24]
    payload = {
        "id": ident,
        "revision": revision(fingerprint),
        "previous_revision": previous_revision,
        "checked_at": now,
        "changes": changes,
        "change_count": len(changed_paths(changes)),
        "checks": [check_dict(check) for check in checks],
        "tests": {"mode": mode, "files": files},
        "ok": all(check.ok for check in checks),
    }
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def _surface(receipt_value: dict, state: Path) -> None:
    if receipt_value["ok"]:
        return
    report = {"checks": [{"name": item["name"], "ok": item["ok"],
                          "detail": item["detail"], "items": []}
                         for item in receipt_value["checks"]]}
    try:
        health.report_failures(report, state)
    except (OSError, ValueError, RuntimeError):
        pass


def _record(receipt_value: dict, state: Path) -> None:
    paths = changed_paths(receipt_value["changes"])
    detail = "passed" if receipt_value["ok"] else "needs repair"
    failed = [item["name"] for item in receipt_value["checks"] if not item["ok"]]
    message = (f"{len(paths)} source change(s) · {detail}"
               + (f" ({', '.join(failed)})" if failed else "")
               + f" · receipt {receipt_value['id']}")
    record(state, "Source observer", message)


def _write_receipt(store: Path, value: dict) -> Path:
    folder = store / "receipts"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{value['id']}.json"
    if not path.is_file():
        save(path, value)
    save(store / "latest.json", value)
    return path


def observe_once(root: Path, state: Path, *, clock: Callable[[], float] = time.time,
                 runner: Callable = subprocess.run,
                 busy: Callable[[Path], bool] = conversation_busy,
                 audit: Callable[[Path, Path], dict] = audit_capabilities,
                 debounce: float = DEBOUNCE_SECONDS) -> dict | str | None:
    """Observe one revision. Returns a receipt, a status word, or None when idle."""
    root, state = Path(root).resolve(), Path(state).resolve()
    store = _store(state)
    with (store / "turn.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        status = load(store / "status.json")
        current = snapshot(root)
        current_revision = revision(current)
        if not status:
            save(store / "status.json", {
                "revision": current_revision,
                "fingerprint": current,
                "checked_at": clock(),
                "last_audit": 0,
                "receipt": None,
            })
            save(store / "pending.json", {})
            record(state, "Source observer",
                   f"Baseline recorded · {len(current)} source file(s)")
            return "baseline"
        previous = status.get("fingerprint") or {}
        if current == previous:
            if load(store / "pending.json"):
                save(store / "pending.json", {})
            return None
        pending = load(store / "pending.json")
        if pending.get("revision") != current_revision:
            pending = {"revision": current_revision, "first_seen": clock()}
            save(store / "pending.json", pending)
        if busy(state):
            return "deferred"
        now = clock()
        if now - pending.get("first_seen", now) < debounce:
            return "settling"
        changes = diff(previous, current)
        checks, mode, files = run_checks(root, changes, runner=runner)
        capabilities = {}
        if all(check.ok for check in checks) and now - status.get("last_audit", 0) >= AUDIT_INTERVAL:
            capabilities = audit(root, state) or {}
        value = receipt(status.get("revision", ""), current, changes, checks,
                        mode=mode, files=files, now=now, capabilities=capabilities)
        _write_receipt(store, value)
        status.update({
            "revision": value["revision"],
            "fingerprint": current,
            "checked_at": now,
            "receipt": value["id"],
        })
        if capabilities:
            status["last_audit"] = now
        save(store / "status.json", status)
        save(store / "pending.json", {})
        _surface(value, state)
        _record(value, state)
        return value


def summary(state: Path) -> list[str]:
    """Read-only status used by the activity viewer and CLI helpers."""
    state = Path(state).resolve()
    store = state / STORE
    try:
        status = load(store / "status.json")
        latest = load(store / "latest.json")
    except (OSError, ValueError):
        return ["Source observer · ledger temporarily unavailable"]
    if not status:
        return ["Source observer · no baseline yet"]
    watch = lock_state(state / f"{WATCH_LOCK}.lock")
    if latest:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(latest.get("checked_at", 0)))
        outcome = "passed" if latest.get("ok") else "needs repair"
        return [f"Source observer · {watch} · last change {when} · {outcome}"
                f" · {latest.get('change_count', 0)} file(s)"]
    return [f"Source observer · {watch} · baseline · {len(status.get('fingerprint') or {})} file(s)"]


def watch(root: Path, state: Path) -> None:
    state = Path(state).resolve()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / f"{WATCH_LOCK}.lock").open("a") as leader:
        try:
            fcntl.flock(leader, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            try:
                outcome = observe_once(root, state)
                if isinstance(outcome, dict):
                    print(f"{outcome['id']} · {'passed' if outcome['ok'] else 'needs repair'}",
                          flush=True)
            except Exception as error:
                record(state, "Source observer",
                       f"Observation deferred ({type(error).__name__})")
            time.sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    state = args.state or args.root / "state"
    try:
        if args.watch:
            watch(args.root, state)
            return 0
        outcome = observe_once(args.root, state)
        if isinstance(outcome, dict):
            print(json.dumps(outcome, indent=2, sort_keys=True))
        else:
            print(outcome or "No source change")
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

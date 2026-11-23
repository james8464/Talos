"""Self-checks for Talos's own source tree.

Backs the ``/repair`` and scheduled maintenance paths with a single report:

* import graph: every ``talos_core`` import resolves to a real module;
* syntax: every Python file compiles;
* lint: optional Ruff scan with a safe auto-fix;
* regression: optional full unittest suite.

The report is written atomically to ``state/health.json`` so the room UI and
maintenance workers can present the last known state without re-running work.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "state", "graphify-out", "__pycache__", "models",
             "generated_images", ".ruff_cache"}
DEFAULT_TIMEOUT = 900


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    ms: int = 0
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if not (set(path.relative_to(root).parts) & SKIP_DIRS)
    )


def _module_exists(root: Path, module: str) -> bool:
    parts = module.split(".")
    base = root.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def check_imports(root: Path) -> Check:
    """Resolve every intra-project import without executing any module."""
    started = time.monotonic()
    broken: list[str] = []
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if ((module == "talos_core" or module.startswith("talos_core."))
                        and not _module_exists(root, module)):
                    broken.append(f"{path.relative_to(root)}:{node.lineno} {module}")
    return Check("imports", not broken,
                 f"{len(broken)} unresolved import(s)" if broken else "all project imports resolve",
                 int((time.monotonic() - started) * 1000), broken[:40])


def check_syntax(root: Path) -> Check:
    started = time.monotonic()
    failures: list[str] = []
    files = _python_files(root)
    for path in files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError, ValueError) as error:
            failures.append(f"{path.relative_to(root)}: {type(error).__name__}: {error}")
    return Check("syntax", not failures,
                 f"{len(failures)} file(s) failed to compile" if failures else f"{len(files)} files compile",
                 int((time.monotonic() - started) * 1000), failures[:20])


def check_ruff(root: Path, *, fix: bool = False,
               runner: Callable = subprocess.run) -> Check:
    started = time.monotonic()
    executable = shutil.which("ruff")
    if not executable:
        return Check("ruff", True, "ruff not installed; skipped")
    command = [executable, "check", "--output-format=json", "."]
    if fix:
        command.insert(2, "--fix")
    try:
        completed = runner(command, cwd=root, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return Check("ruff", False, f"{type(error).__name__}: {error}",
                     int((time.monotonic() - started) * 1000))
    findings: list[str] = []
    payload = None
    try:
        payload = json.loads(completed.stdout or "[]")
        for item in payload[:40]:
            findings.append(
                f"{item.get('filename', '?')}:{item.get('location', {}).get('row', '?')} "
                f"{item.get('code', '?')} {item.get('message', '')}"
            )
    except (TypeError, ValueError):
        findings = [line for line in (completed.stdout or "").splitlines()[:40] if line.strip()]
    count = len(payload) if isinstance(payload, list) else len(findings)
    ok = completed.returncode == 0
    return Check("ruff", ok, f"{count} lint finding(s)", int((time.monotonic() - started) * 1000), findings)


def check_tests(root: Path, *, timeout: int = DEFAULT_TIMEOUT,
                runner: Callable = subprocess.run) -> Check:
    started = time.monotonic()
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
    try:
        completed = runner(command, cwd=root, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Check("tests", False, f"timed out after {timeout}s",
                     int((time.monotonic() - started) * 1000))
    except (OSError, subprocess.SubprocessError) as error:
        return Check("tests", False, f"{type(error).__name__}: {error}",
                     int((time.monotonic() - started) * 1000))
    tail = [line for line in (completed.stderr or "").splitlines() if line.strip()][-12:]
    ok = completed.returncode == 0
    summary = next((line for line in reversed(tail) if line.startswith(("OK", "FAILED"))), "")
    return Check("tests", ok, summary or f"exit {completed.returncode}",
                 int((time.monotonic() - started) * 1000), tail)


def health(root: Path | str, state: Path | str, *, quick: bool = False,
           lint: bool = True, tests: bool = True, fix: bool = False,
           timeout: int = DEFAULT_TIMEOUT,
           runner: Callable = subprocess.run) -> dict:
    root, state = Path(root).resolve(), Path(state).resolve()
    checks = [check_syntax(root), check_imports(root)]
    if lint:
        if fix:
            fixed = check_ruff(root, fix=True, runner=runner)
            fixed.name = "ruff fix"
            checks.append(fixed)
        checks.append(check_ruff(root, runner=runner))
    if tests and not quick:
        checks.append(check_tests(root, timeout=timeout, runner=runner))
    report = {
        "checked_at": time.time(),
        "root": str(root),
        "ok": all(check.ok for check in checks),
        "checks": [check.to_dict() for check in checks],
        "mode": "quick" if quick else "full",
    }
    save_report(report, state)
    return report


def save_report(report: dict, state: Path) -> Path:
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / "health.json"
    fd, temporary = tempfile.mkstemp(prefix=".health-", dir=state)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def summary(state: Path) -> str:
    path = Path(state) / "health.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "No health report yet."
    checks = ", ".join(f"{item['name']} {'ok' if item['ok'] else 'FAILED'}" for item in report["checks"])
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(report["checked_at"]))
    return f"Talos health {when} · {'healthy' if report['ok'] else 'attention needed'} · {checks}"


def report_failures(report: dict, state: Path) -> list[int]:
    """Turn each failed check into a diagnosis incident for the repair loop."""
    from talos_core.self_improvement import self_diagnosis

    incidents = []
    for line in failures(report):
        try:
            incidents.append(self_diagnosis.report(state, "Self-check", line[:800]))
        except (OSError, ValueError):
            continue
    return incidents


def failures(report: dict) -> Iterable[str]:
    for check in report["checks"]:
        if not check["ok"]:
            yield from check.get("items", ()) or (f"{check['name']}: {check['detail']}",)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check Talos's own source tree.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--quick", action="store_true", help="Skip the regression suite.")
    parser.add_argument("--no-lint", action="store_true")
    parser.add_argument("--tests", action="store_true", help="Run the suite even in quick mode.")
    parser.add_argument("--fix", action="store_true", help="Apply safe Ruff fixes before reporting.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    state = args.state or args.root / "state"
    report = health(args.root, state, quick=args.quick, lint=not args.no_lint,
                    tests=args.tests or not args.quick, fix=args.fix)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary(state))
        for line in failures(report):
            print("  " + line)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

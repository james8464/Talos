"""Unified command line for Talos.

Run ``talos --help`` from a checkout (``python -m talos_core.cli``) or after
``pip install -e .``. The CLI keeps entry points discoverable while the
implementation stays inside the ``talos_core`` packages.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"


def _doctor(args) -> int:
    from talos_core.self_improvement import health

    report = health.health(
        args.root, args.state or args.root / "state", quick=args.quick,
        lint=not args.no_lint, tests=not args.quick, fix=args.fix,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(health.summary(args.state or args.root / "state"))
        for line in health.failures(report):
            print("  " + line)
    return 0 if report["ok"] else 1


def _decompose(args) -> int:
    from talos_core.self_improvement import decomposition

    manager = None
    if args.model:
        from agent import model_text
        from talos_core.runtime.local_runtime import check_ollama_model

        check_ollama_model(args.model)

        def infer(prompt: str) -> str:
            return model_text(
                [{"role": "system", "content": prompt}], model=args.model, max_tokens=500
            )

        manager = decomposition.ModelManager(infer)
    plan = decomposition.plan_task(
        args.state, args.task, args.detail, files=args.file,
        manager=manager, max_depth=args.max_depth,
    )
    print(json.dumps(plan, indent=2, sort_keys=True) if args.json else decomposition.render(plan))
    if not args.json:
        print(f"Plan saved to {plan['path']}")
    return 0


def _status(args) -> int:
    from talos_core.mac import computer_use
    from talos_core.self_improvement import health

    state = args.state or ROOT / "state"
    print(f"Talos {VERSION} · {ROOT}")
    print(health.summary(state))
    permissions = computer_use.permissions()
    print("Mac access · Accessibility {accessibility} · Screen Recording {screen_recording}".format(**permissions))
    graph = ROOT / "graphify-out" / "graph.json"
    print(f"Source graph · {'present' if graph.is_file() else 'missing (run graphify extract . --code-only)'}")
    return 0


def _tests(args) -> int:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
    if args.pattern:
        command[-1] = args.pattern
    return subprocess.call(command, cwd=args.root)


def _improve(args) -> int:
    from talos_core.self_improvement import improve_talos

    sys.argv = ["talos improve", *args.rest]
    return improve_talos.main()


def _computer(args) -> int:
    from talos_core.mac import computer_use

    sys.argv = ["talos computer", "--state", str(args.state), *args.rest]
    return computer_use._cli()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="talos", description=__doc__)
    parser.add_argument("--version", action="version", version=f"talos {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Check imports, syntax, lint and regression tests.")
    doctor.add_argument("--root", type=Path, default=ROOT)
    doctor.add_argument("--state", type=Path)
    doctor.add_argument("--quick", action="store_true")
    doctor.add_argument("--no-lint", action="store_true")
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=_doctor)

    decompose = commands.add_parser("decompose", help="Split a task into routable subtasks.")
    decompose.add_argument("task")
    decompose.add_argument("--detail", default="")
    decompose.add_argument("--file", action="append", default=[])
    decompose.add_argument("--state", type=Path, default=ROOT / "state")
    decompose.add_argument("--max-depth", type=int, default=5)
    decompose.add_argument("--model", help="Use a local model as the engineering manager.")
    decompose.add_argument("--json", action="store_true")
    decompose.set_defaults(func=_decompose)

    status = commands.add_parser("status", help="Show health, Mac permissions and graph state.")
    status.add_argument("--state", type=Path)
    status.set_defaults(func=_status)

    tests = commands.add_parser("test", help="Run the regression suite.")
    tests.add_argument("--root", type=Path, default=ROOT)
    tests.add_argument("--pattern", default="test_*.py")
    tests.set_defaults(func=_tests)

    improve = commands.add_parser("improve", help="Run the self-improvement cycle.")
    improve.add_argument("rest", nargs=argparse.REMAINDER)
    improve.set_defaults(func=_improve)

    computer = commands.add_parser("computer", help="Inspect or control this Mac.")
    computer.add_argument("--state", type=Path, default=ROOT / "state")
    computer.add_argument("rest", nargs=argparse.REMAINDER)
    computer.set_defaults(func=_computer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

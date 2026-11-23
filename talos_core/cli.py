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


def _console(args) -> int:
    from agent import chat
    from talos_core.interfaces.brain_ui import BrainUI
    from talos_core.memory.agent_memory import STATE_PATH, open_memory

    path = args.db or STATE_PATH
    with open_memory(path) as database:
        def responder(text: str) -> str:
            try:
                return chat(database, text, model=args.model, session="text")
            except (OSError, RuntimeError, ValueError) as error:
                return f"error: {error}"

        ui = BrainUI(responder=responder, model=args.model)
        if args.script:
            return ui.run_script(args.script)
        return ui.run_interactive()


def _initiative(args) -> int:
    from agent import model_text
    from talos_core.runtime.local_runtime import check_ollama_model
    from talos_core.self_improvement import initiative

    infer = None
    if args.model:
        check_ollama_model(args.model)
        def infer(prompt: str) -> str:
            return model_text([{"role": "user", "content": prompt}],
                              model=args.model, max_tokens=450)
    report = initiative.cycle(args.state, args.root, infer=infer, limit=args.limit)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _reflect(args) -> int:
    from talos_core.knowledge_reflection import reflect, summary
    from talos_core.memory.agent_memory import open_memory

    path = args.db or (args.state / "agent.sqlite3")
    if not path.is_file():
        print(json.dumps({"ok": False, "error": "agent database unavailable"}))
        return 1
    with open_memory(path) as database:
        found = reflect(database, args.state)
        print(json.dumps({"found": len(found), "top": summary(database)},
                         indent=2, ensure_ascii=False))
    return 0


def _audit(args) -> int:
    from tools.knowledge_audit import main as audit_main

    argv = ["--query", args.query]
    if args.graph:
        argv += ["--graph", str(args.graph)]
    return audit_main(argv)


def _vision(args) -> int:
    import base64

    from talos_core.room.room_vision import describe_image

    data = base64.b64encode(Path(args.file).read_bytes()).decode()
    print(json.dumps({"question": args.question,
                      "answer": describe_image(args.question, data)},
                     ensure_ascii=False))
    return 0


def _graph(args) -> int:
    from talos_core.knowledge_graph import graph

    if args.path:
        if len(args.path) != 2:
            raise SystemExit("--path needs exactly two symbols")
        payload = {"path": graph.shortest_path(args.path[0], args.path[1])}
    elif args.neighbours:
        payload = {"neighbours": graph.neighbours(args.neighbours, depth=args.depth)}
    elif args.node:
        payload = {"node": graph.node(args.node),
                   "neighbours": graph.neighbours(args.node, depth=args.depth)}
    else:
        payload = {"stats": graph.stats(),
                   "matches": graph.search(" ".join(args.query), limit=args.limit)
                   if args.query else []}
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0 if payload.get("stats", {}).get("available", True) else 1


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

    console = commands.add_parser("console", help="Animated ASCII brain and typed fallback console.")
    console.add_argument("--db", type=Path)
    console.add_argument("--model", default="qwen2.5:1.5b")
    console.add_argument("--script", nargs="*", help="Run fixed input lines instead of an interactive loop.")
    console.set_defaults(func=_console)

    initiative = commands.add_parser("initiative", help="Think of and decompose new capabilities.")
    initiative.add_argument("--state", type=Path, default=ROOT / "state")
    initiative.add_argument("--root", type=Path, default=ROOT)
    initiative.add_argument("--model", help="Optional local model for one novel idea.")
    initiative.add_argument("--limit", type=int, default=3)
    initiative.set_defaults(func=_initiative)

    reflect = commands.add_parser("reflect", help="Recompute related context from conversations and knowledge.")
    reflect.add_argument("--state", type=Path, default=ROOT / "state")
    reflect.add_argument("--db", type=Path)
    reflect.set_defaults(func=_reflect)

    audit = commands.add_parser("audit", help="Audit personal context, transcripts and the code graph.")
    audit.add_argument("--query", default="decomposition manager")
    audit.add_argument("--graph", type=Path)
    audit.set_defaults(func=_audit)

    vision = commands.add_parser("vision", help="Ask the local vision model about a camera/image frame.")
    vision.add_argument("file")
    vision.add_argument("question")
    vision.set_defaults(func=_vision)

    knowledge = commands.add_parser("graph", help="Query the full Graphify source graph.")
    knowledge.add_argument("query", nargs="*")
    knowledge.add_argument("--node")
    knowledge.add_argument("--neighbours")
    knowledge.add_argument("--path", nargs=2, metavar=("START", "END"))
    knowledge.add_argument("--depth", type=int, default=1)
    knowledge.add_argument("--limit", type=int, default=8)
    knowledge.set_defaults(func=_graph)

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

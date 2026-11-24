"""Unified command line for Talos.

Run ``talos --help`` from a checkout (``python -m talos_core.cli``) or after
``pip install -e .``. The CLI keeps entry points discoverable while the
implementation stays inside the ``talos_core`` packages.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _capabilities(args) -> int:
    from talos_core.self_improvement import capability_audit

    if args.queue:
        payload = {"queued": capability_audit.queue_gaps(args.root, args.state, limit=args.limit)}
    else:
        payload = capability_audit.report(args.root, args.state)
        payload["untested"] = [item["name"] for item in payload["capabilities"]
                               if not item["tested"]]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


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


def _instructions(args) -> int:
    from talos_core.cognition import personal_instructions

    if args.set_text is not None:
        value = personal_instructions.save(args.state, " ".join(args.set_text))
        print(json.dumps({"saved": len(value), "instructions": value}))
    else:
        print(json.dumps({"instructions": personal_instructions.load(args.state)}))
    return 0


def _code(args) -> int:
    from talos_core.self_improvement import initiative_coder

    origins = ("initiative:operator-goal", "initiative:health", "initiative:model",
               "initiative:test", "initiative:graph", "initiative:gap:openclaw",
               "initiative:gap:hermes") if args.all else initiative_coder.AUTO_ORIGINS
    result = initiative_coder.run_once(
        args.state, args.root, infer=initiative_coder.local_infer(args.model),
        apply=not args.verify_only, limit=args.limit, origins=origins,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


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


def _mcp(args) -> int:
    from talos_core import mcp_server
    return mcp_server.serve(root=args.root, state=args.state)


def _utility(args) -> int:
    import shutil
    from datetime import UTC, datetime

    command = args.command
    state = getattr(args, "state", ROOT / "state")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if command == "backup":
        source = state / "agent.sqlite3"
        if not source.is_file():
            print(json.dumps({"ok": False, "error": "agent database unavailable"}))
            return 1
        folder = state / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        destination = folder / f"agent-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        print(json.dumps({"backup": str(destination)}))
        return 0
    if command == "checkpoints":
        folder = state / "backups"
        print(json.dumps({"checkpoints": sorted(str(p) for p in folder.glob("*.sqlite3"))}))
        return 0
    if command == "debug" or command == "monitoring":
        from talos_core.self_improvement import health
        report = health.health(args.root, state, quick=True, lint=True)
        print(json.dumps(report["checks"], indent=2))
        return 0 if report["ok"] else 1
    if command == "insights":
        from talos_core.self_improvement import capability_audit, health
        report = capability_audit.report(getattr(args, "root", ROOT), state)
        print(json.dumps({"capabilities": report["summary"],
                          "gaps": len(report["gaps"]),
                          "health": health.summary(state)}, indent=2))
        return 0
    if command == "profile":
        from talos_core.mac import computer_use
        print(json.dumps({"root": str(ROOT), "state": str(state),
                          "permissions": computer_use.permissions()}, indent=2))
        return 0
    if command == "prompt_size":
        from agent import console_context
        from talos_core.memory.agent_memory import STATE_PATH, open_memory
        path = state / "agent.sqlite3"
        if not path.is_file():
            from contextlib import closing
            with closing(open_memory(STATE_PATH)) as db:
                print(console_context(db, "text", "", args.model))
        else:
            with open_memory(path) as db:
                print(console_context(db, "text", "", args.model))
        return 0
    if command == "security":
        from talos_core.mac import computer_use
        from talos_core.security.redact import findings
        print(json.dumps({"permissions": computer_use.permissions(),
                          "redaction_patterns": findings("Bearer abcdefghijklmnop")}, indent=2))
        return 0
    if command == "setup":
        print("Install the service with: python3 tools/talos_service.py install")
        print("Run the room with:      python3 run_talos.py")
        return 0
    if command == "skin":
        from talos_core.interfaces.theme import names
        print(json.dumps({"themes": list(names())}))
        return 0
    if command == "vault":
        folder = state / "Talos Memory"
        notes = list(folder.rglob("*.md")) if folder.is_dir() else []
        print(json.dumps({"vault": str(folder), "notes": len(notes),
                          "exists": folder.is_dir()}, indent=2))
        return 0
    if command == "gui":
        print("python3 run_talos.py")
        return 0
    if command == "webapp":
        from talos_core.portal import render
        print(render({"command": "webapp", "state": str(state)}))
        return 0
    if command == "whatsapp":
        from talos_core.mac import mac_context
        print(json.dumps({"enabled_apps": sorted(mac_context.enabled(state)),
                          "available_apps": mac_context.APPS}, indent=2))
        return 0
    if command == "dump":
        from talos_core.runtime import talos_activity
        print("\n".join(talos_activity.snapshot(state, None)))
        return 0
    if command == "fallback":
        from talos_core.runtime.local_runtime import local_model_names
        print(json.dumps({"models": sorted(local_model_names())}))
        return 0
    if command == "auth":
        import os
        print(json.dumps({"telegram_token": bool(os.environ.get("TALOS_TELEGRAM_TOKEN")),
                          "respan_key": (state / "respan.key").is_file()}))
        return 0
    if command == "pairing":
        print(json.dumps({"telegram_state": (state / "telegram").is_dir()}))
        return 0
    if command == "migrate":
        from talos_core.memory.agent_memory import open_memory
        path = state / "agent.sqlite3"
        if not path.is_file():
            print(json.dumps({"ok": False, "error": "agent database unavailable"}))
            return 1
        with open_memory(path) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(json.dumps({"ok": True, "tables": len(tables)}))
        return 0
    if command == "curator":
        from talos_core.self_improvement import capability_audit
        report = capability_audit.report(getattr(args, "root", ROOT), state)
        print(json.dumps({"summary": report["summary"], "gaps": len(report["gaps"])}, indent=2))
        return 0
    if command == "peer":
        from talos_core import nodes
        print(json.dumps({"peers": nodes.nodes(state)}, indent=2))
        return 0
    if command == "egress":
        from talos_core.mac import computer_use
        print(json.dumps({"permissions": computer_use.permissions(),
                          "loopback_only": True}, indent=2))
        return 0
    if command == "update":
        import subprocess
        completed = subprocess.run(["git", "-C", str(args.root), "status", "--short", "--branch"],
                                   capture_output=True, text=True, check=False)
        print(completed.stdout or completed.stderr)
        return completed.returncode
    if command == "uninstall":
        print("Stop with: python3 tools/talos_service.py stop")
        return 0
    if command == "clarify":
        print("What exactly should Talos change or do next?")
        return 0
    if command in ("feishu_doc", "feishu_drive"):
        from talos_core import feishu
        if args.app_id and args.app_secret:
            print(json.dumps(feishu.configure(state, args.app_id, args.app_secret)))
        elif command == "feishu_doc" and args.document:
            print(json.dumps(feishu.doc(state, args.document), indent=2))
        else:
            print(json.dumps(feishu.drive(state, args.folder or ""), indent=2))
        return 0
    if command == "acp":
        import shlex

        from talos_core import acp
        if not args.acp_command or not args.prompt:
            print(json.dumps({"ok": False, "error": "supply --command and --prompt"}))
            return 1
        if not args.confirm:
            print(json.dumps({"ok": False, "error": "add --confirm"}))
            return 1
        session = acp.ACPSession(shlex.split(args.acp_command))
        try:
            initialized = session.initialize()
            prompted = session.prompt(args.prompt, cwd=str(args.root))
        except (acp.ACPError, OSError, ValueError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 1
        finally:
            session.close()
        print(json.dumps({"initialized": initialized, "prompt": prompted}, indent=2))
        return 0
    if command in ("slack", "discord"):
        from talos_core import connectors
        if args.url:
            print(json.dumps(connectors.configure(state, command, args.url)))
        elif args.send:
            print(json.dumps(connectors.send(state, command, args.send)))
        else:
            print(json.dumps({"kind": command,
                              "configured": bool(connectors._load(state).get(command))}))
        return 0
    if command == "codex_runtime":
        from talos_core import codex_runtime
        try:
            result = codex_runtime.run(args.prompt, root=args.root, model=args.model,
                                       confirm=args.confirm)
        except (codex_runtime.RuntimeRefused, RuntimeError, ValueError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if command == "login":
        from talos_core import session_auth
        token = session_auth.login(state)
        print(json.dumps({"logged_in": True, "token_chars": len(token)}))
        return 0
    if command == "logout":
        from talos_core import session_auth
        print(json.dumps({"logged_out": session_auth.logout(state)}))
        return 0
    if command == "import_cmd":
        from talos_core import commands
        if args.name and args.cmd_args:
            print(json.dumps(commands.import_command(state, args.name, args.cmd_args)))
        else:
            print(json.dumps({"commands": commands.commands(state)}, indent=2))
        return 0
    if command == "webhook":
        from talos_core import webhook
        if args.url and args.secret:
            print(json.dumps(webhook.configure(state, args.url, args.secret)))
        else:
            print(json.dumps({"webhook": webhook.config(state)}, indent=2))
        return 0
    if command == "moa":
        from talos_core import moa
        if not args.response:
            print(json.dumps({"ok": False, "error": "supply --response"}))
            return 1
        print(json.dumps(moa.choose(args.response, keywords=args.keyword), indent=2))
        return 0
    if command == "pets":
        print("  /\\_/\\\n ( o.o )\n  > ^ <   Talos is watching the cursor.")
        return 0
    if command == "todo":
        from talos_core import todo
        if args.add:
            print(json.dumps(todo.add(state, args.add)))
        elif args.done:
            print(json.dumps({"completed": todo.complete(state, args.done)}))
        else:
            print(json.dumps({"todos": todo.items(state)}, indent=2))
        return 0
    if command == "tour":
        print("1. Run `talos status` for permissions and health.")
        print("2. Run `talos console` to type when speech is unavailable.")
        print("3. Run `talos capabilities` and `talos capabilities --queue`.")
        print("4. The launchd service runs maintenance, reflection and coding.")
        return 0
    if command == "tip":
        from talos_core.self_improvement import capability_audit
        report = capability_audit.report(getattr(args, "root", ROOT), state)
        gap = report["gaps"][0]["name"] if report["gaps"] else "none"
        print(f"Tip: {report['summary']['tested']}/{report['summary']['total']} tools tested; next gap: {gap}.")
        return 0
    if command == "focus_pane":
        from talos_core.mac.computer_use import ComputerUse
        result = ComputerUse(state, cursor=False).window("focus", app=args.app)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if command == "completion":
        parser = build_parser()
        names = sorted({name for action in parser._subparsers._group_actions
                        for name in action.choices})
        print(" ".join(names))
        return 0
    if command == "approvals":
        from talos_core.memory.agent_memory import open_memory
        from talos_core.runtime.local_actions import inbox_items
        path = state / "agent.sqlite3"
        if not path.is_file():
            print("[]")
            return 0
        with open_memory(path) as db:
            print(json.dumps(inbox_items(db, limit=20), indent=2))
        return 0
    if command == "connectors":
        import os

        from talos_core.mac import mac_context
        print(json.dumps({"apps": sorted(mac_context.enabled(state)),
                          "telegram": bool(os.environ.get("TALOS_TELEGRAM_TOKEN"))}, indent=2))
        return 0
    if command == "journey":
        from talos_core.runtime import talos_activity
        print("\n".join(talos_activity.snapshot(state, None)))
        return 0
    if command == "hooks":
        folder = state / "hooks"
        print(json.dumps({"hooks": sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []}))
        return 0
    if command == "bundles":
        from talos_core import skills
        print(json.dumps({"bundles": [item["name"] for item in skills.discover(state)]}))
        return 0
    if command == "usage":
        import agent
        from talos_core.memory.agent_memory import open_memory
        path = state / "agent.sqlite3"
        if not path.is_file():
            print("No agent database.")
            return 1
        with open_memory(path) as db:
            print(agent.console_usage(db, None))
        return 0
    print(f"{command}: no local implementation")
    return 1


def _media(args) -> int:
    from talos_core import media

    if args.confirm is False:
        raise SystemExit("Add --confirm to run a generation command.")
    try:
        result = media.generate(args.kind, args.prompt, args.out, confirm=True,
                                command=args.command)
    except (media.MediaRefused, media.MediaUnavailable, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _clipboard(args) -> int:
    from talos_core.mac.computer_use import ComputerUse

    computer = ComputerUse(args.state, cursor=False)
    if args.set is not None:
        text = " ".join(args.set)
        computer.set_clipboard(text)
        print(json.dumps({"saved": len(text)}))
    else:
        print(json.dumps({"text": computer.clipboard()}))
    return 0


def _skills(args) -> int:
    from talos_core import skills

    if args.create:
        path = skills.create(args.state, args.create[0], " ".join(args.create[1:]))
        print(json.dumps({"created": str(path)}))
    else:
        print(json.dumps({"skills": skills.discover(args.state)}, indent=2))
    return 0


def _nodes(args) -> int:
    from talos_core import nodes

    if args.register:
        print(json.dumps(nodes.register(args.state, args.register, endpoint=args.endpoint or "")))
    elif args.remove:
        print(json.dumps({"removed": nodes.remove(args.state, args.remove)}))
    else:
        print(json.dumps({"nodes": nodes.nodes(args.state)}, indent=2))
    return 0


def _plugins(args) -> int:
    from talos_core import plugins
    print(json.dumps({"plugins": plugins.discover(args.state)}, indent=2))
    return 0


def _subagents(args) -> int:
    from talos_core import subagents
    print(json.dumps({"subagents": subagents.discover(args.state)}, indent=2))
    return 0


def _tools(args) -> int:
    from talos_core.self_improvement import capability_audit
    payload = capability_audit.report(args.root, args.state)
    print(json.dumps({"summary": payload["summary"],
                      "gaps": len(payload["gaps"])}, indent=2))
    return 0


def _model(args) -> int:
    from talos_core.runtime.local_runtime import local_model_names
    print(json.dumps({"models": sorted(local_model_names())}, indent=2))
    return 0


def _cloud(args) -> int:
    from talos_core.cognition import cloud_model
    try:
        reply = cloud_model.complete(
            args.prompt, model=args.model, system=args.system, max_tokens=args.max_tokens,
        )
    except (cloud_model.CloudModelError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    if args.json:
        print(json.dumps({"ok": True, "model": args.model, "reply": reply}, ensure_ascii=False))
    else:
        print(reply)
    return 0


def _logs(args) -> int:
    from talos_core.runtime import talos_activity
    print("\n".join(talos_activity.snapshot(args.state, None)))
    return 0


def _config(args) -> int:
    from talos_core.mac import computer_use
    print(json.dumps({
        "root": str(ROOT), "state": str(args.state),
        "permissions": computer_use.permissions(),
        "graph": (ROOT / "graphify-out/graph.json").is_file(),
    }, indent=2))
    return 0


def _theme(args) -> int:
    from talos_core.interfaces import theme
    print(json.dumps({"themes": list(theme.names()), "default": "cyan"}))
    return 0


def _worktree(args) -> int:
    import subprocess
    completed = subprocess.run(["git", "-C", str(args.root), "worktree", "list", "--porcelain"],
                               capture_output=True, text=True, check=False)
    print(completed.stdout or completed.stderr)
    return completed.returncode


def _verify(args) -> int:
    from talos_core.self_improvement import health
    report = health.health(args.root, args.state, quick=True, lint=True, tests=False)
    print(json.dumps(report["checks"], indent=2))
    return 0 if report["ok"] else 1


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


def _audio_mode():
    if os.environ.get("TALOS_MUTE") == "1":
        return "muted"
    plist = Path.home() / "Library" / "LaunchAgents" / "local.talos.room.plist"
    try:
        import plistlib
        data = plistlib.loads(plist.read_bytes())
    except (OSError, ValueError):
        return "enabled"
    environment = data.get("EnvironmentVariables") or {}
    arguments = data.get("ProgramArguments") or []
    if environment.get("TALOS_MUTE") == "1" or "--mute" in arguments:
        return "muted"
    return "enabled"


def _status(args) -> int:
    from talos_core.mac import computer_use
    from talos_core.self_improvement import health

    state = args.state or ROOT / "state"
    print(f"Talos {VERSION} · {ROOT}")
    print(health.summary(state))
    permissions = computer_use.permissions()
    print("Mac access · Accessibility {accessibility} · Screen Recording {screen_recording}".format(**permissions))
    print("Audio · " + _audio_mode())
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

    capabilities = commands.add_parser("capabilities", help="Inventory tools and upstream feature gaps.")
    capabilities.add_argument("--root", type=Path, default=ROOT)
    capabilities.add_argument("--state", type=Path, default=ROOT / "state")
    capabilities.add_argument("--queue", action="store_true")
    capabilities.add_argument("--limit", type=int, default=2)
    capabilities.set_defaults(func=_capabilities)

    initiative = commands.add_parser("initiative", help="Think of and decompose new capabilities.")
    initiative.add_argument("--state", type=Path, default=ROOT / "state")
    initiative.add_argument("--root", type=Path, default=ROOT)
    initiative.add_argument("--model", help="Optional local model for one novel idea.")
    initiative.add_argument("--limit", type=int, default=3)
    initiative.set_defaults(func=_initiative)

    todo = commands.add_parser("todo", help="Local todo list.")
    todo.add_argument("--state", type=Path, default=ROOT / "state")
    todo.add_argument("--add")
    todo.add_argument("--done", type=int)
    todo.set_defaults(func=_utility)

    tour = commands.add_parser("tour", help="Show onboarding steps.")
    tour.add_argument("--state", type=Path, default=ROOT / "state")
    tour.set_defaults(func=_utility)

    tip = commands.add_parser("tip", help="Show one capability improvement tip.")
    tip.add_argument("--state", type=Path, default=ROOT / "state")
    tip.add_argument("--root", type=Path, default=ROOT)
    tip.set_defaults(func=_utility)

    focus = commands.add_parser("focus_pane", help="Focus an application window.")
    focus.add_argument("app")
    focus.add_argument("--state", type=Path, default=ROOT / "state")
    focus.set_defaults(func=_utility)

    completion = commands.add_parser("completion", help="Print supported command names.")
    completion.set_defaults(func=_utility)

    for kind in ("feishu_doc", "feishu_drive"):
        feishu_cmd = commands.add_parser(kind, help=f"Configure or use Feishu {kind}.")
        feishu_cmd.add_argument("--state", type=Path, default=ROOT / "state")
        feishu_cmd.add_argument("--app-id")
        feishu_cmd.add_argument("--app-secret")
        feishu_cmd.add_argument("--document")
        feishu_cmd.add_argument("--folder")
        feishu_cmd.set_defaults(func=_utility)
    acp = commands.add_parser("acp", help="Connect to an Agent Client Protocol command.")
    acp.add_argument("--command", dest="acp_command")
    acp.add_argument("--prompt")
    acp.add_argument("--root", type=Path, default=ROOT)
    acp.add_argument("--state", type=Path, default=ROOT / "state")
    acp.add_argument("--confirm", action="store_true")
    acp.set_defaults(func=_utility)
    for kind in ("slack", "discord"):
        external = commands.add_parser(kind, help=f"Configure or send a {kind} webhook.")
        external.add_argument("--state", type=Path, default=ROOT / "state")
        external.add_argument("--url")
        external.add_argument("--send")
        external.set_defaults(func=_utility)
    runtime = commands.add_parser("codex_runtime", help="Run the local Codex CLI read-only.")
    runtime.add_argument("prompt")
    runtime.add_argument("--root", type=Path, default=ROOT)
    runtime.add_argument("--state", type=Path, default=ROOT / "state")
    runtime.add_argument("--model")
    runtime.add_argument("--confirm", action="store_true")
    runtime.set_defaults(func=_utility)
    login = commands.add_parser("login", help="Create a local owner token.")
    login.add_argument("--state", type=Path, default=ROOT / "state")
    login.set_defaults(func=_utility)
    logout = commands.add_parser("logout", help="Remove the local owner token.")
    logout.add_argument("--state", type=Path, default=ROOT / "state")
    logout.set_defaults(func=_utility)
    import_cmd = commands.add_parser("import_cmd", help="Import or list command aliases.")
    import_cmd.add_argument("--state", type=Path, default=ROOT / "state")
    import_cmd.add_argument("--name")
    import_cmd.add_argument("cmd_args", nargs="*")
    import_cmd.set_defaults(func=_utility)
    webhook = commands.add_parser("webhook", help="Configure signed webhooks.")
    webhook.add_argument("--state", type=Path, default=ROOT / "state")
    webhook.add_argument("--url")
    webhook.add_argument("--secret")
    webhook.set_defaults(func=_utility)
    moa = commands.add_parser("moa", help="Choose among candidate agent responses.")
    moa.add_argument("--response", action="append")
    moa.add_argument("--keyword", action="append")
    moa.set_defaults(func=_utility)
    pets = commands.add_parser("pets", help="Show the Talos desk companion.")
    pets.add_argument("--state", type=Path, default=ROOT / "state")
    pets.set_defaults(func=_utility)

    for name, help_text in (
        ("auth", "Show configured local credentials."),
        ("pairing", "Show Telegram pairing state."),
        ("migrate", "Open and report the local agent schema."),
        ("curator", "Show capability curation summary."),
        ("peer", "List local peer nodes."),
        ("egress", "Show local network/permission policy."),
        ("update", "Show Git update state."),
        ("uninstall", "Show service uninstall command."),
        ("clarify", "Ask for a clearer instruction."),
        ("approvals", "List pending local approvals."),
        ("connectors", "List enabled connectors."),
        ("journey", "Show the recent activity journey."),
        ("hooks", "List local hook manifests."),
        ("bundles", "List local skill bundles."),
    ):
        extra = commands.add_parser(name, help=help_text)
        extra.add_argument("--state", type=Path, default=ROOT / "state")
        extra.set_defaults(func=_utility)

    for name, help_text in (
        ("usage", "Show measured console token usage."),
        ("backup", "Back up the local agent database."),
        ("checkpoints", "List local database backups."),
        ("debug", "Run the quick self-check."),
        ("monitoring", "Run the quick self-check."),
        ("insights", "Show capability and health summary."),
        ("profile", "Show local paths and permissions."),
        ("prompt_size", "Show console prompt component sizes."),
        ("security", "Show permissions and redaction status."),
        ("setup", "Show service setup commands."),
        ("skin", "List terminal themes."),
        ("vault", "Show the local Obsidian vault."),
        ("gui", "Show the native app command."),
        ("webapp", "Render the local status portal."),
        ("whatsapp", "Show enabled WhatsApp/Mac sources."),
        ("dump", "Print recent local activity."),
        ("fallback", "List local model fallbacks."),
    ):
        utility = commands.add_parser(name, help=help_text)
        utility.add_argument("--state", type=Path, default=ROOT / "state")
        utility.add_argument("--root", type=Path, default=ROOT)
        if name == "prompt_size":
            utility.add_argument("--model", default="qwen3:4b")
        utility.set_defaults(func=_utility)

    mcp = commands.add_parser("mcp", help="Serve Talos tools over MCP stdio.")
    mcp.add_argument("--root", type=Path, default=ROOT)
    mcp.add_argument("--state", type=Path, default=ROOT / "state")
    mcp.set_defaults(func=_mcp)

    media = commands.add_parser("media", help="Run a configured local music/video generator.")
    media.add_argument("kind", choices=["music", "video"])
    media.add_argument("prompt")
    media.add_argument("out", type=Path)
    media.add_argument("--command")
    media.add_argument("--confirm", action="store_true")
    media.set_defaults(func=_media)

    clipboard = commands.add_parser("clipboard", help="Read or set the Mac clipboard.")
    clipboard.add_argument("--state", type=Path, default=ROOT / "state")
    clipboard.add_argument("--set", nargs="+")
    clipboard.set_defaults(func=_clipboard)

    skill = commands.add_parser("skills", help="List or create local skill files.")
    skill.add_argument("--state", type=Path, default=ROOT / "state")
    skill.add_argument("--create", nargs="+")
    skill.set_defaults(func=_skills)

    node = commands.add_parser("nodes", help="List or register local nodes.")
    node.add_argument("--state", type=Path, default=ROOT / "state")
    node.add_argument("--register")
    node.add_argument("--remove")
    node.add_argument("--endpoint")
    node.set_defaults(func=_nodes)

    plugin = commands.add_parser("plugins", help="List local plugin manifests.")
    plugin.add_argument("--state", type=Path, default=ROOT / "state")
    plugin.set_defaults(func=_plugins)

    sub = commands.add_parser("subagents", help="List registered subagents.")
    sub.add_argument("--state", type=Path, default=ROOT / "state")
    sub.set_defaults(func=_subagents)

    tools = commands.add_parser("tools", help="Show the local capability summary.")
    tools.add_argument("--root", type=Path, default=ROOT)
    tools.add_argument("--state", type=Path, default=ROOT / "state")
    tools.set_defaults(func=_tools)

    model = commands.add_parser("model", help="List installed local models.")
    model.set_defaults(func=_model)

    cloud = commands.add_parser("cloud", help="Ask the optional OpenCode Go cloud model.")
    cloud.add_argument("prompt")
    cloud.add_argument("--model", default="deepseek-v4.1-flash")
    cloud.add_argument("--system")
    cloud.add_argument("--max-tokens", type=int, default=800)
    cloud.add_argument("--json", action="store_true")
    cloud.set_defaults(func=_cloud)

    logs = commands.add_parser("logs", help="Show the recent local activity journal.")
    logs.add_argument("--state", type=Path, default=ROOT / "state")
    logs.set_defaults(func=_logs)

    config = commands.add_parser("config", help="Show local paths and permissions.")
    config.add_argument("--state", type=Path, default=ROOT / "state")
    config.set_defaults(func=_config)

    theme = commands.add_parser("theme", help="List terminal themes.")
    theme.add_argument("--state", type=Path, default=ROOT / "state")
    theme.set_defaults(func=_theme)

    worktree = commands.add_parser("worktree", help="List Git worktrees.")
    worktree.add_argument("--root", type=Path, default=ROOT)
    worktree.add_argument("--state", type=Path, default=ROOT / "state")
    worktree.set_defaults(func=_worktree)

    verify = commands.add_parser("verify", help="Run the quick local verification.")
    verify.add_argument("--root", type=Path, default=ROOT)
    verify.add_argument("--state", type=Path, default=ROOT / "state")
    verify.set_defaults(func=_verify)

    instructions = commands.add_parser("instructions", help="Read or set local personal instructions.")
    instructions.add_argument("--state", type=Path, default=ROOT / "state")
    instructions.add_argument("--set", dest="set_text", nargs="+")
    instructions.set_defaults(func=_instructions)

    code = commands.add_parser("code", help="Draft, verify and apply one planned initiative.")
    code.add_argument("--state", type=Path, default=ROOT / "state")
    code.add_argument("--root", type=Path, default=ROOT)
    code.add_argument("--model", default="qwen3:4b")
    code.add_argument("--limit", type=int, default=1)
    code.add_argument("--verify-only", action="store_true")
    code.add_argument("--all", action="store_true", help="Include upstream/graph gap plans.")
    code.set_defaults(func=_code)

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

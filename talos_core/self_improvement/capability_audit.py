"""Capability inventory and upstream gap analysis.

The point is not to ask a model what Talos can do. The audit reads the actual
tool registries, public computer-use methods, CLI commands, room endpoints and
maintenance jobs, then checks which have a matching test. It also inventories
the pinned OpenClaw and Hermes tool surfaces and reports features that exist
upstream but have no local counterpart.

Gaps are evidence, not promises. ``queue_gaps`` sends the highest-value ones
through decomposition and the existing verified-patch loop.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = "capability-audit.json"
TOKEN = re.compile(r"[a-z][a-z0-9]+")


@dataclass
class Capability:
    name: str
    kind: str
    implemented: bool
    tested: bool
    tested_by: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Gap:
    project: str
    name: str
    evidence: str
    score: int
    target: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _words(value: str) -> set[str]:
    return set(TOKEN.findall(value.casefold().replace("_", " ").replace("-", " ")))


def _test_text(root: Path) -> str:
    chunks = []
    for path in (root / "tests").glob("test_*.py"):
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def local_capabilities(root: Path = ROOT) -> list[Capability]:
    tests = _test_text(root)
    found: dict[str, Capability] = {}

    def add(name: str, kind: str, *, notes: str = "", alias: str | None = None) -> None:
        key = f"{kind}:{name}"
        if key in found:
            return
        needle = alias or name.replace("_", " ")
        haystack = tests.casefold()
        tail = name.removeprefix("/v1/").casefold()
        tested = (name.casefold() in haystack or needle.casefold() in haystack
                  or (tail and tail in haystack))
        found[key] = Capability(name=name, kind=kind, implemented=True,
                                tested=tested, tested_by="tests" if tested else "",
                                notes=notes)

    try:
        from agent import LOCAL_WRITES, LOOKUP_TOOLS, TOOLS
        for tool in TOOLS:
            add(tool, "planner_tool", notes="writes" if tool in LOCAL_WRITES else
                "lookup" if tool in LOOKUP_TOOLS else "action")
    except (ImportError, OSError):
        pass
    try:
        from talos_core.mac.computer_use import ComputerUse
        for name in ("click", "type_text", "press", "menu", "scroll", "hover", "drag",
                     "window", "screenshot", "see", "find_controls", "ocr", "click_ref",
                     "type_ref", "click_goal", "observe", "screenshot_jpeg", "window_jpeg"):
            if callable(getattr(ComputerUse, name, None)):
                add(name, "computer_use")
    except (ImportError, OSError):
        pass
    for module_name, capability in (
        ("talos_core.cognition.personal_instructions", "personal_instructions"),
        ("talos_core.interfaces.theme", "theme"),
        ("talos_core.security.redact", "secrets"),
        ("talos_core.knowledge.pdf", "pdf"),
        ("talos_core.plugins", "plugins"),
        ("talos_core.skills", "skill_workshop"),
        ("talos_core.subagents", "subagents"),
        ("talos_core.portal", "portal"),
        ("talos_core.github", "github_publish"),
        ("talos_core.nodes", "nodes"),
        ("talos_core.delegate", "delegate"),
        ("talos_core.media", "music_generate"),
        ("talos_core.media", "video_generate"),
    ):
        try:
            __import__(module_name)
            add(capability, "feature", notes=module_name)
        except (ImportError, OSError):
            pass
    try:
        from talos_core.cli import build_parser
        for action in build_parser()._subparsers._group_actions:
            for command in action.choices:
                add(command, "cli")
    except (ImportError, OSError, AttributeError):
        pass
    try:
        text = (root / "talos_core/room/room_service.py").read_text(encoding="utf-8")
        for path in sorted(set(re.findall(r'"(/v1/[a-z0-9_/.-]+)"', text))):
            add(path, "room_endpoint")
    except OSError:
        pass
    try:
        text = (root / "talos_core/runtime/maintenance.py").read_text(encoding="utf-8")
        for job in sorted(set(re.findall(r'kind == "([a-z_]+)"', text))):
            add(job, "maintenance_job")
    except OSError:
        pass
    return sorted(found.values(), key=lambda item: (item.kind, item.name))


def upstream_capabilities(state: Path) -> dict[str, list[str]]:
    """Inventory tool-shaped files from the pinned source trees."""
    result: dict[str, list[str]] = {}
    openclaw = state / "openclaw-src/src/agents/tools"
    if openclaw.is_dir():
        names = []
        for path in openclaw.glob("*.ts"):
            if ".test." in path.name or path.stem.startswith("computer-tool-"):
                continue
            if path.stem.endswith("-tool"):
                names.append(path.stem[:-5])
        result["OpenClaw"] = sorted(set(names))
    hermes = state / "hermes-src"
    if hermes.is_dir():
        names = []
        for path in (hermes / "tools").glob("*_tool.py"):
            name = path.stem[:-5]
            if name:
                names.append(name)
        skills = hermes / "optional-skills"
        if skills.is_dir():
            names.extend(path.name for path in skills.iterdir()
                         if path.is_dir() and not path.name.startswith("."))
        commands = hermes / "hermes_cli/subcommands"
        if commands.is_dir():
            names.extend(f"cli {path.stem}" for path in commands.glob("*.py")
                         if path.stem not in {"__init__", "_shared"})
        result["Hermes"] = sorted(set(names))
    return result


def _local_vocabulary(capabilities: list[Capability]) -> set[str]:
    words: set[str] = set()
    for item in capabilities:
        words |= _words(item.name)
    # Known aliases/adaptations documented in UPSTREAM.md.
    words |= {"agent", "agents", "wait", "ask", "user", "computer", "browser", "cron",
              "memory", "telegram", "session", "sessions", "history", "message", "file",
              "shell", "exec", "web", "search", "image", "voice", "tts", "screen",
              "terminal", "reminder", "calendar", "task", "transcript", "transcripts",
              "decision", "decisions", "gateway", "dashboard", "structured", "output",
              "mobile", "ui", "personal", "instructions", "progress", "card"}
    return words


def gaps(root: Path = ROOT, state: Path | None = None) -> list[Gap]:
    capabilities = local_capabilities(root)
    vocabulary = _local_vocabulary(capabilities)
    upstream = upstream_capabilities(state or root / "state")
    found = []
    for project, names in upstream.items():
        for name in names:
            parts = _words(name)
            overlap = len(parts & vocabulary)
            if overlap >= max(1, len(parts) // 2):
                continue
            base = 7 if project == "OpenClaw" else 4 if name.startswith("cli ") else 3
            found.append(Gap(
                project=project,
                name=name,
                evidence=f"{project} capability `{name}` has no matching Talos tool",
                score=base + max(0, 2 - overlap),
                target="talos_core/self_improvement/initiative.py",
            ))
    for item in capabilities:
        if not item.tested and item.kind in {"planner_tool", "computer_use", "maintenance_job"}:
            found.append(Gap(
                project="Talos", name=f"test {item.kind} {item.name}",
                evidence=f"{item.kind} `{item.name}` has no matching regression test",
                score=6, target="tests",
            ))
    found.sort(key=lambda item: (-item.score, item.project, item.name))
    return found


def report(root: Path = ROOT, state: Path | None = None) -> dict:
    root = Path(root)
    state = Path(state) if state else root / "state"
    capabilities = local_capabilities(root)
    gap_list = gaps(root, state)
    tested = sum(1 for item in capabilities if item.tested)
    payload = {
        "checked_at": time.time(),
        "root": str(root),
        "capabilities": [item.to_dict() for item in capabilities],
        "summary": {
            "total": len(capabilities),
            "tested": tested,
            "untested": len(capabilities) - tested,
            "coverage_pct": round(100 * tested / len(capabilities), 1) if capabilities else 0.0,
        },
        "upstream": upstream_capabilities(state),
        "gaps": [item.to_dict() for item in gap_list],
    }
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / REPORT
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return payload


def queue_gaps(root: Path, state: Path, *, limit: int = 2) -> list[dict]:
    """Queue top gaps through decomposition and the verified coding loop."""
    from talos_core.self_improvement import initiative

    report_data = report(root, state)
    ideas = []
    for gap in report_data["gaps"][:max(1, limit)]:
        ideas.append(initiative.Idea(
            id=initiative._ident(f"port {gap['project']} {gap['name']}"),
            title=f"Port {gap['project']} capability: {gap['name'].replace('_', ' ')}",
            rationale=gap["evidence"],
            acceptance=(
                f"A local implementation covers the upstream behavior with a regression test; "
                f"replay the {gap['project']} source and compare user-visible results."
            ),
            source=f"gap:{gap['project'].lower()}",
            files=(gap["target"],),
            score=gap["score"],
        ))
    accepted = []
    for idea in ideas:
        plan = initiative.decomposition.plan_task(
            state, idea.title, idea.rationale, acceptance=idea.acceptance,
            files=idea.files, max_depth=4,
        )
        initiative._queue(state, idea, plan)
        accepted.append({"title": idea.title, "project": idea.source,
                         "plan_path": plan["path"], "leaves": len(plan["leaves"])})
    return accepted


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--queue", action="store_true")
    args = parser.parse_args()
    if args.queue:
        print(json.dumps(queue_gaps(args.root, args.state or args.root / "state"), indent=2))
    else:
        print(json.dumps(report(args.root, args.state), indent=2, sort_keys=True))

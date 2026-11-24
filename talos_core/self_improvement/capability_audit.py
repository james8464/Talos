"""Capability inventory and upstream gap analysis.

The point is not to ask a model what Talos can do. The audit reads the actual
tool registries, public computer-use methods, CLI commands, room endpoints and
maintenance jobs, then checks which have explicit test evidence. It also
inventories the pinned OpenClaw and Hermes tool surfaces and reports features
that exist upstream but have no declared local counterpart.

Coverage is deliberately strict:

* A local capability is tested only when a test function name contains the full
  capability identifier as a token sequence, a test file declares
  ``CAPABILITY:<kind>:<name>``, or ``capability_manifest.LOCAL_EVIDENCE`` names
  a real test reference. Text anywhere else in a test file is not evidence.
* An upstream capability is covered only through an explicit entry in
  ``capability_manifest.UPSTREAM_MANIFEST`` whose local target exists and has
  verified test evidence. Shared words are never treated as parity.

Gaps are evidence, not promises. ``queue_gaps`` sends the highest-value ones
through decomposition and the existing verified-patch loop.
"""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = "capability-audit.json"
TOKEN = re.compile(r"[a-z0-9]+")
CAPABILITY_MARKER = re.compile(r"CAPABILITY:\s*([a-z_]+:[A-Za-z0-9_/.-]+)")
UNMAPPED = "unmapped"
UNTESTED = "untested"
COVERED = "covered"


@dataclass
class Capability:
    name: str
    kind: str
    implemented: bool
    tested: bool
    tested_by: str = ""
    notes: str = ""
    evidence: str = ""
    coverage_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Gap:
    project: str
    name: str
    evidence: str
    score: int
    target: str = ""
    mapped_local: str = ""
    coverage_status: str = UNMAPPED

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TestIndex:
    """Parsed test evidence, never raw prose matches."""

    functions: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    markers: dict[str, list[str]] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(TOKEN.findall(value.casefold().replace("_", " ").replace("-", " ")))


def _has_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[index:index + width] == needle
               for index in range(len(haystack) - width + 1))


def _test_index(root: Path = ROOT) -> TestIndex:
    index = TestIndex()
    for path in sorted((root / "tests").glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        for line_number, line in enumerate(text.splitlines(), start=1):
            for marker in CAPABILITY_MARKER.finditer(line):
                index.markers.setdefault(marker.group(1), []).append(
                    f"{relative}:{line_number}")
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue

        def visit(node: ast.AST, prefix: str = "", relative: str = relative) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name.startswith("test"):
                        tokens = _tokens(child.name)
                        if tokens and tokens[0] == "test":
                            tokens = tokens[1:]
                        reference = f"{relative}::{prefix}{child.name}"
                        index.functions.append((tokens, reference))
                        index.refs[reference] = reference
                        index.refs.setdefault(f"{relative}::{child.name}", reference)
                    continue
                if isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}::")
                else:
                    visit(child, prefix)

        visit(tree)
    return index


def evidence_for(root: Path = ROOT, identifier: str = "", *,
                 index: TestIndex | None = None,
                 declared: dict[str, str] | None = None) -> str:
    """Return verified evidence for one ``kind:name`` capability identifier.

    Only three sources count: a declared evidence reference that resolves to a
    parsed test, a ``CAPABILITY:`` marker, or the identifier appearing as a
    whole token sequence in a test function name.
    """
    index = index or _test_index(root)
    declared = declared if declared is not None else _local_evidence()
    reference = declared.get(identifier, "")
    if reference:
        resolved = index.refs.get(reference, "")
        if resolved:
            return resolved
    markers = index.markers.get(identifier)
    if markers:
        return markers[0]
    name = identifier.split(":", 1)[1] if ":" in identifier else identifier
    tokens = _tokens(name)
    for function_tokens, reference in index.functions:
        if len(tokens) == 1:
            # A one-word capability must be the subject of the test, not a word
            # that happens to appear later in an unrelated test name.
            if function_tokens and function_tokens[0] == tokens[0]:
                return reference
        elif _has_sequence(function_tokens, tokens):
            return reference
    return ""


def _local_evidence() -> dict[str, str]:
    from talos_core.self_improvement import capability_manifest

    return dict(capability_manifest.LOCAL_EVIDENCE)


def _manifest() -> dict:
    from talos_core.self_improvement import capability_manifest

    return capability_manifest.UPSTREAM_MANIFEST


def local_capabilities(root: Path = ROOT, *, index: TestIndex | None = None,
                       declared: dict[str, str] | None = None) -> list[Capability]:
    index = index or _test_index(root)
    declared = declared if declared is not None else _local_evidence()
    found: dict[str, Capability] = {}

    def add(name: str, kind: str, *, notes: str = "") -> None:
        key = f"{kind}:{name}"
        if key in found:
            return
        evidence = evidence_for(root, key, index=index, declared=declared)
        found[key] = Capability(
            name=name, kind=kind, implemented=True, tested=bool(evidence),
            tested_by=evidence, notes=notes, evidence=evidence,
            coverage_status="tested" if evidence else UNTESTED,
        )

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
        ("talos_core.mac.browser", "browser"),
        ("talos_core.mac.browser_actions", "browser_actions"),
        ("talos_core.design_studio", "design_studio"),
        ("talos_core.security.policy", "policy"),
        ("talos_core.mac.pages", "pages"),
        ("talos_core.mac.gui_apps", "gui_apps"),
        ("talos_core.mac.gui_actions", "gui_actions"),
        ("talos_core.interfaces.room_client", "room_client"),
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
        # optional-skills are content packs, not executable tools; the local
        # skill workshop covers that surface once. Counting each topic as a
        # missing tool inflated the gap list without representing code.
        commands = hermes / "hermes_cli/subcommands"
        if commands.is_dir():
            names.extend(f"cli {path.stem}" for path in commands.glob("*.py")
                         if path.stem not in {"__init__", "_shared"})
        result["Hermes"] = sorted(set(names))
    return result


def _resolve(project: str, name: str, capabilities: dict[str, Capability],
             index: TestIndex, manifest: dict) -> tuple[str, str]:
    """Return ``(coverage_status, detail)`` for one upstream capability."""
    entry = manifest.get(project, {}).get(name)
    if entry is None:
        return UNMAPPED, "has no declared Talos mapping"
    local = capabilities.get(entry.local)
    if local is None:
        return UNMAPPED, f"maps to `{entry.local}`, which is not implemented"
    if entry.evidence:
        resolved = index.refs.get(entry.evidence, "")
        if not resolved:
            return UNMAPPED, (
                f"declares evidence `{entry.evidence}`, which is not a real test")
        return COVERED, f"maps to `{entry.local}` with evidence {resolved}"
    if local.tested and local.tested_by:
        return COVERED, f"maps to `{entry.local}` with evidence {local.tested_by}"
    return UNTESTED, f"maps to `{entry.local}`, which has no verified test"


def gaps(root: Path = ROOT, state: Path | None = None, *,
         capabilities: list[Capability] | None = None,
         index: TestIndex | None = None,
         manifest: dict | None = None,
         declared: dict[str, str] | None = None) -> list[Gap]:
    index = index or _test_index(root)
    capabilities = capabilities or local_capabilities(
        root, index=index, declared=declared)
    manifest = manifest if manifest is not None else _manifest()
    by_id = {f"{item.kind}:{item.name}": item for item in capabilities}
    upstream = upstream_capabilities(Path(state) if state else root / "state")
    found: list[Gap] = []
    for project, names in upstream.items():
        for name in names:
            status, detail = _resolve(project, name, by_id, index, manifest)
            if status == COVERED:
                continue
            base = 7 if project == "OpenClaw" else 4 if name.startswith("cli ") else 3
            entry = manifest.get(project, {}).get(name)
            found.append(Gap(
                project=project,
                name=name,
                evidence=f"{project} capability `{name}` {detail}",
                score=base + (2 if status == UNMAPPED else 1),
                target="talos_core/self_improvement/initiative.py",
                mapped_local=entry.local if entry else "",
                coverage_status=status,
            ))
    for item in capabilities:
        if not item.tested and item.kind in {"planner_tool", "computer_use", "maintenance_job"}:
            found.append(Gap(
                project="Talos", name=f"test {item.kind} {item.name}",
                evidence=f"{item.kind} `{item.name}` has no explicit test evidence",
                score=6, target="tests",
                mapped_local=f"{item.kind}:{item.name}",
                coverage_status=UNTESTED,
            ))
    found.sort(key=lambda item: (-item.score, item.project, item.name))
    return found


def top_gaps(root: Path = ROOT, state: Path | None = None, *, limit: int = 2) -> list[Gap]:
    """Return the highest-scoring gaps, best first, capped at ``limit``."""
    return gaps(root, state)[:max(0, limit)]


def report(root: Path = ROOT, state: Path | None = None) -> dict:
    root = Path(root)
    state = Path(state) if state else root / "state"
    index = _test_index(root)
    capabilities = local_capabilities(root, index=index)
    gap_list = gaps(root, state, capabilities=capabilities, index=index)
    upstream = upstream_capabilities(state)
    upstream_gaps = [item for item in gap_list if item.project != "Talos"]
    upstream_total = sum(len(names) for names in upstream.values())
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
            "upstream_total": upstream_total,
            "upstream_covered": upstream_total - len(upstream_gaps),
            "upstream_gaps": len(upstream_gaps),
            "upstream_coverage_pct": (
                round(100 * (upstream_total - len(upstream_gaps)) / upstream_total, 1)
                if upstream_total else 0.0),
            "unmapped_upstream": sum(1 for item in upstream_gaps
                                     if item.coverage_status == UNMAPPED),
            "evidence_rule": (
                "explicit manifest mapping plus verified test evidence; "
                "raw word overlap is not coverage"),
        },
        "upstream": upstream,
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

    report(root, state)
    ideas = []
    for gap in top_gaps(root, state, limit=max(1, limit)):
        record = gap.to_dict()
        ideas.append(initiative.Idea(
            id=initiative._ident(f"port {record['project']} {record['name']}"),
            title=f"Port {record['project']} capability: {record['name'].replace('_', ' ')}",
            rationale=record["evidence"],
            acceptance=(
                f"A local implementation covers the upstream behavior with a regression test; "
                f"replay the {record['project']} source and compare user-visible results."
            ),
            source=f"gap:{record['project'].lower()}",
            files=(record["target"],),
            score=record["score"],
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

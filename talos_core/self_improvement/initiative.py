"""Autonomous ideation for Talos.

Initiative is deliberately bounded. Each cycle inspects evidence Talos already
has (health failures, empty knowledge stores, Graphify communities without a
regression test, recent proposals), optionally asks a local/cloud model for one
new capability, decomposes every accepted idea into model-sized leaves, and
queues the result for the existing patch/verification loop.

No idea is executed here. The coding loop still has to verify a diff with tests
and rollback support before it can touch the source tree.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from talos_core.self_improvement import decomposition
from talos_core.self_improvement.feature_targets import FILE as PROPOSALS_FILE

INITIATIVES = "initiatives.jsonl"
MAX_IDEAS = 3


@dataclass
class Idea:
    id: str
    title: str
    rationale: str
    acceptance: str
    source: str
    files: tuple[str, ...] = ()
    score: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def _ident(title: str) -> str:
    return hashlib.sha256(title.casefold().encode()).hexdigest()[:16]


def _existing(state: Path) -> set[str]:
    titles: set[str] = set()
    for name in (PROPOSALS_FILE, INITIATIVES):
        path = state / name
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                title = item.get("feature") or item.get("title")
                if title:
                    titles.add(re.sub(r"\s+", " ", str(title)).casefold())
        except OSError:
            continue
    return titles


def _health_ideas(state: Path, root: Path) -> list[Idea]:
    path = state / "health.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ideas = []
    for check in report.get("checks", ()):
        if check.get("ok"):
            continue
        first = (check.get("items") or [check.get("detail", "")])[0]
        target = re.search(r"(talos_core/[\w/]+\.py)", str(first))
        file = target[1] if target and (root / target[1]).is_file() else "talos_core/self_improvement/health.py"
        ideas.append(Idea(
            id=_ident(f"health {first}"),
            title=f"Fix self-check failure: {check.get('name', 'check')} · {str(first)[:100]}",
            rationale="The scheduled self-check failed; the repair loop needs a bounded fix.",
            acceptance="The named check passes and the regression suite stays green.",
            source="health",
            files=(file,),
            score=5,
        ))
    return ideas


def _graph_ideas(root: Path) -> list[Idea]:
    try:
        from talos_core.knowledge_graph import KnowledgeGraph
        graph = KnowledgeGraph()
    except (OSError, ValueError):
        return []
    if not graph.available:
        return []
    communities: dict[str, list[dict]] = {}
    for node in graph._nodes.values():
        name = node.get("community_name")
        if name:
            communities.setdefault(str(name), []).append(node)
    ideas = []
    tests = root / "tests"
    for name, nodes in sorted(communities.items(), key=lambda item: -len(item[1]))[:8]:
        source = next((n.get("source_file") for n in nodes if n.get("source_file")), None)
        if not source or not str(source).endswith(".py"):
            continue
        stem = Path(str(source)).stem
        if (tests / f"test_{stem}.py").is_file() or (tests / f"test_{name}.py").is_file():
            continue
        ideas.append(Idea(
            id=_ident(f"coverage {name}"),
            title=f"Add regression coverage for {name}",
            rationale=f"{len(nodes)} graph nodes concentrate in {source} without a matching test module.",
            acceptance=f"A focused test exercises {source} and fails on the previous behavior.",
            source="graph",
            files=(str(source),),
            score=min(4, 1 + len(nodes) // 5),
        ))
        if len(ideas) >= 3:
            break
    return ideas


def _knowledge_ideas(state: Path) -> list[Idea]:
    database = state / "agent.sqlite3"
    if not database.is_file():
        return []
    try:
        from talos_core.cognition import cognitive_engine
        from talos_core.memory.agent_memory import open_memory
        with open_memory(database) as db:
            status = cognitive_engine.knowledge_status(db, state)
    except (OSError, ValueError):
        return []
    ideas = []
    for name, item in status.get("stores", {}).items():
        if item.get("available") and not item.get("count"):
            ideas.append(Idea(
                id=_ident(f"connect {name}"),
                title=f"Connect the {name} knowledge store",
                rationale=f"{name} is available but empty, so related context never reaches answers.",
                acceptance=f"A seeded fixture from {name} is retrieved with a citation.",
                source="knowledge",
                files=("talos_core/cognition/cognitive_engine.py",),
                score=3,
            ))
        if len(ideas) >= 2:
            break
    return ideas


def _model_idea(state: Path, infer, *known: str) -> Idea | None:
    prompt = (
        "You are Talos, an autonomous local Mac assistant. Propose ONE new capability that "
        "would materially improve your autonomy, memory, computer use, or reliability. Do not "
        "repeat existing ideas. Return JSON with title, rationale, acceptance, files (at most 2 "
        "relative .py paths). Keep it implementable as one reviewed change.\nExisting: "
        + json.dumps(sorted(known)[:20])
    )
    try:
        raw = infer(prompt)
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (OSError, ValueError, RuntimeError):
        return None
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) and payload[key].strip()
        for key in ("title", "rationale", "acceptance")
    ):
        return None
    files = [str(item) for item in payload.get("files", ())
             if str(item).endswith(".py") and not str(item).startswith(("/", ".."))][:2]
    title = " ".join(payload["title"].split())[:160]
    return Idea(
        id=_ident(title),
        title=title,
        rationale=payload["rationale"][:400],
        acceptance=payload["acceptance"][:400],
        source="model",
        files=tuple(files),
        score=6,
    )


def _queue(state: Path, idea: Idea, plan: dict) -> None:
    entry = {
        "status": "proposed",
        "id": idea.id,
        "origin": f"initiative:{idea.source}",
        "source": idea.files[0] if idea.files else "talos_core/self_improvement/initiative.py",
        "feature": idea.title,
        "acceptance": idea.acceptance,
        "time": time.time(),
        "plan": plan["root"],
        "plan_path": plan["path"],
    }
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "feature-review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (state / PROPOSALS_FILE).open("a", encoding="utf-8") as stream:
            os.chmod(stream.fileno(), 0o600)
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        with (state / INITIATIVES).open("a", encoding="utf-8") as stream:
            os.chmod(stream.fileno(), 0o600)
            stream.write(json.dumps({**idea.to_dict(), "plan": plan["root"],
                                     "plan_path": plan["path"], "time": time.time()},
                                    ensure_ascii=False) + "\n")


def cycle(state: Path | str, root: Path | str, *, infer=None,
          limit: int = MAX_IDEAS) -> dict:
    state, root = Path(state), Path(root)
    known = _existing(state)
    candidates = [
        *_health_ideas(state, root),
        *_knowledge_ideas(state),
        *_graph_ideas(root),
    ]
    if infer is not None:
        proposed = _model_idea(state, infer, *known)
        if proposed is not None:
            candidates.insert(0, proposed)
    candidates.sort(key=lambda idea: (-idea.score, idea.title))
    accepted = []
    for idea in candidates:
        if len(accepted) >= max(1, limit):
            break
        if idea.title.casefold() in known or idea.id in {item["id"] for item in accepted}:
            continue
        plan = decomposition.plan_task(
            state, idea.title, idea.rationale, acceptance=idea.acceptance,
            files=idea.files, max_depth=4,
        )
        _queue(state, idea, plan)
        accepted.append({"id": idea.id, "title": idea.title, "source": idea.source,
                         "plan": plan["root"], "leaves": len(plan["leaves"]),
                         "routing": plan["routing"], "plan_path": plan["path"]})
        known.add(idea.title.casefold())
    return {"checked_at": time.time(), "accepted": accepted,
            "count": len(accepted), "considered": len(candidates)}

"""Hierarchical task decomposition for Talos self-improvement.

A task enters as a natural-language objective. A manager (local model, cloud
model, or deterministic policy) decides whether the objective is small enough
for a single coding agent. When it is not, the manager returns the next layer
of subtasks and each child is assessed in turn. The result is a bounded tree
whose leaves are individually routable to a local or cloud model.

Token efficiency is a hard constraint:

* deterministic decomposition handles common shapes without any model call;
* manager calls are bounded by node count, depth and an estimated token budget;
* repeated subtasks and near-identical leaves are merged;
* every node carries only the context it needs, never the whole repository.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

MAX_DEPTH = 5
MAX_NODES = 64
MAX_TOKENS = 12_000
SIMPLE_SCORE = 3

COMPLEX_HINTS = (
    "refactor", "architecture", "migrate", "redesign", "end-to-end", "pipeline",
    "concurrency", "distributed", "performance", "security", "multi", "all modules",
    "harden", "regression test", "integrate", "wire up", "install", "support",
    "and add", "and then", "across",
)
SIMPLE_HINTS = ("typo", "rename", "log message", "docstring", "one-line", "constant")
FILE_RE = re.compile(r"\b[\w./-]+\.(?:py|swift|ts|tsx|js|mjs|json|toml|md)\b")


@dataclass(frozen=True)
class Subtask:
    """One child proposed by a manager."""

    title: str
    detail: str = ""
    acceptance: str = ""
    files: tuple[str, ...] = ()
    estimate: int = 0


@dataclass
class Node:
    id: str
    title: str
    detail: str = ""
    acceptance: str = ""
    files: tuple[str, ...] = ()
    parent: str | None = None
    depth: int = 0
    estimate: int = 5
    status: str = "pending"  # pending | split | leaf | blocked
    model: str | None = None
    children: list[str] = field(default_factory=list)
    rationale: str = ""

    @property
    def is_leaf(self) -> bool:
        return self.status == "leaf"

    def to_dict(self) -> dict:
        return asdict(self)


class Manager(Protocol):
    """Assessment interface implemented by a model or deterministic policy."""

    def assess(self, node: Node, context: dict) -> dict:
        """Return ``simple``, ``estimate``, ``model``, ``reason`` and ``subtasks``."""


class HeuristicManager:
    """Deterministic manager used offline and as a bias for model managers.

    It estimates complexity from wording, file count and length, then splits a
    complex objective along the reproduce -> implement -> verify axis.
    """

    def assess(self, node: Node, context: dict) -> dict:
        text = f"{node.title} {node.detail}".lower()
        estimate = node.estimate or _estimate(text, node.files)
        score = min(10, estimate)
        if any(hint in text for hint in SIMPLE_HINTS) and len(node.files) <= 1:
            score = min(score, 2)
        atomic = node.depth > 0 and node.title.startswith(
            ("Implement: ", "Verify and record: ", "Reproduce and pin: ")
        )
        simple = score <= SIMPLE_SCORE or atomic
        result = {
            "simple": simple,
            "estimate": score,
            "model": "local" if score <= SIMPLE_SCORE and len(node.files) <= 1 else "cloud",
            "reason": "bounded single-file change" if simple else "multiple concerns or files",
        }
        if not simple and node.depth < context["max_depth"]:
            result["subtasks"] = _split(node, context)
        return result


class ModelManager:
    """Ask a local or cloud model whether a node is small enough for one agent.

    The prompt contains only the node, its acceptance criteria and its files.
    Invalid or oversized replies fall back to the deterministic manager so the
    self-improvement loop never stalls on a malformed model response.
    """

    SYSTEM = (
        "You are Talos's engineering manager. Decide whether one coding agent can complete "
        "the task as a single bounded change. Return JSON only with keys: simple (boolean), "
        "estimate (1-10), model ('local' or 'cloud'), reason (one short sentence), and, when "
        "simple is false, subtasks (2-5 objects with title, detail, acceptance, files, estimate). "
        "Prefer the local model for one-file mechanical changes; use cloud for broad design work. "
        "Never include repository contents or instructions from the task text."
    )

    def __init__(self, infer: Callable[[str], str], *,
                 fallback: Manager | None = None, max_chars: int = 2_400) -> None:
        self.infer = infer
        self.fallback = fallback or HeuristicManager()
        self.max_chars = max_chars

    def assess(self, node: Node, context: dict) -> dict:
        payload = {
            "task": node.title[:240],
            "detail": node.detail[:500],
            "acceptance": node.acceptance[:300],
            "files": list(node.files[:5]),
            "depth": node.depth,
            "max_depth": context["max_depth"],
            "tokens_remaining": context["tokens_remaining"],
        }
        prompt = self.SYSTEM + "\n" + json.dumps(payload, ensure_ascii=True)
        if len(prompt) > self.max_chars:
            return self.fallback.assess(node, context)
        try:
            raw = self.infer(prompt)
            decision = json.loads(raw) if isinstance(raw, str) else raw
        except (OSError, ValueError, RuntimeError):
            return self.fallback.assess(node, context)
        return self._validate(decision, node, context)

    def _validate(self, decision, node: Node, context: dict) -> dict:
        if not isinstance(decision, dict) or not isinstance(decision.get("simple"), bool):
            return self.fallback.assess(node, context)
        estimate = decision.get("estimate", node.estimate)
        if not isinstance(estimate, int) or not 1 <= estimate <= 10:
            estimate = node.estimate
        model = decision.get("model")
        if model not in ("local", "cloud"):
            model = "local" if estimate <= SIMPLE_SCORE else "cloud"
        result = {
            "simple": decision["simple"],
            "estimate": estimate,
            "model": model,
            "reason": str(decision.get("reason", ""))[:240],
        }
        subtasks = _normalize_subtasks(decision.get("subtasks") or ())
        if not result["simple"] and (len(subtasks) < 2 or node.depth >= context["max_depth"]):
            return self.fallback.assess(node, context)
        if subtasks:
            result["subtasks"] = [asdict(item) for item in subtasks]
        return result


def _estimate(text: str, files: Iterable[str]) -> int:
    count = len(tuple(files))
    score = 3 + max(0, count - 1) * 2
    score += sum(1 for hint in COMPLEX_HINTS if hint in text) * 2
    score += 1 if len(text) > 180 else 0
    score += 1 if text.count(" and ") >= 2 else 0
    score += 1 if text.count(",") >= 2 else 0
    if text.count(",") >= 2 and " and " in text:
        score += 1
    return min(10, score)


def _split(node: Node, context: dict) -> list[dict]:
    """Produce the next layer without a model call."""
    files = list(node.files)
    target = files[0] if files else "the target module"
    base = node.title.strip().rstrip(".")
    common = {
        "detail": node.detail or base,
        "acceptance": node.acceptance,
        "files": tuple(files[:1]),
    }
    layers = [
        {
            "title": f"Reproduce and pin: {base}",
            "detail": f"Write a failing regression case that captures the current behavior of {target}.",
            "acceptance": "A focused regression test fails before the change and names the observed behavior.",
            "files": tuple(files[:1]),
            "estimate": 2,
        },
        {
            "title": f"Implement: {base}",
            "detail": f"Make the smallest change in {target} that satisfies the acceptance criteria.",
            "acceptance": node.acceptance or "The new regression test passes and existing tests stay green.",
            "files": tuple(files[:1]),
            "estimate": min(5, max(3, node.estimate - 1)),
        },
        {
            "title": f"Verify and record: {base}",
            "detail": "Run the focused test plus the full suite; record a receipt with commands and results.",
            "acceptance": "The suite passes and the self-improvement receipt links the task to the diff.",
            "files": (),
            "estimate": 1,
        },
    ]
    if node.depth == 0 and context.get("include_investigation", True):
        layers.insert(
            0,
            {
                "title": f"Investigate: {base}",
                "detail": "Trace the relevant call path and record the files, symbols and evidence that scope the change.",
                "acceptance": "A short evidence note names the call path and the exact edit surface.",
                "files": tuple(files[:3]),
                "estimate": 2,
            },
        )
    return [{**common, **layer} for layer in layers]


class Decomposer:
    """Expand an objective into a bounded tree of routable leaf tasks."""

    def __init__(
        self,
        manager: Manager | None = None,
        *,
        max_depth: int = MAX_DEPTH,
        max_nodes: int = MAX_NODES,
        token_budget: int = MAX_TOKENS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.manager = manager or HeuristicManager()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.token_budget = token_budget
        self.clock = clock
        self.tokens_used = 0
        self.manager_calls = 0
        self.nodes: dict[str, Node] = {}
        self.root_id: str | None = None

    # -- public API -------------------------------------------------------
    def decompose(self, title: str, detail: str = "", *, acceptance: str = "",
                  files: Iterable[str] = (), task_id: str | None = None) -> dict:
        self.nodes.clear()
        self.tokens_used = 0
        self.manager_calls = 0
        root = Node(
            id=task_id or uuid.uuid4().hex[:12],
            title=_clean(title),
            detail=detail.strip(),
            acceptance=acceptance.strip(),
            files=tuple(files),
            estimate=_estimate(f"{title} {detail}", files),
        )
        self.nodes[root.id] = root
        self.root_id = root.id
        self._expand(root)
        return self.plan()

    def plan(self) -> dict:
        leaves = [node for node in self.nodes.values() if node.is_leaf]
        return {
            "root": self.root_id,
            "created_at": self.clock(),
            "max_depth": self.max_depth,
            "nodes": {ident: node.to_dict() for ident, node in self.nodes.items()},
            "leaves": [node.id for node in sorted(leaves, key=lambda n: (n.depth, n.title))],
            "tokens_used": self.tokens_used,
            "manager_calls": self.manager_calls,
            "routing": {
                "local": [n.id for n in leaves if n.model == "local"],
                "cloud": [n.id for n in leaves if n.model != "local"],
            },
        }

    # -- internals --------------------------------------------------------
    def _expand(self, node: Node) -> None:
        if node.depth >= self.max_depth:
            self._make_leaf(node, "local" if node.estimate <= SIMPLE_SCORE else "cloud",
                            "depth limit reached")
            return
        if len(self.nodes) >= self.max_nodes:
            self._make_leaf(node, "cloud", "node budget reached")
            return
        if self.tokens_used >= self.token_budget:
            self._make_leaf(node, "cloud", "token budget reached")
            return

        context = {
            "max_depth": self.max_depth,
            "nodes_remaining": self.max_nodes - len(self.nodes),
            "tokens_remaining": self.token_budget - self.tokens_used,
            "include_investigation": node.depth == 0,
        }
        prompt_tokens = _estimate_prompt(node, context)
        if prompt_tokens > context["tokens_remaining"]:
            self._make_leaf(node, "cloud", "context would exceed token budget")
            return
        self.tokens_used += prompt_tokens
        self.manager_calls += 1
        decision = self.manager.assess(node, context) or {}
        node.estimate = int(decision.get("estimate", node.estimate) or node.estimate)
        node.rationale = str(decision.get("reason", ""))[:240]

        children = _normalize_subtasks(decision.get("subtasks") or [])
        if decision.get("simple") or not children:
            model = decision.get("model") or (
                "local" if node.estimate <= SIMPLE_SCORE else "cloud")
            self._make_leaf(node, str(model), node.rationale or "manager marked simple")
            return

        node.status = "split"
        known = {_fingerprint(child.title): child for child in self.nodes.values()}
        for child in children:
            if len(self.nodes) >= self.max_nodes:
                break
            fingerprint = _fingerprint(child.title)
            if fingerprint in known:
                existing = known[fingerprint].id
                if existing not in node.children:
                    node.children.append(existing)
                continue
            ident = uuid.uuid5(uuid.NAMESPACE_URL, f"{node.id}:{fingerprint}").hex[:12]
            estimate = child.estimate or _estimate(f"{child.title} {child.detail}", child.files)
            item = Node(
                id=ident,
                title=child.title,
                detail=child.detail,
                acceptance=child.acceptance or node.acceptance,
                files=child.files or node.files,
                parent=node.id,
                depth=node.depth + 1,
                estimate=estimate,
            )
            self.nodes[ident] = item
            known[fingerprint] = item
            node.children.append(ident)
        if not node.children:
            self._make_leaf(node, "cloud", "no bounded subtasks")
            return
        for child_id in list(node.children):
            self._expand(self.nodes[child_id])

    def _make_leaf(self, node: Node, model: str, rationale: str) -> None:
        node.status = "leaf"
        node.model = model
        if rationale[:240] not in node.rationale:
            node.rationale = (node.rationale + " · " + rationale).strip(" ·")[:240]


def _normalize_subtasks(items: Iterable) -> list[Subtask]:
    result = []
    for item in items:
        if isinstance(item, Subtask):
            result.append(item)
        elif isinstance(item, dict) and item.get("title"):
            result.append(
                Subtask(
                    title=_clean(str(item["title"])),
                    detail=str(item.get("detail", ""))[:800],
                    acceptance=str(item.get("acceptance", ""))[:800],
                    files=tuple(str(name) for name in item.get("files", ()) if name),
                    estimate=int(item.get("estimate", 0) or 0),
                )
            )
        elif isinstance(item, str) and item.strip():
            result.append(Subtask(title=_clean(item)))
    return result[:6]


def _clean(text: str) -> str:
    return " ".join(text.split())[:240]


def _fingerprint(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _estimate_prompt(node: Node, context: dict) -> int:
    text = node.title + node.detail + " ".join(node.files) + json.dumps(context)
    return max(40, len(text) // 4)


def render(plan: dict) -> str:
    """Render a plan as a compact, human-readable outline."""
    nodes = plan["nodes"]
    lines = []

    def walk(ident: str, depth: int = 0) -> None:
        node = nodes[ident]
        marker = "•" if node["status"] == "leaf" else "▾"
        model = f" [{node['model']}]" if node["model"] else ""
        lines.append(f"{'  ' * depth}{marker} {node['title']}{model}")
        for child in node["children"]:
            walk(child, depth + 1)

    if plan.get("root"):
        walk(plan["root"])
    local = len(plan["routing"]["local"])
    cloud = len(plan["routing"]["cloud"])
    lines.append(f"{len(plan['leaves'])} leaves · {local} local · {cloud} cloud · "
                 f"{plan['tokens_used']} estimated tokens")
    return "\n".join(lines)


def save(plan: dict, state: Path, name: str | None = None) -> Path:
    """Persist a plan atomically under ``state/plans``."""
    directory = state / "plans"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    ident = name or f"{plan['root']}-{int(plan.get('created_at', time.time()))}"
    path = directory / f"{ident}.json"
    fd, temporary = tempfile.mkstemp(prefix=".plan-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def plan_task(state: Path, title: str, detail: str = "", *, acceptance: str = "",
              files: Iterable[str] = (), manager: Manager | None = None,
              max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES,
              token_budget: int = MAX_TOKENS) -> dict:
    """Convenience wrapper: decompose, persist, and return the plan."""
    decomposer = Decomposer(
        manager, max_depth=max_depth, max_nodes=max_nodes, token_budget=token_budget
    )
    plan = decomposer.decompose(
        title, detail, acceptance=acceptance, files=files
    )
    plan["path"] = str(save(plan, state))
    return plan


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Decompose a Talos improvement task.")
    parser.add_argument("task")
    parser.add_argument("--detail", default="")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--state", type=Path, default=Path("state"))
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = plan_task(
        args.state, args.task, args.detail, files=args.file, max_depth=args.max_depth
    )
    print(json.dumps(plan, indent=2, sort_keys=True) if args.json else render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

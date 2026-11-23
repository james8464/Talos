"""Query Talos's Graphify source graph.

The graph is built by ``graphify extract . --code-only`` and stored in
``graphify-out/graph.json``. This module exposes the whole graph to the agent:
search, node lookup, neighbours, shortest paths and stats. Results are compact
so they can be used inside a bounded prompt.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "graphify-out" / "graph.json"


class KnowledgeGraph:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._mtime = None
        self._nodes: dict[str, dict] = {}
        self._links: list[dict] = []
        self._adj: dict[str, list[tuple[str, str]]] = {}
        self._load()

    @property
    def available(self) -> bool:
        return bool(self._nodes)

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime == self._mtime:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        nodes = {}
        for node in payload.get("nodes", ()):
            ident = str(node.get("id") or node.get("label") or "")
            if ident:
                nodes[ident] = node
        links = [link for link in payload.get("links", ()) if link.get("source") and link.get("target")]
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for link in links:
            source, target = str(link["source"]), str(link["target"])
            relation = str(link.get("relation", "related"))
            adjacency.setdefault(source, []).append((target, relation))
            adjacency.setdefault(target, []).append((source, relation))
        self._nodes, self._links, self._adj = nodes, links, adjacency
        self._mtime = mtime

    def stats(self) -> dict:
        communities = {node.get("community_name") for node in self._nodes.values()
                       if node.get("community_name")}
        return {
            "available": self.available,
            "path": str(self.path),
            "nodes": len(self._nodes),
            "edges": len(self._links),
            "communities": len(communities),
            "built_at_commit": None if not self._nodes else self._commit(),
        }

    def _commit(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            value = payload.get("built_at_commit")
            return str(value)[:12] if value else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 1}

    def search(self, query: str, limit: int = 8, *, token_budget: int = 1200) -> list[dict]:
        """Rank nodes by label, file, community and attached symbol names."""
        self._load()
        wanted = self._tokens(query)
        if not wanted or not self._nodes:
            return []
        scored = []
        for ident, node in self._nodes.items():
            label = str(node.get("label", ident))
            haystack = " ".join(str(node.get(key, "")) for key in
                                ("label", "source_file", "community_name", "file_type"))
            words = self._tokens(haystack)
            overlap = len(wanted & words)
            if not overlap:
                continue
            exact = 3 if query.strip().lower() in label.lower() else 0
            score = overlap * 2 + exact + (1 if node.get("source_file") else 0)
            scored.append((score, label, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results = []
        used = 0
        for score, label, node in scored[:max(1, limit)]:
            neighbours = self._adj.get(str(node.get("id") or label), [])[:4]
            excerpt = (
                f"{node.get('source_file', '?')}:{str(node.get('source_location', '?')).lstrip('L')}"
                f" · {node.get('community_name', '?')} · {node.get('file_type', '?')}"
            )
            if neighbours:
                excerpt += " · connects to " + ", ".join(name for name, _ in neighbours)
            if used + len(excerpt) > token_budget:
                break
            used += len(excerpt)
            results.append({
                "store": "code_graph",
                "title": label,
                "score": score,
                "source": node.get("source_file", ""),
                "line": node.get("source_location", ""),
                "excerpt": excerpt,
                "snippet": excerpt,
                "node": node.get("id", label),
            })
        return results

    def node(self, name: str) -> dict | None:
        self._load()
        if name in self._nodes:
            return self._nodes[name]
        lowered = name.lower()
        for ident, node in self._nodes.items():
            if str(node.get("label", "")).lower() == lowered or ident.lower() == lowered:
                return node
        return None

    def neighbours(self, name: str, depth: int = 1) -> list[dict]:
        self._load()
        node = self.node(name)
        if node is None:
            return []
        frontier = deque([(str(node.get("id", name)), 0)])
        seen = {frontier[0][0]}
        found = []
        while frontier and len(found) < 40:
            current, level = frontier.popleft()
            if level >= max(1, depth):
                continue
            for target, relation in self._adj.get(current, ()):
                if target in seen:
                    continue
                seen.add(target)
                entry = self._nodes.get(target, {"id": target, "label": target})
                found.append({"relation": relation, "node": entry.get("id", target),
                              "label": entry.get("label", target),
                              "source_file": entry.get("source_file", "")})
                frontier.append((target, level + 1))
        return found

    def shortest_path(self, start: str, end: str, max_depth: int = 4) -> list[dict]:
        """Shortest undirected path between two node names."""
        self._load()
        first, last = self.node(start), self.node(end)
        if first is None or last is None:
            return []
        source, target = str(first.get("id", start)), str(last.get("id", end))
        queue = deque([(source, [source])])
        seen = {source}
        while queue:
            current, route = queue.popleft()
            if current == target:
                return [
                    {"node": ident, "label": self._nodes.get(ident, {}).get("label", ident),
                     "source_file": self._nodes.get(ident, {}).get("source_file", "")}
                    for ident in route
                ]
            if len(route) > max_depth:
                continue
            for neighbour, _relation in self._adj.get(current, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append((neighbour, [*route, neighbour]))
        return []


graph = KnowledgeGraph()


def search(query: str, limit: int = 8) -> list[dict]:
    return graph.search(query, limit)

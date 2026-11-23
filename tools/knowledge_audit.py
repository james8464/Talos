"""Read-only end-to-end audit of Talos personal context and conversation recall.

The audit seeds an isolated temporary state, asks the unified cognition layer for
the seeded personal fact, transcript turn, goal and knowledge-index record, then
checks that every knowledge store is reported and that a Graphify query still
returns source-file/line citations. It never writes to the repository ``state/``
directory: the default target is a fresh temporary directory and an explicit
``state/`` path is rejected.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from talos_core import knowledge_graph
from talos_core.cognition import cognitive_engine, goal_store
from talos_core.memory import personal_facts, personal_index, transcript_memory
from talos_core.memory.agent_memory import open_memory
from talos_core.memory.transcript_memory import TEXT_SESSION

AUDIT_SESSION = "knowledge-audit"
AUDIT_GOAL_ID = "00000000-0000-0000-0000-00000000a001"
AUDIT_FACT_SUBJECT = "workshop code"
AUDIT_FACT_VALUE = "Gamma-9-Delta"
AUDIT_TRANSCRIPT = "The orbital telemetry archive lives on channel 77."
AUDIT_INDEX_KEY = "audit-note-1"
AUDIT_INDEX_TEXT = "Arc Reactor telemetry uses vibranium lattice on channel 77."
AUDIT_GOAL = "Calibrate vibration dampers"
DEFAULT_GRAPH_QUERY = "decomposition manager"
GRAPH_LIMIT = 4

EXPECTED_STORES = (
    "personal_index",
    "ai_memory_reviews",
    "memory_connections",
    "vault",
    "code_graph",
    "transcripts",
    "personal_facts",
    "commitments",
    "context_evidence",
    "goals",
    "inbox",
    "background_work",
    "turn_critiques",
    "cognitive_intentions",
)
REQUIRED_AVAILABLE = (
    "personal_index",
    "ai_memory_reviews",
    "memory_connections",
    "vault",
    "code_graph",
)

REAL_STATE = (ROOT / "state").resolve()


@contextmanager
def _bound_graph(graph: knowledge_graph.KnowledgeGraph):
    """Make the cognition layer consult ``graph`` for this audit only."""
    previous = knowledge_graph.graph
    knowledge_graph.graph = graph
    try:
        yield graph
    finally:
        knowledge_graph.graph = previous


def _guard_state(state: Path | str) -> Path:
    state = Path(state)
    try:
        state.resolve().relative_to(REAL_STATE)
    except ValueError:
        return state
    raise ValueError("Refusing to touch the real state directory; pass a temporary state path.")


def seed_state(state: Path | str) -> None:
    """Seed a temporary SQLite state through the same APIs the runtime uses."""
    state = _guard_state(state)
    state.mkdir(parents=True, exist_ok=True)
    with closing(open_memory(state / "agent.sqlite3")) as db:
        cognitive_engine.ensure_tables(db)
        reply = personal_facts.reply(
            db,
            f"remember that my {AUDIT_FACT_SUBJECT} is {AUDIT_FACT_VALUE}",
            TEXT_SESSION,
            remember=True,
            apply=True,
            follow_up=False,
        )
        if "remember" not in str(reply).casefold():
            raise RuntimeError(f"Personal fact seed was not persisted: {reply}")
        transcript_memory.remember(
            db,
            AUDIT_SESSION,
            [{"role": "user", "content": AUDIT_TRANSCRIPT}],
        )
        with db:
            goal_store.save_goal(
                db,
                {
                    "id": AUDIT_GOAL_ID,
                    "outcome": AUDIT_GOAL,
                    "topic": "Dampers",
                    "deadline": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
                },
            )
    personal_index.sync(
        state,
        state / "Library",
        knowledge=(("Notes", ((AUDIT_INDEX_KEY, AUDIT_INDEX_TEXT),)),),
    )


def _citation(hit: dict) -> dict:
    return {
        "store": str(hit.get("store", "")),
        "title": str(hit.get("title", "")),
        "source": str(hit.get("source", "")),
        "snippet": str(hit.get("snippet", "")),
    }


def _find_citation(result: dict, stores: tuple[str, ...], needle: str) -> dict:
    needle = needle.casefold()
    for hit in result.get("results", ()):
        store = str(hit.get("store", ""))
        if not any(store == name or store.startswith(name + ":") for name in stores):
            continue
        haystack = " ".join(str(hit.get(key, "")) for key in ("title", "snippet", "excerpt"))
        if needle not in haystack.casefold():
            continue
        citation = _citation(hit)
        return {
            "ok": all(citation[key] for key in ("store", "title", "source", "snippet")),
            "citation": citation,
        }
    return {"ok": False, "citation": None, "results": len(result.get("results", ()))}


def _store_report(status: dict) -> dict:
    stores = status.get("stores") or {}
    present = [name for name in EXPECTED_STORES if name in stores]
    missing = [name for name in EXPECTED_STORES if name not in stores]
    unavailable = [name for name in REQUIRED_AVAILABLE if not (stores.get(name) or {}).get("available")]
    return {
        "expected": len(EXPECTED_STORES),
        "reported": len(present),
        "missing": missing,
        "unavailable": unavailable,
        "code_graph": bool((stores.get("code_graph") or {}).get("available")),
        "counts": {name: int((stores.get(name) or {}).get("count", 0)) for name in EXPECTED_STORES},
    }


def _graph_report(graph: knowledge_graph.KnowledgeGraph, query: str) -> dict:
    hits = graph.search(query, limit=GRAPH_LIMIT)
    citations = [{"source": str(hit.get("source", "")), "line": str(hit.get("line", ""))} for hit in hits]
    ok = bool(hits) and all(item["source"] and item["line"] for item in citations)
    return {"query": query, "count": len(hits), "ok": ok, "citations": citations}


def audit(
    state: Path | str,
    *,
    graph_path: Path | str | None = None,
    graph_query: str = DEFAULT_GRAPH_QUERY,
) -> dict:
    """Audit an already-seeded state and return a compact JSON-serialisable report."""
    state = _guard_state(state)
    graph = knowledge_graph.KnowledgeGraph(graph_path) if graph_path is not None else knowledge_graph.graph
    with _bound_graph(graph), closing(open_memory(state / "agent.sqlite3")) as db:
        cognitive_engine.ensure_tables(db)
        status = cognitive_engine.knowledge_status(db, state=state)
        retrievals = {
            "personal_fact": _find_citation(
                cognitive_engine.query_knowledge(db, AUDIT_FACT_SUBJECT, state=state),
                ("personal_facts",),
                AUDIT_FACT_VALUE,
            ),
            "transcript": _find_citation(
                cognitive_engine.query_knowledge(db, "orbital telemetry channel 77", state=state),
                ("transcripts",),
                "channel 77",
            ),
            "knowledge_index": _find_citation(
                cognitive_engine.query_knowledge(db, "Arc Reactor vibranium lattice", state=state),
                ("personal_index",),
                "vibranium",
            ),
            "goal": _find_citation(
                cognitive_engine.query_knowledge(db, AUDIT_GOAL, state=state),
                ("goals",),
                AUDIT_GOAL.casefold(),
            ),
        }
        stores = _store_report(status)
        graphify = _graph_report(graph, graph_query)

    ok = (
        not stores["missing"]
        and not stores["unavailable"]
        and all(item["ok"] for item in retrievals.values())
        and graphify["ok"]
    )
    return {
        "ok": ok,
        "state": str(state),
        "stores": stores,
        "retrieval": retrievals,
        "graphify": graphify,
    }


def _reject_real_state(state: Path) -> None:
    try:
        _guard_state(state)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def _run(state: Path, args: argparse.Namespace) -> int:
    try:
        seed_state(state)
        report = audit(
            state,
            graph_path=args.graph_path,
            graph_query=args.query,
        )
    except Exception as error:
        report = {
            "ok": False,
            "state": str(state),
            "error": f"{type(error).__name__}: {error}",
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0 if report.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        type=Path,
        help="Temporary state directory to seed and audit (defaults to a new temp dir).",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        dest="graph_path",
        help="Graphify graph.json to query (defaults to the repository graph).",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_GRAPH_QUERY,
        help="Graphify query used to verify source-file/line citations.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.state is None:
        with tempfile.TemporaryDirectory(prefix="talos-knowledge-audit-") as tmp:
            return _run(Path(tmp), args)
    _reject_real_state(args.state)
    return _run(args.state, args)


if __name__ == "__main__":
    raise SystemExit(main())

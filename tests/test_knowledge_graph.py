import json
import tempfile
import unittest
from pathlib import Path

from talos_core import knowledge_graph
from talos_core.cognition import cognitive_engine
from talos_core.memory.agent_memory import open_memory


def fixture(path: Path):
    payload = {
        "nodes": [
            {"id": "decompose", "label": "decomposition.decompose", "source_file": "talos_core/self_improvement/decomposition.py",
             "source_location": "L259", "community_name": "decomposition.py", "file_type": "code"},
            {"id": "manager", "label": "ModelManager.assess", "source_file": "talos_core/self_improvement/decomposition.py",
             "source_location": "L115", "community_name": "decomposition.py", "file_type": "code"},
            {"id": "router", "label": "route_leaf", "source_file": "talos_core/self_improvement/decomposition.py",
             "source_location": "L400", "community_name": "decomposition.py", "file_type": "code"},
            {"id": "computer", "label": "ComputerUse.click", "source_file": "talos_core/mac/computer_use.py",
             "source_location": "L240", "community_name": "computer_use.py", "file_type": "code"},
        ],
        "links": [
            {"source": "decompose", "target": "manager", "relation": "calls"},
            {"source": "manager", "target": "router", "relation": "calls"},
            {"source": "router", "target": "computer", "relation": "uses"},
        ],
        "built_at_commit": "abc123",
    }
    path.write_text(json.dumps(payload))
    return path


class KnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.path = fixture(Path(self.enterContext(tempfile.TemporaryDirectory())) / "graph.json")
        self.graph = knowledge_graph.KnowledgeGraph(self.path)

    def test_stats_cover_every_node_and_edge(self):
        stats = self.graph.stats()
        self.assertTrue(stats["available"])
        self.assertEqual((stats["nodes"], stats["edges"]), (4, 3))
        self.assertEqual(stats["built_at_commit"], "abc123")

    def test_search_returns_citable_source_locations(self):
        hit = self.graph.search("model manager assess", limit=1)[0]
        self.assertEqual(hit["store"], "code_graph")
        self.assertIn("decomposition.py:115", hit["excerpt"])
        self.assertEqual(hit["node"], "manager")

    def test_node_neighbours_and_shortest_path(self):
        self.assertEqual(self.graph.node("manager")["label"], "ModelManager.assess")
        names = {item["node"] for item in self.graph.neighbours("decompose", depth=2)}
        self.assertEqual(names, {"manager", "router"})
        route = [item["node"] for item in self.graph.shortest_path("decompose", "computer")]
        self.assertEqual(route, ["decompose", "manager", "router", "computer"])

    def test_unavailable_graph_degrades_to_empty_results(self):
        missing = knowledge_graph.KnowledgeGraph(self.path.parent / "missing.json")
        self.assertFalse(missing.available)
        self.assertEqual(missing.search("anything"), [])

    def test_unified_knowledge_reports_the_code_graph_store(self):
        with open_memory(self.path.parent / "agent.sqlite3") as db:
            result = cognitive_engine.query_knowledge(db, "decomposition manager", limit=6)
        self.assertIn("code_graph", result["stores_searched"])
        status = cognitive_engine.knowledge_status(db)
        self.assertIn("code_graph", status["stores"])


if __name__ == "__main__":
    unittest.main()

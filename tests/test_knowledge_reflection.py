import tempfile
import unittest
from pathlib import Path

from talos_core import knowledge_reflection as reflection
from talos_core.memory.agent_memory import open_memory
from talos_core.memory.transcript_memory import remember


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = open_memory(self.state / "agent.sqlite3")
        self.addCleanup(self.db.close)
        remember(self.db, "text", [
            {"role": "user", "content": "The vibranium lattice in the Arc Reactor needs calibration."},
            {"role": "assistant", "content": "I noted the calibration."},
        ])

    def test_conversation_becomes_weighted_related_context(self):
        found = reflection.reflect(self.db, self.state, limit=4)
        self.assertTrue(found)
        self.assertTrue(any("vibranium" in item.excerpt.lower() for item in found))
        self.assertGreaterEqual(reflection.count(self.db), 1)
        top = reflection.summary(self.db)[0]
        self.assertIn("store", top)
        self.assertGreater(top["weight"], 0)

    def test_reflection_is_idempotent(self):
        first = reflection.reflect(self.db, self.state, limit=4)
        before = reflection.count(self.db)
        second = reflection.reflect(self.db, self.state, limit=4)
        self.assertEqual(reflection.count(self.db), before)
        self.assertEqual([item.store for item in first], [item.store for item in second])

    def test_superpose_collapses_duplicates_to_the_heaviest(self):
        items = [
            reflection.Hypothesis("a", "transcripts", "s", "t", "low", 1.0),
            reflection.Hypothesis("a", "transcripts", "s", "t", "high", 3.0),
            reflection.Hypothesis("b", "code_graph", "s2", "t2", "other", 2.0),
        ]
        collapsed = reflection.superpose(items)
        self.assertEqual(len(collapsed), 2)
        self.assertEqual(collapsed[0].excerpt, "high")

    def test_empty_store_returns_no_relations(self):
        with open_memory(self.state / "empty.sqlite3") as empty:
            self.assertEqual(reflection.reflect(empty, self.state), [])
            self.assertEqual(reflection.count(empty), 0)


if __name__ == "__main__":
    unittest.main()

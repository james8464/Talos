"""Tests for the personal-context and conversation-recall audit tool."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "knowledge_audit.py"

_SPEC = importlib.util.spec_from_file_location("knowledge_audit", TOOL)
assert _SPEC and _SPEC.loader
knowledge_audit = importlib.util.module_from_spec(_SPEC)
sys.modules["knowledge_audit"] = knowledge_audit
_SPEC.loader.exec_module(knowledge_audit)


def _graph_fixture(path: Path) -> Path:
    payload = {
        "nodes": [
            {
                "id": "decompose",
                "label": "decomposition.decompose",
                "source_file": "talos_core/self_improvement/decomposition.py",
                "source_location": "L259",
                "community_name": "decomposition.py",
                "file_type": "code",
            },
            {
                "id": "manager",
                "label": "ModelManager.assess",
                "source_file": "talos_core/self_improvement/decomposition.py",
                "source_location": "L115",
                "community_name": "decomposition.py",
                "file_type": "code",
            },
        ],
        "links": [{"source": "decompose", "target": "manager", "relation": "calls"}],
        "built_at_commit": "audit-fixture",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class KnowledgeAuditTests(unittest.TestCase):
    def _state(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"

    def _graph(self) -> Path:
        return _graph_fixture(Path(self.enterContext(tempfile.TemporaryDirectory())) / "graph.json")

    def test_success_reports_every_store_and_retrieves_context_with_citations(self):
        state = self._state()
        knowledge_audit.seed_state(state)

        report = knowledge_audit.audit(state, graph_path=self._graph())

        self.assertTrue(report["ok"], report)
        stores = report["stores"]
        self.assertEqual(stores["missing"], [])
        self.assertEqual(stores["unavailable"], [])
        self.assertEqual(stores["reported"], stores["expected"])
        self.assertEqual(stores["expected"], len(knowledge_audit.EXPECTED_STORES))
        self.assertIn("code_graph", knowledge_audit.EXPECTED_STORES)
        self.assertTrue(stores["code_graph"])
        self.assertGreaterEqual(stores["counts"]["personal_facts"], 1)
        self.assertGreaterEqual(stores["counts"]["transcripts"], 1)
        self.assertGreaterEqual(stores["counts"]["goals"], 1)
        self.assertGreaterEqual(stores["counts"]["personal_index"], 1)
        self.assertGreaterEqual(stores["counts"]["vault"], 1)

        fact = report["retrieval"]["personal_fact"]
        self.assertTrue(fact["ok"])
        self.assertEqual(fact["citation"]["store"], "personal_facts")
        self.assertIn(knowledge_audit.AUDIT_FACT_VALUE, fact["citation"]["snippet"])
        self.assertTrue(fact["citation"]["source"])

        transcript = report["retrieval"]["transcript"]
        self.assertTrue(transcript["ok"])
        self.assertEqual(transcript["citation"]["store"], "transcripts")
        self.assertIn("channel 77", transcript["citation"]["snippet"])
        self.assertTrue(transcript["citation"]["source"])

        index = report["retrieval"]["knowledge_index"]
        self.assertTrue(index["ok"])
        self.assertTrue(index["citation"]["store"].startswith("personal_index"))
        self.assertIn("vibranium", index["citation"]["snippet"])

        goal = report["retrieval"]["goal"]
        self.assertTrue(goal["ok"])
        self.assertEqual(goal["citation"]["store"], "goals")

        graphify = report["graphify"]
        self.assertTrue(graphify["ok"])
        self.assertGreaterEqual(graphify["count"], 1)
        for citation in graphify["citations"]:
            self.assertTrue(citation["source"].endswith(".py"))
            self.assertRegex(citation["line"], r"^L\d+$")

    def test_missing_personal_index_degrades_without_losing_conversation_recall(self):
        state = self._state()
        knowledge_audit.seed_state(state)
        index = state / "personal-index-v2.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            (state / f"{index.name}{suffix}").unlink(missing_ok=True)
        self.assertFalse(index.exists())

        report = knowledge_audit.audit(state, graph_path=self._graph())

        self.assertFalse(report["ok"])
        self.assertIn("personal_index", report["stores"]["unavailable"])
        self.assertIn("ai_memory_reviews", report["stores"]["unavailable"])
        self.assertIn("memory_connections", report["stores"]["unavailable"])
        self.assertFalse(report["retrieval"]["knowledge_index"]["ok"])
        self.assertTrue(report["retrieval"]["personal_fact"]["ok"])
        self.assertTrue(report["retrieval"]["transcript"]["ok"])
        self.assertEqual(report["stores"]["missing"], [])
        self.assertTrue(report["stores"]["code_graph"])

    def test_cli_prints_compact_json_and_uses_exit_status(self):
        state = self._state()
        success = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--state",
                str(state),
                "--graph",
                str(self._graph()),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stdout.strip().count("\n"), 0)
        payload = json.loads(success.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stores"]["code_graph"])
        self.assertTrue(payload["retrieval"]["personal_fact"]["ok"])

        missing_state = self._state()
        failure = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--state",
                str(missing_state),
                "--graph",
                str(missing_state / "missing-graph.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failure.returncode, 1, failure.stderr)
        payload = json.loads(failure.stdout)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["stores"]["code_graph"])
        self.assertFalse(payload["graphify"]["ok"])


if __name__ == "__main__":
    unittest.main()

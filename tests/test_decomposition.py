import json
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import decomposition as dec


class ScriptedManager:
    """Deterministic stand-in for an AI manager."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = []

    def assess(self, node, context):
        self.calls.append((node.title, node.depth, dict(context)))
        for prefix, decision in self.decisions.items():
            if node.title.startswith(prefix):
                return decision
        return {"simple": True, "estimate": 2, "model": "local", "reason": "default"}


class DecompositionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"

    def test_multilayer_tree_stops_when_manager_marks_subtasks_simple(self):
        manager = ScriptedManager({
            "Harden the voice pipeline": {
                "simple": False,
                "estimate": 7,
                "model": "cloud",
                "reason": "spans several stages",
                "subtasks": [
                    {"title": "Trace the voice call path", "files": ["talos_core/room/room_voice.py"]},
                    {"title": "Harden the audio buffer", "files": ["mac/SpeechAudioBufferNormalizer.swift"]},
                ],
            },
            "Trace the voice call path": {
                "simple": False,
                "estimate": 5,
                "reason": "needs a map",
                "subtasks": [
                    {"title": "Read room_voice", "files": ["talos_core/room/room_voice.py"]},
                    {"title": "Read the Swift normalizer", "files": ["mac/SpeechAudioBufferNormalizer.swift"]},
                ],
            },
        })
        plan = dec.Decomposer(manager).decompose(
            "Harden the voice pipeline", "Fix dropouts from capture to playback."
        )
        nodes = plan["nodes"]
        root = nodes[plan["root"]]
        self.assertEqual(root["status"], "split")
        self.assertEqual(len(root["children"]), 2)
        trace = nodes[root["children"][0]]
        self.assertEqual(trace["status"], "split")
        self.assertEqual(len(trace["children"]), 2)
        self.assertTrue(all(nodes[ident]["status"] == "leaf" for ident in plan["leaves"]))
        self.assertEqual(len(plan["leaves"]), 3)
        self.assertEqual(plan["manager_calls"], len(plan["nodes"]))
        self.assertGreaterEqual(plan["tokens_used"], 80)

    def test_leaf_routing_prefers_local_for_small_bounded_tasks(self):
        plan = dec.Decomposer().decompose("Fix a typo in the log message", files=["talos_core/runtime/maintenance.py"])
        self.assertEqual(len(plan["leaves"]), 1)
        leaf = plan["nodes"][plan["leaves"][0]]
        self.assertEqual(leaf["model"], "local")
        self.assertEqual(plan["manager_calls"], 1)

    def test_large_objective_routes_implementation_to_cloud_and_verify_locally(self):
        plan = dec.Decomposer().decompose(
            "Refactor the whole memory pipeline across every module and migrate storage",
            files=["talos_core/memory/personal_index.py"],
        )
        leaves = [plan["nodes"][ident] for ident in plan["leaves"]]
        self.assertGreaterEqual(len(leaves), 4)
        self.assertTrue(any(leaf["model"] == "cloud" for leaf in leaves))
        self.assertTrue(any(leaf["model"] == "local" for leaf in leaves))
        self.assertTrue(all(leaf["status"] == "leaf" for leaf in leaves))
        # Bounded by depth and node limits.
        self.assertLessEqual(max(leaf["depth"] for leaf in leaves), dec.MAX_DEPTH)
        self.assertLessEqual(len(plan["nodes"]), dec.MAX_NODES)

    def test_token_budget_stops_decomposition_without_overspending(self):
        plan = dec.Decomposer(token_budget=50).decompose(
            "Refactor every subsystem of the assistant and migrate all storage",
            files=[f"talos_core/memory/module_{i}.py" for i in range(8)],
        )
        self.assertEqual(len(plan["leaves"]), 1)
        self.assertEqual(plan["nodes"][plan["leaves"][0]]["model"], "cloud")
        self.assertLessEqual(plan["tokens_used"], 50)
        self.assertEqual(plan["manager_calls"], 0)

    def test_duplicate_subtasks_are_merged(self):
        manager = ScriptedManager({
            "Broad task": {
                "simple": False,
                "subtasks": ["Add a test", "Add a test", "add a  TEST"],
            },
        })
        plan = dec.Decomposer(manager).decompose("Broad task")
        root = plan["nodes"][plan["root"]]
        self.assertEqual(len(root["children"]), 1)

    def test_plan_persists_atomically_and_renders_outline(self):
        plan = dec.plan_task(self.state, "Fix the reminder typo", files=["talos_core/cognition/recurring_reminders.py"])
        path = Path(plan["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())["root"], plan["root"])
        outline = dec.render(plan)
        self.assertIn("Fix the reminder typo", outline)
        self.assertIn("1 leaves", outline)
        self.assertIn("local", outline)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

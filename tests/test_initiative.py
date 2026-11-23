import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.self_improvement import initiative


class InitiativeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.state.mkdir()
        (self.root / "talos_core/self_improvement").mkdir(parents=True)
        (self.root / "talos_core/self_improvement/health.py").write_text("VALUE = 1\n")
        (self.root / "tests").mkdir()
        (self.state / "health.json").write_text(json.dumps({
            "ok": False,
            "checks": [{"name": "tests", "ok": False, "detail": "failed",
                        "items": ["talos_core/self_improvement/health.py:1 broken"]}],
        }))
        graph = self.enterContext(patch.object(initiative, "_graph_ideas", return_value=[]))
        graph.start()

    def queue(self):
        return [json.loads(line) for line in
                (self.state / "feature-requests.jsonl").read_text().splitlines()]

    def test_health_failure_becomes_a_decomposed_proposal(self):
        report = initiative.cycle(self.state, self.root, limit=2)
        self.assertEqual(report["count"], 1)
        entry = self.queue()[0]
        self.assertEqual(entry["origin"], "initiative:health")
        self.assertIn("self-check failure", entry["feature"])
        plan = json.loads(Path(entry["plan_path"]).read_text())
        self.assertGreaterEqual(len(plan["leaves"]), 1)
        self.assertIn("local", plan["routing"])
        self.assertTrue((self.state / "initiatives.jsonl").is_file())

    def test_duplicate_titles_are_not_queued_again(self):
        initiative.cycle(self.state, self.root, limit=2)
        before = len(self.queue())
        second = initiative.cycle(self.state, self.root, limit=2)
        self.assertEqual(second["count"], 0)
        self.assertEqual(len(self.queue()), before)

    def test_model_can_propose_a_new_feature(self):
        def infer(prompt):
            self.assertIn("autonomous local Mac assistant", prompt)
            return json.dumps({
                "title": "Add a calendar heatmap",
                "rationale": "The owner keeps asking about meeting density.",
                "acceptance": "A fixture renders seven days of density.",
                "files": ["talos_core/room/room_service.py"],
            })

        report = initiative.cycle(self.state, self.root, infer=infer, limit=1)
        self.assertEqual(report["accepted"][0]["source"], "model")
        self.assertEqual(self.queue()[0]["feature"], "Add a calendar heatmap")

    def test_invalid_model_output_is_ignored_without_stopping_evidence(self):
        initiative.cycle(self.state, self.root, infer=lambda prompt: "not json", limit=1)
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.queue()[0]["origin"], "initiative:health")

    def test_limit_is_enforced(self):
        report = initiative.cycle(self.state, self.root, limit=1)
        self.assertEqual(report["count"], 1)


if __name__ == "__main__":
    unittest.main()

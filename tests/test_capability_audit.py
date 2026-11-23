import json
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import capability_audit


class CapabilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        (self.root / "tests").mkdir()
        (self.root / "tests/test_tools.py").write_text("def test_click(): pass\n")
        (self.root / "talos_core/self_improvement").mkdir(parents=True)
        (self.root / "talos_core/self_improvement/initiative.py").write_text("VALUE = 1\n")
        upstream = self.state / "openclaw-src/src/agents/tools"
        upstream.mkdir(parents=True)
        (upstream / "agents-list-tool.ts").write_text("export const x = 1\n")
        (upstream / "browser-tool.ts").write_text("export const y = 1\n")
        hermes = self.state / "hermes-src/tools"
        hermes.mkdir(parents=True)
        (hermes / "browser_tool.py").write_text("x = 1\n")

    def test_upstream_inventory_finds_tool_shaped_files(self):
        inventory = capability_audit.upstream_capabilities(self.state)
        self.assertIn("OpenClaw", inventory)
        self.assertIn("agents-list", inventory["OpenClaw"])
        self.assertIn("browser", inventory["Hermes"])

    def test_report_records_tested_and_untested_tools(self):
        payload = capability_audit.report(self.root, self.state)
        self.assertIn("summary", payload)
        self.assertIn("capabilities", payload)
        self.assertGreater(payload["summary"]["total"], 0)
        self.assertTrue((self.state / "capability-audit.json").is_file())
        self.assertIn("gaps", payload)

    def test_gap_queue_creates_decomposed_proposals(self):
        queued = capability_audit.queue_gaps(self.root, self.state, limit=1)
        self.assertEqual(len(queued), 1)
        entries = [json.loads(line) for line in
                   (self.state / "feature-requests.jsonl").read_text().splitlines()]
        self.assertTrue(entries[0]["origin"].startswith("initiative:gap:"))
        plan = json.loads(Path(entries[0]["plan_path"]).read_text())
        self.assertGreaterEqual(len(plan["leaves"]), 1)

    def test_repeated_report_is_stable(self):
        first = capability_audit.report(self.root, self.state)
        second = capability_audit.report(self.root, self.state)
        self.assertEqual(first["summary"], second["summary"])


if __name__ == "__main__":
    unittest.main()

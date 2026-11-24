import json
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import capability_audit
from talos_core.self_improvement.capability_manifest import Mapping


class CapabilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        (self.root / "tests").mkdir()
        (self.root / "tests/test_tools.py").write_text(
            "def test_example_behavior():\n    pass\n")
        (self.root / "talos_core/self_improvement").mkdir(parents=True)
        (self.root / "talos_core/self_improvement/initiative.py").write_text("VALUE = 1\n")
        self.upstream = self.state / "openclaw-src/src/agents/tools"
        self.upstream.mkdir(parents=True)
        (self.upstream / "agents-list-tool.ts").write_text("export const x = 1\n")
        (self.upstream / "browser-tool.ts").write_text("export const y = 1\n")
        hermes = self.state / "hermes-src/tools"
        hermes.mkdir(parents=True)
        (hermes / "browser_tool.py").write_text("x = 1\n")

    def gap_named(self, ranked, name):
        return next((gap for gap in ranked if gap.name == name), None)

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
        self.assertIn("upstream_total", payload["summary"])
        self.assertIn("upstream_coverage_pct", payload["summary"])
        self.assertIn("unmapped_upstream", payload["summary"])
        summary = payload["summary"]
        self.assertEqual(
            summary["upstream_covered"] + summary["upstream_gaps"],
            summary["upstream_total"])
        self.assertEqual(len(payload["gaps"]), summary["upstream_gaps"]
                         + sum(1 for gap in payload["gaps"] if gap["project"] == "Talos"))
        for item in payload["capabilities"]:
            self.assertIn("evidence", item)
            self.assertIn("coverage_status", item)

    def test_fake_upstream_tool_without_mapping_is_a_gap(self):
        (self.upstream / "fake-widget-tool.ts").write_text("export const z = 1\n")
        ranked = capability_audit.gaps(self.root, self.state)
        gap = self.gap_named(ranked, "fake-widget")
        self.assertIsNotNone(gap)
        self.assertEqual(gap.project, "OpenClaw")
        self.assertEqual(gap.coverage_status, "unmapped")
        self.assertEqual(gap.mapped_local, "")
        self.assertIn("no declared Talos mapping", gap.evidence)

    def test_raw_name_overlap_does_not_suppress_an_upstream_gap(self):
        # `doctor` overlaps the local `cli doctor` name and a real doctor test,
        # but with no manifest entry it is still an unmapped gap.
        (self.upstream / "doctor-tool.ts").write_text("export const z = 1\n")
        (self.root / "tests/test_doctor.py").write_text(
            "def test_doctor_reports_a_clean_tree():\n    pass\n")
        ranked = capability_audit.gaps(self.root, self.state)
        gap = self.gap_named(ranked, "doctor")
        self.assertIsNotNone(gap)
        self.assertEqual(gap.coverage_status, "unmapped")

    def test_declared_mapping_with_real_test_is_not_a_gap(self):
        (self.upstream / "fake-widget-tool.ts").write_text("export const z = 1\n")
        (self.root / "tests/test_doctor.py").write_text(
            "def test_doctor_reports_a_clean_tree():\n    pass\n")
        manifest = {"OpenClaw": {"fake-widget": Mapping("cli:doctor")}}
        ranked = capability_audit.gaps(self.root, self.state, manifest=manifest)
        self.assertIsNone(self.gap_named(ranked, "fake-widget"))

    def test_declared_mapping_without_a_test_is_an_untested_gap(self):
        (self.upstream / "fake-widget-tool.ts").write_text("export const z = 1\n")
        manifest = {"OpenClaw": {"fake-widget": Mapping("cli:doctor")}}
        ranked = capability_audit.gaps(self.root, self.state, manifest=manifest)
        gap = self.gap_named(ranked, "fake-widget")
        self.assertIsNotNone(gap)
        self.assertEqual(gap.coverage_status, "untested")
        self.assertEqual(gap.mapped_local, "cli:doctor")

    def test_declared_mapping_to_missing_local_is_unmapped(self):
        (self.upstream / "fake-widget-tool.ts").write_text("export const z = 1\n")
        manifest = {"OpenClaw": {"fake-widget": Mapping("feature:not_a_talos_feature")}}
        ranked = capability_audit.gaps(self.root, self.state, manifest=manifest)
        gap = self.gap_named(ranked, "fake-widget")
        self.assertIsNotNone(gap)
        self.assertEqual(gap.coverage_status, "unmapped")
        self.assertIn("not implemented", gap.evidence)

    def test_substring_mentions_alone_are_not_coverage(self):
        (self.root / "tests/test_mentions.py").write_text(
            "def test_unrelated_behavior():\n"
            '    """click and type_text are mentioned in prose."""\n'
            '    note = "click type_text"\n'
            "    assert note\n")
        self.assertEqual(
            capability_audit.evidence_for(self.root, "computer_use:click"), "")
        self.assertEqual(
            capability_audit.evidence_for(self.root, "computer_use:type_text"), "")

    def test_identifier_in_a_test_name_is_coverage(self):
        (self.root / "tests/test_click.py").write_text(
            "def test_click_uses_trusted_events():\n    pass\n")
        evidence = capability_audit.evidence_for(self.root, "computer_use:click")
        self.assertEqual(evidence, "tests/test_click.py::test_click_uses_trusted_events")

    def test_explicit_capability_marker_is_coverage(self):
        (self.root / "tests/test_marker.py").write_text(
            "# CAPABILITY: cli:doctor\n"
            "def test_command_works():\n    pass\n")
        evidence = capability_audit.evidence_for(self.root, "cli:doctor")
        self.assertTrue(evidence.startswith("tests/test_marker.py:"))

    def test_declared_local_evidence_must_name_a_real_test(self):
        stale = {"cli:doctor": "tests/test_doctor.py::test_missing"}
        self.assertEqual(capability_audit.evidence_for(
            self.root, "cli:doctor", declared=stale), "")
        (self.root / "tests/test_doctor.py").write_text(
            "def test_doctor_reports_a_clean_tree():\n    pass\n")
        declared = {"cli:doctor": "tests/test_doctor.py::test_doctor_reports_a_clean_tree"}
        self.assertEqual(
            capability_audit.evidence_for(self.root, "cli:doctor", declared=declared),
            "tests/test_doctor.py::test_doctor_reports_a_clean_tree")

    def test_gap_queue_creates_decomposed_proposals(self):
        queued = capability_audit.queue_gaps(self.root, self.state, limit=1)
        self.assertEqual(len(queued), 1)
        entries = [json.loads(line) for line in
                   (self.state / "feature-requests.jsonl").read_text().splitlines()]
        self.assertTrue(entries[0]["origin"].startswith("initiative:gap:"))
        plan = json.loads(Path(entries[0]["plan_path"]).read_text())
        self.assertGreaterEqual(len(plan["leaves"]), 1)

    def test_top_gaps_returns_ranked_prefix(self):
        ranked = capability_audit.gaps(self.root, self.state)
        self.assertTrue(ranked)
        scores = [gap.score for gap in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))
        top = capability_audit.top_gaps(self.root, self.state, limit=2)
        self.assertEqual([gap.name for gap in top], [gap.name for gap in ranked[:2]])
        self.assertEqual(len(capability_audit.top_gaps(self.root, self.state, limit=1)), 1)
        self.assertEqual(capability_audit.top_gaps(self.root, self.state, limit=0), [])

    def test_repeated_report_is_stable(self):
        first = capability_audit.report(self.root, self.state)
        second = capability_audit.report(self.root, self.state)
        self.assertEqual(first["summary"], second["summary"])


if __name__ == "__main__":
    unittest.main()

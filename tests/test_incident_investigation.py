"""Source-grounded incident diagnosis, including an optional real CPU inference."""
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from talos_core.self_improvement import incident_investigation as investigate
from talos_core.self_improvement import local_source_model
from talos_core.self_improvement import self_diagnosis
from talos_core.cognition.conversation_activity import foreground


class IncidentInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / "state"
        (self.root / "tests").mkdir()
        self.source = self.root / "talos_core/runtime/voice_recovery.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("def recent(receipts):\n    return receipts[:5]\n")
        test = self.root / "tests/test_voice_recovery.py"
        (self.root / "talos_core/runtime").mkdir(parents=True, exist_ok=True)
        (self.root / "talos_core/__init__.py").write_text("")
        (self.root / "talos_core/runtime/__init__.py").write_text("")
        test.write_text(
            "import unittest\nfrom talos_core.runtime.voice_recovery import recent\n"
            "class VoiceRecoveryTests(unittest.TestCase):\n"
            "    def test_complaint_and_receipts_survive_restart(self):\n"
            "        receipts = ['older', 'newer']\n"
            "        self.assertEqual(recent(receipts)[0], 'newer')\n"
        )

    def report(self):
        ident = self_diagnosis.report(self.state, "Room report", "playback private never-record-this-text")
        for _ in range(2):
            self_diagnosis.tick(self.root, self.state)
        self.assertEqual(self_diagnosis.handoff(self.state)[0]["status"], "repeatable fixture failure")
        return ident

    def tick(self, when=1000):
        return investigate.tick(self.state, root=self.root, clock=lambda: when)

    def receipts(self):
        folder = self.state / "self-repair/investigations"
        return [json.loads(f.read_text()) for f in folder.glob("*-*.json")]

    def test_probes_then_cites_once_across_restarts_and_reopens_on_source_change(self):
        ident = self.report()
        raw = json.dumps({"source": 1, "assertion": 1,
                          "hypothesis": "Older receipts could be returned before newer receipts.",
                          "regression": "Supply seven receipts and expect the newest five in reverse order."})
        cpu = Mock(return_value=(raw, 131, 54))
        with patch.object(local_source_model, "installed", return_value=True), patch.object(local_source_model,"run_cpu",cpu):
            self.assertIn("unverified hypothesis", self.tick())
            self.assertIsNone(self.tick(1005))
            self.assertIsNone(self.tick(1610))
            cpu.assert_called_once()
            first, = self.receipts()
            self.assertEqual((first["incident"], first["source"]["line"], first["assertion"]["line"]), (ident, 2, 6))
            self.assertNotIn("never-record-this-text", str(cpu.call_args) + str(first))
            self.source.write_text("def recent(receipts):\n    return receipts[-5:]\n")
            self.tick(2220)
            self.assertEqual(len(self.receipts()), 2)
            self.assertEqual(cpu.call_count, 2)

    def test_unrelated_mechanisms_and_incomplete_advice_are_withheld(self):
        self.report()
        data = investigate.evidence(self.root, self_diagnosis.handoff(self.state)[0])
        example = {"source": 1, "assertion": 1, "hypothesis": "A race condition might reorder receipts.",
                   "regression": "Add seven receipts and expect the newest result."}
        self.assertEqual(investigate.diagnosis(json.dumps(example),data), {})
        example["hypothesis"] = "Older receipts might be selected."
        example["regression"] = "Check whether the receipts"
        self.assertEqual(investigate.diagnosis(json.dumps(example),data), {})

    def test_foreground_environment_and_insufficient_reproductions_avoid_model(self):
        with closing(self_diagnosis.connect(self.state)) as db, db:
            self_diagnosis.observe(db, "Voice", "DNS unavailable", 1)
        with patch.object(local_source_model, "run_cpu") as run:
            with foreground(self.state):
                self.assertIsNone(self.tick())
            os.utime(self.state / "conversation.activity", (0,0))
            self.assertIsNone(self.tick())
            self_diagnosis.report(self.state, "Room report", "playback private never-record-this-text")
            self_diagnosis.tick(self.root, self.state, run_probe=lambda *_: "fixture passed; symptom unverified")
            self.assertIn("abstained",self.tick(1700))
            self.assertIsNone(self.tick(2400))
            self.assertNotIn("model",self.receipts()[0])
            run.assert_not_called()

    def test_evidence_changed_during_inference_and_invalid_citations(self):
        self.report()
        def complete(*args, **kwargs):
            self.source.write_text("def recent(receipts):\n    return list(reversed(receipts))[:5]\n")
            return "quiet", 100, 1
        with patch.object(local_source_model,"installed",return_value=True), patch.object(local_source_model,"run_cpu",side_effect=complete):
            self.assertIsNone(self.tick())
            self.assertEqual(self.receipts(), [])
        with patch.object(local_source_model,"installed",return_value=True), patch.object(local_source_model,"run_cpu",return_value=('{"source":999}',53,5)):
            self.assertIn("abstained",self.tick(1700))
            self.assertIsNone(self.tick(2400))

    @unittest.skipUnless(os.environ.get("TALOS_TEST_LOCAL_SOURCE_MODEL") == "1", "optional installed CPU model")
    def test_real_model_after_two_independent_offline_fixture_failures(self):
        runtime = local_source_model.installed(investigate.ROOT, investigate.ROOT / "state")
        if not runtime:
            self.skipTest("pinned local backend unavailable")
        self.report()
        # Only the verified engine and weights come from the install; the incident,
        # source, fixture, journals and resulting audit live entirely in this sandbox.
        with patch.object(local_source_model, "installed", return_value=runtime):
            result = self.tick()
        receipt, = self.receipts()
        self.assertIn(receipt["status"], result)
        self.assertEqual(receipt["model"], local_source_model.MODEL)
        self.assertGreater(receipt["input_tokens"], 0)
        self.assertGreater(receipt["output_tokens"], 0)
        if receipt["status"] == "unverified hypothesis":
            self.assertEqual(receipt["source"]["file"], "talos_core/runtime/voice_recovery.py")
            self.assertIn("regression", receipt)
        print("Isolated CPU incident audit:", json.dumps(receipt, sort_keys=True), flush=True)

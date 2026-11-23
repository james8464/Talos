import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.mac import mac_context
from talos_core.memory import memory_connections, memory_review
from talos_core.memory.personal_index import sync
from talos_core.self_improvement import graph_review
from talos_core.self_improvement.maintenance_schedule import MaintenanceSchedule


class MemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = self.state / "Library"
        self.update([
            ("Projects", [("Atlas:" + "a" * 40, "Atlas initial commit")]),
            ("Notes", [("old", "2026-05-01 Atlas launch planned for June")]),
            ("Calendar", [("event", "2026-05-10 Atlas launch rescheduled from June to July")]),
        ])

    def update(self, rows):
        sync(self.state, self.library, knowledge=rows)
        memory_connections.refresh(self.state)

    @staticmethod
    def result(claim, evidence=(1, 2)):
        return json.dumps({"findings": [
            {"kind": "changed plan", "claim": claim, "evidence": list(evidence)}
        ]})

    def recall(self, voice=False):
        return mac_context.room_answer("Show memory review for Atlas", self.state, voice)["reply"]

    def test_review_reuses_unchanged_evidence_and_revises_results(self):
        infer = Mock(return_value=self.result("Atlas moved its launch to July."))
        memory_review.review(self.state, "fixture", infer=infer)
        self.assertIn("Atlas moved its launch to July.", self.recall())
        self.assertIn("Notes [old]", self.recall())
        self.assertIn("Calendar [event]", self.recall())
        self.assertIn("private message field", self.recall(voice=True))
        memory_review.review(self.state, "fixture", infer=infer)
        infer.assert_called_once()

        self.update([("Calendar", [("event", "2026-05-11 Atlas launch rescheduled from June to August")])])
        self.assertNotIn("July", self.recall())
        infer.return_value = self.result("Atlas moved its launch to August.")
        memory_review.review(self.state, "fixture", infer=infer)
        self.assertIn("August", self.recall())
        self.update([("Calendar", [])])
        self.assertNotIn("August", self.recall())

    def test_cloud_focus_triggers_one_new_local_evidence_review(self):
        infer = Mock(return_value=self.result("Atlas moved its launch to July."))
        memory_review.review(self.state, "fixture", infer=infer)
        graph_review.review(self.state, lambda *_: '{"focus":"changed plan"}', lambda _: None)
        memory_review.review(self.state, "fixture", infer=infer)
        messages = infer.call_args.args[1]
        self.assertEqual(json.loads(messages[1]["content"])["investigation_focus"], "changed plan")
        memory_review.review(self.state, "fixture", infer=infer)
        self.assertEqual(infer.call_count, 2)

    def test_invalid_citations_leave_no_hypotheses(self):
        infer = Mock(return_value=self.result("A fabricated result", (1, 90)))
        self.assertIn("failed evidence validation", memory_review.review(self.state, "fixture", infer=infer))
        self.assertNotIn("fabricated", self.recall())

    def test_dismiss_and_restore_are_private_and_persist_across_reviews(self):
        claim = "Atlas moved its launch to July."
        infer = Mock(return_value=self.result(claim))
        memory_review.review(self.state, "fixture", infer=infer)
        identifier = memory_review.finding_id({"kind": "changed plan", "claim": claim})
        dismiss = f"Dismiss memory finding {identifier} for Atlas"
        self.assertIn("private message field", mac_context.room_answer(dismiss, self.state, True)["reply"])
        self.assertIn(claim, self.recall())
        self.assertIn("Dismissed", mac_context.room_answer(dismiss, self.state, False)["reply"])
        memory_review.review(self.state, "different-model", infer=infer)
        self.assertNotIn(claim, self.recall())
        restore = f"Restore memory finding {identifier} for Atlas"
        self.assertIn("Restored", mac_context.room_answer(restore, self.state, False)["reply"])
        self.assertIn(claim, self.recall())

    def test_evidence_changed_while_model_was_running(self):
        def change_source(*args):
            self.update([("Calendar", [("event", "Atlas cancelled the launch")])])
            return self.result("Atlas moved its launch to July.")

        self.assertIn("Evidence changed", memory_review.review(self.state, "fixture", infer=change_source))
        self.assertNotIn("July", self.recall())

    def test_review_schedule_defers_when_busy_and_retries_interrupted_work(self):
        scheduler = MaintenanceSchedule()
        job = Mock(side_effect=[None, "review complete"])
        with patch("talos_core.self_improvement.maintenance_schedule.conversation_busy", side_effect=[True, False, False, False]):
            for now in (0, 1, 2, 3):
                with patch("talos_core.self_improvement.maintenance_schedule.time.monotonic", return_value=now):
                    scheduler.poll_memory(self.state, run=job)
        self.assertEqual([call.args for call in job.call_args_list], [("memory_review",)] * 2)
        self.assertEqual(scheduler.memory_outcome, "review complete")

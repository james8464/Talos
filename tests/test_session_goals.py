import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import session_goals
from agent import interactive_chat
from agent_memory import open_memory


def verdict(result="done", reason="The draft includes the requested items.", evidence="blue"):
    return json.dumps({"verdict": result, "reason": reason, "evidence": evidence})


class StandingGoalTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "test.sqlite"

    def console(self, commands, generate):
        out, err = io.StringIO(), io.StringIO()
        with closing(open_memory(self.path)) as db, patch("agent.check_ollama_model"), patch("agent.model_text", generate):
            interactive_chat(db, io.StringIO(commands), out, err, apply=False, model="fixture")
        return out.getvalue(), err.getvalue()

    def test_real_console_drafts_reviews_revises_and_reopens_completed_result(self):
        writer = Mock(side_effect=["red", verdict("continue", "Include the color blue.", ""),
                                   "red and blue", verdict()])
        out, err = self.console("/new project\n/goal draft List both colors\n/subgoal Include blue\n/goal run\n", writer)
        self.assertEqual(err, "")
        self.assertIn("Done goal after 2 steps", out)
        self.assertIn("red and blue", out)
        self.assertIn("Include the color blue.", writer.call_args_list[2].args[0][-1]["content"])
        self.assertIn("Include blue", writer.call_args_list[3].args[0][-1]["content"])
        replay = Mock()
        out, err = self.console("/goal\n/resume project\n/goal show\n/goal run\n/goal clear\n/goal\n", replay)
        self.assertIn("red and blue", out)
        self.assertEqual(out.count("No standing goal"), 3)
        replay.assert_not_called()

    def test_missing_and_invented_evidence_pause_and_keep_draft(self):
        for response in ("invalid", verdict(evidence="invented"), verdict("blocked", "Needs a file.", "")):
            with self.subTest(response=response):
                out, _ = self.console("/goal clear\n/goal write a paragraph\n", Mock(side_effect=["blue", response]))
                self.assertIn("Paused goal after 1 step", out)
                self.assertIn("\nblue", out)
                out, err = self.console("/goal resume\n", Mock(side_effect=["blue", verdict()]))
                self.assertEqual(err, "")
                self.assertIn("Done goal after 2 steps", out)

    def test_concurrent_run_and_pause_prevent_a_late_completion(self):
        attempts = []
        def generate(messages, **kwargs):
            if "schema" not in kwargs:
                out, _ = self.console("/goal run\n", Mock(side_effect=AssertionError))
                attempts.append(out)
                return "blue"
            self.console("/goal pause\n", Mock(side_effect=AssertionError))
            return verdict()
        out, _ = self.console("/goal Write colors\n", generate)
        self.assertIn("Another run", attempts[0])
        self.assertIn("Paused goal after 0 steps", out)
        self.assertNotIn("\nblue", out)

    def test_an_expired_claim_is_recoverable_and_budget_stops_the_loop(self):
        self.console("/goal draft Write colors\n", Mock())
        with closing(open_memory(self.path)) as db, db:
            db.execute("UPDATE console_goals SET status='active',lease_until=1 WHERE session='text'")
        llm = Mock(side_effect=["blue", verdict("continue", "Needs more detail.", "")] * session_goals.MAX_STEPS)
        out, _ = self.console("/goal resume\n", llm)
        self.assertIn(f"Paused goal after {session_goals.MAX_STEPS} steps", out)
        self.assertEqual(llm.call_count, 2 * session_goals.MAX_STEPS)

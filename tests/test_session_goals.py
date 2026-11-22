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

    def test_background_queue_defers_without_model_and_recovers_after_activity(self):
        self.console("/goal queue Write a color card\n", Mock(side_effect=AssertionError))
        writer = Mock(side_effect=["blue first draft", "blue final", verdict()])
        ready = Mock()
        with closing(open_memory(self.path)) as db:
            self.assertFalse(session_goals.work_once(db, "fixture", writer, ready, lambda: False))
            ready.assert_not_called()
            self.assertTrue(session_goals.work_once(db, "fixture", writer, ready, Mock(side_effect=[True, True, False])))
            row = session_goals.current(db, "text")
            self.assertEqual((row["status"], row["draft"], row["lease_until"]), ("queued", "blue first draft", 0))
        with closing(open_memory(self.path)) as db:
            self.assertTrue(session_goals.work_once(db, "fixture", writer, ready, lambda: True))
            self.assertIn("blue first draft", writer.call_args_list[1].args[0][-1]["content"])
            self.assertFalse(session_goals.work_once(db, "fixture", writer, ready, lambda: True))
        out, _ = self.console("/goal show\n", Mock())
        self.assertIn("Done goal after 1 step", out)

    def test_worker_command_processes_console_queue_through_loopback_api(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import os
        import subprocess
        import sys
        import threading
        calls = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, result):
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                calls.append(self.path)
                self.reply({"models": [{"name": "fixture"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(self.path)
                answer = verdict() if body.get("format") else "The card is blue."
                self.reply({"done": True, "done_reason": "stop", "message": {"content": answer}})

        root = Path(__file__).resolve().parents[1]
        cli = [sys.executable, str(root / "agent.py"), "--db", str(self.path), "--model", "fixture", "chat"]
        subprocess.run(cli, input="/goal queue Draw up a color card\n", text=True, check=True, capture_output=True)
        with ThreadingHTTPServer(("127.0.0.1", 0), Model) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                worker = [sys.executable, "-c",
                          "import agent,idle_lab; idle_lab.power_and_idle=lambda: True; raise SystemExit(agent.main())",
                          "--db", str(self.path), "--model", "fixture", "goal-worker"]
                subprocess.run(worker, cwd=root, check=True, capture_output=True, timeout=10,
                               env={**os.environ, "TALOS_OLLAMA_PORT": str(server.server_port)})
            finally:
                server.shutdown()
                thread.join()
        shown = subprocess.run(cli, input="/goal show\n", text=True, check=True, capture_output=True).stdout
        self.assertEqual(calls, ["/api/tags", "/api/chat", "/api/chat"])
        self.assertIn("Done goal after 1 step", shown)
        self.assertIn("The card is blue.", shown)

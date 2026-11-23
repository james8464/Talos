from contextlib import closing
import fcntl
import io
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import talos_activity as activity
import background_work
from agent import interactive_chat
from agent_memory import open_memory
from room_service import Handler, Room


class RuntimeActivityTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.out = io.StringIO()

    def test_viewer_does_not_start_work_and_reports_real_locks(self):
        with patch("talos_activity.probe", return_value="stopped (connection refused)"):
            activity.display(self.state, once=True, output=self.out)
            self.assertEqual(list(self.state.iterdir()), [])
            self.assertIn("Maintenance · stopped", self.out.getvalue())
            with (self.state / "maintenance.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with activity.room_presence(self.state, 8877), activity.operation(
                    self.state, "Maintenance", "Refresh enabled memory sources"
                ):
                    report = "\n".join(activity.snapshot(self.state, None))
                    self.assertIn("Room on port 8877 · process running", report)
                    self.assertIn("Maintenance · running", report)
                    self.assertIn("Running now · Refresh enabled memory sources", report)
            self.assertNotIn("Running now", "\n".join(activity.snapshot(self.state, None)))

    def test_console_context_matches_real_payload_and_records_provider_usage(self):
        response = {"done": True, "message": {"content": "Private reply."},
                    "prompt_eval_count": 90, "eval_count": 8, "eval_duration": 500_000_000}
        with closing(open_memory(self.state / "test.sqlite")) as db, patch("agent.check_ollama_model"), \
             patch("agent.request_ollama_json", return_value=response) as api, \
             patch("talos_activity.probe", return_value="unknown (local connection blocked)"):
            inputs = io.StringIO("/context\nPrivate question\n/context Other question\n/activity\n/tools\n/quit\n")
            errors = io.StringIO()
            interactive_chat(db, inputs, self.out, errors, apply=False, model="fixture")
            self.assertEqual(errors.getvalue(), "")
            text = self.out.getvalue()
            self.assertIn("No measured model turn", text)
            self.assertIn("90/8,192 input tokens", text)
            self.assertIn("16.0 output tokens/s", text)
            self.assertIn("recent dialogue 30", text)
            self.assertIn("current request 14", text)
            self.assertIn("instructions " + format(len(api.call_args.kwargs["payload"]["messages"][0]["content"]), ","), text)
            self.assertIn("Planner lookups", text)
        journal = "\n".join(row[3] for row in activity.events(self.state))
        self.assertIn("Local response received", journal)
        self.assertNotIn("Private question", journal)
        self.assertNotIn("Private reply", journal)

    def test_health_uses_supervisor_and_activity_failure_is_visible(self):
        room = Room(self.state / "room.sqlite", "fixture", model_state=lambda: "warming up")
        with room.turn_lock:
            status, health = room.route("GET", "/health", {})
        self.assertEqual(status, 200)
        self.assertTrue(health["turn_active"])
        self.assertEqual(health["model_state"], "warming up")
        with self.assertRaises(ValueError), activity.operation(self.state, "Room", "Handle typed turn"):
            raise ValueError("private text")
        self.assertIn("ended (ValueError)", activity.events(self.state)[-1][3])
        self.assertNotIn("private text", activity.events(self.state)[-1][3])

    def test_viewer_reads_the_room_http_handler_over_an_in_process_connection(self):
        room = Room(self.state / "room.sqlite", "fixture", model_state=lambda: "warming up")
        client, server = socket.socketpair()
        with client, server, room.turn_lock:
            client.settimeout(2)
            handler = threading.Thread(target=Handler, args=(server, ("127.0.0.1", 0), SimpleNamespace(room=room)))
            handler.start()
            try:
                with patch("http.client.HTTPConnection.connect", lambda connection: setattr(connection, "sock", client)):
                    self.assertEqual(activity.probe(8765), "running · model warming up · handling a turn")
            finally:
                handler.join(timeout=2)
            self.assertFalse(handler.is_alive())

    def test_usage_persists_across_sessions_and_marks_missing_provider_counts(self):
        payload = {"done": True, "message": {"content": "reply"}}
        responses = [{**payload, "prompt_eval_count": 7, "eval_count": 2}, payload,
                     {**payload, "prompt_eval_count": 11, "eval_count": 3}]
        path = self.state / "usage.sqlite"
        with closing(open_memory(path)) as db, patch("agent.check_ollama_model"), \
             patch("agent.request_ollama_json", side_effect=responses):
            interactive_chat(db, io.StringIO("hello\nagain\n/new sample\nhello\n/usage\n"), self.out,
                             io.StringIO(), apply=False, model="example")
        self.assertIn("1/1 replies measured · 11 input and 3 output tokens", self.out.getvalue())
        with closing(open_memory(path)) as db:
            interactive_chat(db, io.StringIO("/usage all\n"), self.out, io.StringIO(), apply=False, model="example")
        self.assertIn("2/3 replies measured · 18 input and 5 output tokens", self.out.getvalue())

    def test_console_and_worker_share_work_order_state(self):
        path = self.state / "agent.sqlite3"
        receipts = []
        def console(db, commands, apply):
            out, errors = io.StringIO(), io.StringIO()
            interactive_chat(db, io.StringIO(commands), out, errors, apply=apply, model="fixture")
            self.assertEqual(errors.getvalue(), "")
            return out.getvalue()

        with closing(open_memory(path)) as db:
            self.assertIn("disabled in shadow mode", console(db, "/bg inspect the archived minutes\n", False))
            self.assertIn("#1 queued", console(db, "/bg inspect the archived minutes\n/jobs\n", True))
        with closing(open_memory(path)) as worker:
            self.assertTrue(background_work.run_one(worker, None, "fixture", ready=False, notify=receipts.append))
            self.assertIn("waiting for model readiness", receipts[-1])
            self.assertNotIn("archived minutes", " ".join(receipts))
            self.assertIn("#1 cancelled", console(worker, "/jobs cancel 1\n/jobs\n", True))

    def test_terminal_control_characters_cannot_escape_an_event(self):
        activity.record(self.state, "Room", "log\x1b[31m\nline\u202e")
        self.assertFalse(any(ch in activity.events(self.state)[-1][3] for ch in ("\x1b", "\n", "\u202e")))

    def test_console_access_modes_and_command_pages(self):
        for enabled, mode in ((True, "local actions enabled"), (False, "shadow tasks")):
            with self.subTest(mode=mode), closing(open_memory(self.state / "console.sqlite")) as db:
                out, errors = io.StringIO(), io.StringIO()
                interactive_chat(db, io.StringIO("/whoami\n/commands 2\n/tools list\n"), out, errors,
                                 apply=enabled, model="fixture")
                self.assertFalse(errors.getvalue())
                self.assertIn(f"user ID {os.geteuid()} · session text · {mode}", out.getvalue())
                self.assertIn("page 2/", out.getvalue())
                self.assertIn("search_memory", out.getvalue())

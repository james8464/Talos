import io
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import self_diagnosis as diag
from agent import interactive_chat
from agent_memory import open_memory
from room_service import Room
from talos_activity import record


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = Path(__file__).resolve().parents[1]

    def test_scheduled_fixture_rotates_across_restarts_and_flags_failures(self):
        now = 1000
        probe = Mock(side_effect=["fixture passed; symptom unverified", "fixture assertion failed",
                                  "fixture assertion failed", "fixture assertion failed"])

        def check():
            return diag.tick(self.root, self.state, run_probe=probe, proactive=True, clock=lambda: now)

        with patch.object(diag, "conversation_busy", side_effect=[True, False, False, False, False, False]):
            self.assertIsNone(check())
            self.assertEqual(check(), "playback · fixture passed")
            self.assertIsNone(check())
            now += diag.HEALTH_INTERVAL
            self.assertEqual(check(), "voice · fixture assertion failed")
            self.assertIn("fixture assertion failed", check())
            self.assertIn("repeatable fixture failure", check())
        self.assertEqual([call.args[1] for call in probe.call_args_list], ["playback", "voice", "voice", "voice"])
        self.assertEqual(diag.handoff(self.state)[0]["area"], "voice")

    def test_restart_groups_actual_provider_failures_and_observes_transport_recovery(self):
        for when in ("12:00", "12:05"):
            record(self.state, "Respan", "Connection unavailable (gaierror); next eligible " + when)
        record(self.state, "Maintenance", "Finished with no errors")
        probe = Mock(side_effect=AssertionError("environmental blockers need no fixture"))
        diag.tick(self.root, self.state, run_probe=probe)
        diag.tick(self.root, self.state, run_probe=probe)
        (row,) = diag.handoff(self.state)
        self.assertEqual(
            (row["hits"], row["kind"], row["status"]), (2, "environment", "blocked by environment")
        )
        record(self.state, "Respan", "Request to provider")
        diag.tick(self.root, self.state, run_probe=probe)
        self.assertEqual(len(diag.handoff(self.state)), 1)
        record(self.state, "Respan", "Response from fixture provider in 1s")
        diag.tick(self.root, self.state, run_probe=probe)
        self.assertEqual(diag.handoff(self.state), [])
        self.assertIn("transport recovered", " ".join(diag.summary(self.state)))
        record(self.state, "Respan", "Connection unavailable (gaierror); retry in 60s")
        diag.tick(self.root, self.state, run_probe=probe)
        self.assertEqual(diag.handoff(self.state)[0]["hits"], 3)
        probe.assert_not_called()

    def test_real_worker_lock_recovery_is_scoped_to_one_instance(self):
        for lock in ("vault-watch", "maintenance"):
            record(self.state, "Supervisor", f"{lock} exited (19) · will retry in 2s")
        record(self.state, "Supervisor", "maintenance exited (20) · will retry in 4s")
        record(self.state, "Supervisor", "maintenance restarted · awaiting worker lock")
        diag.tick(self.root, self.state, run_probe=lambda *_: "fixture passed; symptom unverified")
        rows = diag.handoff(self.state)
        self.assertEqual(
            {r["component"]: r["hits"] for r in rows},
            {"Supervisor/vault-watch": 1, "Supervisor/maintenance": 2},
        )
        record(self.state, "Supervisor", "maintenance recovered · process stayed up and worker lock is held")
        diag.tick(self.root, self.state, run_probe=lambda *_: "fixture passed; symptom unverified")
        self.assertEqual([r["component"] for r in diag.handoff(self.state)], ["Supervisor/vault-watch"])
        self.assertIn("Supervisor/maintenance · worker recovered", " ".join(diag.summary(self.state)))

    def test_room_and_console_reports_share_scope_without_private_text_in_handoff(self):
        room = Room(self.state / "room.db", "fixture", online=False)
        description = "My search lost a result about private-fixture-omega"
        status, msg = room.route("POST", "/v1/turn", {"text": "Report a problem: " + description})
        self.assertEqual(status, 200)
        self.assertIn("Recorded problem", msg["reply"])
        output, error = io.StringIO(), io.StringIO()
        with closing(open_memory(self.state / "console.db")) as db:
            interactive_chat(
                db,
                io.StringIO("/repair " + description + "\n/repairs\n/repairs 1\n/quit\n"),
                output,
                error,
                apply=True,
                model="offline",
            )
        self.assertFalse(error.getvalue())
        self.assertIn(description, output.getvalue())  # private console inspection only
        (row,) = diag.handoff(self.state)
        self.assertEqual((row["hits"], row["area"]), (2, "memory"))
        self.assertNotIn("omega", str(row))
        self.assertNotIn(
            "omega", room.route("POST", "/v1/voice/facing", {"text": "What needs repair?"})[1]["reply"]
        )
        self.assertIn("symptom unverified", diag.tick(self.root, self.state))
        self.assertEqual(diag.handoff(self.state)[0]["status"], "fixture passed; symptom unverified")

    def test_unknown_area_and_preview_do_not_guess_or_execute_reported_instructions(self):
        room = Room(self.state / "room.db", "fixture", apply=False, online=False)
        result = room.route("POST", "/v1/turn", {"text": "Report a problem: my drawings look grey"})
        self.assertIn("preview", result[1]["reply"])
        self.assertFalse((self.state / diag.DB).exists())
        diag.report(self.state, "Console report", "my drawings look grey")
        probe = Mock(side_effect=AssertionError("cannot select an unknown regression"))
        diag.tick(self.root, self.state, run_probe=probe)
        self.assertEqual(diag.handoff(self.state)[0]["status"], "needs a reproduction")
        probe.assert_not_called()

    def test_two_independent_fixture_assertions_required_for_repeatability(self):
        diag.report(self.state, "Console report", "routine scheduling is ignoring its due time")
        run = Mock(return_value="fixture assertion failed")
        self.assertIn("fixture assertion failed", diag.tick(self.root, self.state, run_probe=run))
        self.assertIn("repeatable fixture failure", diag.tick(self.root, self.state, run_probe=run))
        diag.tick(self.root, self.state, run_probe=run)
        self.assertEqual(run.call_count, 2)
        (row,) = diag.handoff(self.state)
        self.assertEqual((row["area"], row["fixture"]), ("scheduling", diag.CASES["scheduling"]))

    def test_bounded_cursor_and_overflow_keep_existing_environment_incident(self):
        record(self.state, "Respan", "Network unavailable: gaierror")
        for i in range(75):
            record(self.state, "New subsystem", f"index {i} completed")
        result = diag.tick(self.root, self.state)
        self.assertEqual(result, "Scanned 64 operational events")
        self.assertEqual(diag.tick(self.root, self.state), "Scanned 12 operational events")
        with patch.object(diag, "MAX_SIGNATURES", 2):
            for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
                diag.report(self.state, "User", "colour mismatch for " + name)
        rows = diag.handoff(self.state, limit=20)
        self.assertEqual(len(rows), 3)
        self.assertTrue(any(r["component"] == "Additional reports" and r["hits"] == 4 for r in rows))
        self.assertTrue(any(r["status"] == "blocked by environment" for r in rows))

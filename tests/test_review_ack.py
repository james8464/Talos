import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from talos_core.review_ack import review_content
from talos_core.runtime import talos_activity
from talos_core.self_improvement import respan_worker
from talos_core.self_improvement.upstream_review import inventory


class AcknowledgementTests(unittest.TestCase):
    def test_quiet_markup_repetition_and_short_acks(self):
        for raw in (
            " **HEARTBEAT_OK** ",
            "<b>HEARTBEAT_OK</b>",
            "`HEARTBEAT_OK`",
            "HEARTBEAT_OK HEARTBEAT_OK!!!",
            "all clear HEARTBEAT_OK.",
            "NO_reply",
            "**no_reply**",
            "<code>HEARTBEAT_OK</code>&nbsp;all good",
            "HEARTBEAT_OK " + "🟢" * 150,
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(review_content(raw))
        self.assertEqual(review_content("HEARTBEAT_OK " + "🟢" * 151), "🟢" * 151)

    def test_substantive_content_and_malformed_output_survive_for_validation(self):
        for raw in (
            "NO_REPLY: actual reminder",
            "HEARTBEAT_BROKEN",
            "NO_REP",
            "body HEARTBEAT_OK inside",
            "",
            'HEARTBEAT_OK {"incomplete":',
            "HEARTBEAT_OK {",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(review_content(raw), raw)
        data = json.dumps(
            {"upstream_quote": "<i>HEARTBEAT_OK</i>", "talos_quote": "**NO_REPLY**"}
        )
        for raw in (
            data,
            "HEARTBEAT_OK " + data,
            data + " <b>HEARTBEAT_OK</b>",
            "**NO_REPLY**\n" + data,
        ):
            with self.subTest(raw=raw):
                self.assertEqual(review_content(raw), data)
        self.assertEqual(
            review_content("unexpected prose " + data), "unexpected prose " + data
        )

    def test_abstention_progress_notifications_and_invalid_proposal_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            for folder in ("openclaw-src/src/auto-reply", "hermes-src/cron"):
                (state / folder).mkdir(parents=True)
            (state / "openclaw-src/src/auto-reply/heartbeat.ts").write_text(
                "function quiet_replay() {}"
            )
            (state / "hermes-src/cron/missed_runs.py").write_text(
                "def future_only(): pass"
            )
            for file in ("talos_core/self_improvement/respan_worker.py", "talos_core/room/room_checkins.py"):
                (root / file).parent.mkdir(parents=True, exist_ok=True)
                (root / file).write_text("def existing_behavior(): pass")
            inventory(root, state)

            def run(name, raw, now=100):
                return respan_worker.turn(
                    root,
                    state,
                    job=name,
                    clock=lambda: now,
                    call=lambda *a, **kw: (raw, {}),
                )

            self.assertIn("Compared OpenClaw", run("OpenClaw", "<em>HEARTBEAT_OK</em>"))
            self.assertIn("deferred", run("Hermes", 'HEARTBEAT_OK {"broken":'))
            with closing(sqlite3.connect(state / "upstream-inventory.sqlite3")) as db:
                self.assertEqual(
                    db.execute("SELECT tree FROM comparison_progress").fetchall(),
                    [("OpenClaw",)],
                )
            proposal = json.dumps(
                dict(
                    upstream_quote="future_only",
                    talos_quote="existing_behavior",
                    feature="Review missed occurrences",
                    acceptance="Replay a suspended job",
                )
            )
            self.assertIn(
                "Compared Hermes", run("Hermes", "HEARTBEAT_OK " + proposal, now=161)
            )
            rows = [
                json.loads(line)
                for line in (state / "feature-requests.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [r["feature"] for r in rows], ["Review missed occurrences"]
            )
            events = " ".join(r[3] for r in talos_activity.events(state, 0))
            self.assertIn("no supported improvement", events)
            self.assertIn("queued proposal", events)

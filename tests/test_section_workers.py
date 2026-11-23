import fcntl
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.runtime import talos_activity
from talos_core.self_improvement import section_workers as workers
from talos_core.self_improvement.upstream_review import inventory
from tools import talos_service


class SectionReviews(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / "state"
        for path in [
            "state/openclaw-src/src/auto-reply/heartbeat.ts",
            "state/hermes-src/cron/missed_runs.py",
            "talos_core/self_improvement/respan_worker.py",
            "talos_core/room/room_checkins.py",
        ]:
            src = self.root / path
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text("reference_behavior_here" if path.startswith("state/") else "talos_behavior_here")
        (self.state / "private_notes").write_text("personal fixture")
        inventory(self.root, self.state)

    def test_two_engines_actually_overlap_and_resume_with_evidence_receipts(self):
        both = threading.Barrier(2)

        def answer(engine, excerpt):
            data = json.loads(excerpt)
            self.assertEqual(set(data), {"upstream_path", "upstream", "talos_path", "talos"})
            self.assertNotIn("personal fixture", excerpt)
            both.wait(timeout=5)
            return json.dumps(
                {
                    "feature": "Provide a quiet review",
                    "acceptance": "Replay an idle state",
                    "upstream_quote": "reference_behavior_here",
                    "talos_quote": "talos_behavior_here",
                }
            )

        def one(engine):
            return workers.turn(self.root, self.state, engine, review=answer, clock=lambda: 100)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(one, workers.ENGINES))
        self.assertEqual(sum(x.startswith("Compared ") for x in results), 2)
        queue = [
            json.loads(line) for line in (self.state / "feature-requests.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            {x["origin"] for x in queue},
            {"OpenCode-section-review", "OpenClaude-section-review"},
        )
        self.assertEqual({x["status"] for x in queue}, {"proposed"})
        for engine in workers.ENGINES:
            self.assertIsNone(one(engine))
            result = workers.turn(
                self.root,
                self.state,
                engine,
                review=Mock(side_effect=AssertionError),
                clock=lambda: 701,
            )
            self.assertIn("No pending", result)
        self.assertEqual(len((self.state / "feature-requests.jsonl").read_text().splitlines()), 2)

    def test_failed_validation_and_timeout_preserve_cursor_and_backoff(self):
        for now, reply in [
            (100, Mock(side_effect=TimeoutError("private error detail"))),
            (160, Mock(return_value='{"x":1}')),
        ]:
            result = workers.turn(self.root, self.state, "OpenCode", review=reply, clock=lambda now=now: now)
            self.assertIn("deferred", result)
            self.assertNotIn("private error", result)
        with closing(sqlite3.connect(self.state / workers.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT failures,due FROM workers").fetchone(), (2, 280))
        with closing(sqlite3.connect(self.state / "upstream-inventory.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM comparison_progress").fetchone()[0], 0)

    def test_room_owner_takes_priority_without_duplicate_reviews(self):
        priority = self.state / "section-OpenCode-room-owner.lock"
        with priority.open("a") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            with patch.object(workers, "turn") as turn:
                workers.watch(self.root, self.state, "OpenCode", True)
                workers.watch(self.root, self.state, "OpenCode", True, room_owner=True)
                turn.assert_not_called()
        with patch.object(workers, "turn", return_value=None) as turn:
            workers.watch(self.root, self.state, "OpenCode", True, room_owner=True)
            turn.assert_called_once_with(self.root, self.state, "OpenCode")

    def test_opencode_real_process_honors_isolation_and_only_reads_visible_reply(self):
        cli = self.root / "opencode"
        cli.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
assert '--standalone' in sys.argv and '--thinking' not in sys.argv
assert pathlib.Path.home().resolve() == pathlib.Path.cwd().resolve()
assert json.loads(pathlib.Path('opencode.json').read_text())['permission']=={'*':'deny'}
assert 'PRIVATE_FIXTURE' not in os.environ
print(json.dumps({'type':'reasoning','part':{'text':'hidden'}}))
print(json.dumps({'type':'text','part':{'text':'{"feature":"quiet"}'}}))
""")
        cli.chmod(0o700)
        with (
            patch.dict(os.environ, {"PRIVATE_FIXTURE": "private"}),
            patch("talos_core.self_improvement.section_workers.shutil.which", return_value=str(cli)),
        ):
            self.assertEqual(workers.opencode_review("only source"), {"feature": "quiet"})
        with patch(
            "talos_core.self_improvement.section_workers.free_review", return_value={"feature": "quiet"}
        ) as routed:
            self.assertEqual(
                json.loads(workers.cli_review("OpenClaude", "source excerpt")),
                {"feature": "quiet"},
            )
            self.assertTrue(routed.call_args.args[0].endswith("source excerpt"))
        self.assertNotIn(
            "--section-reviews",
            talos_service.configuration(respan=True)["ProgramArguments"],
        )
        self.assertIn(
            "--section-reviews",
            talos_service.configuration(sections=True)["ProgramArguments"],
        )
        workers.turn(
            self.root,
            self.state,
            "OpenCode",
            review=Mock(side_effect=TimeoutError),
            clock=lambda: 100,
        )
        self.assertIn(
            "OpenCode sections · stopped",
            "\n".join(talos_activity.snapshot(self.state, 12345)),
        )

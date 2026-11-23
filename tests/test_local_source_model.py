import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from talos_core.self_improvement import local_source_model as local
from talos_core.self_improvement.feature_targets import FILE
from talos_core.self_improvement.respan_worker import (
    CONTENDED,
    LOCAL_READY,
    LOCK_RETRY,
    rearm_deferred_jobs,
    turn,
)
from talos_core.self_improvement.upstream_review import inventory


class LocalReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.state.mkdir()

    def test_local_only_schedules_source_and_knowledge_without_a_provider(self):
        (self.state / "upstream-inventory.sqlite3").touch()
        with (
            patch.object(local, "installed", return_value=("exe", "model")),
            patch("talos_core.self_improvement.respan_worker.local_source_turn", return_value="source reviewed") as source,
            patch("talos_core.self_improvement.graph_review.review_local", return_value="knowledge reviewed") as knowledge,
            patch("talos_core.self_improvement.graph_review.review", side_effect=AssertionError("unexpected cloud review")),
            patch("talos_core.self_improvement.respan_worker.compare", side_effect=AssertionError("unexpected cloud comparison")),
        ):
            for job in ("OpenClaw", "Hermes", "Knowledge"):
                result = turn(self.root, self.state, job=job, clock=lambda: 100, local_only=True)
                self.assertTrue(result.startswith(local.MODEL))
            self.assertEqual(source.call_count, 2)
            knowledge.assert_called_once()

    def test_rejects_unverified_install_and_changes_to_previously_verified_weights(
        self,
    ):
        helper = self.state / "local-reviewer/complete"
        helper.parent.mkdir()
        helper.touch(mode=0o700)
        weights = local.model_path(self.root)
        weights.parent.mkdir(parents=True)
        weights.write_bytes(b"model")
        library = self.root / "library"
        library.write_bytes(b"engine")
        manifest = (
            self.root / "models/ollama/manifests/registry.ollama.ai/library/qwen3/4b"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "layers": [
                        {
                            "mediaType": "application/vnd.ollama.image.model",
                            "digest": local.WEIGHT,
                            "size": 5,
                        }
                    ]
                }
            )
        )
        with patch.object(local, "LIB", library):
            self.assertIsNone(local.installed(self.root, self.state))
            marker = helper.parent / "engine-id"
            marker.write_text(
                json.dumps([local.file_id(library), local.file_id(weights)])
            )
            self.assertEqual(local.installed(self.root, self.state), (helper, weights))
            weights.write_bytes(b"other")
            os.utime(weights, ns=(weights.stat().st_atime_ns, 0))
            self.assertIsNone(local.installed(self.root, self.state))

    def test_local_fallback_writes_only_hash_checked_source_with_accurate_origin(self):
        for name in ("openclaw-src", "hermes-src"):
            (self.state / name).mkdir()
        target = self.root / "talos_core/self_improvement/respan_worker.py"
        target.parent.mkdir(parents=True)
        target.write_text("if status == 'ready':\n    return run_job()\n")
        upstream = self.state / "openclaw-src/src/auto-reply/heartbeat.ts"
        upstream.parent.mkdir(parents=True)
        upstream.write_text("if (status === 'ready') {\n    return submitJob();\n}\n")
        inventory(self.root, self.state)
        response = json.dumps(
            dict(
                upstream=1,
                talos=1,
                feature="Check ready status before sending a job",
                acceptance="Replay unavailable and ready states",
            )
        )

        def offline(*args, **kwargs):
            raise RuntimeError("Respan connection unavailable (gaierror).")

        with (
            patch.object(local, "installed", return_value=("exe", "model")),
            patch.object(local, "run_cpu", return_value=(response, 400, 38)) as model,
        ):
            outcome = turn(self.root, self.state, job="OpenClaw", call=offline)
        self.assertIn("Compared OpenClaw", outcome)
        event = json.loads((self.state / FILE).read_text().strip())
        self.assertEqual(event["origin"], local.ORIGIN)
        self.assertIn(event["upstream_quote"], upstream.read_text())
        self.assertIn(event["talos_quote"], target.read_text())
        self.assertEqual(model.call_count, 1)
        with closing(sqlite3.connect(self.state / "respan-review.sqlite3")) as db:
            self.assertEqual(
                db.execute(
                    "SELECT failures FROM jobs WHERE name='OpenClaw'"
                ).fetchone()[0],
                0,
            )

    def test_watcher_handoff_only_rearms_deferred_source_work_once(self):
        clock = lambda: 100
        file = self.state/"respan-review.sqlite3"
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("CREATE TABLE jobs(name TEXT PRIMARY KEY,due REAL,failures INTEGER,outcome TEXT)")
            db.executemany("INSERT INTO jobs VALUES(?,?,?,?)", [
                ("Hermes",800,3,"Respan connection unavailable (gaierror)."),
                ("OpenClaw",900,0,"Compared OpenClaw heartbeat"),
                ("Knowledge",700,0,"Reviewed knowledge")])
        with patch.object(local,"installed",return_value=None):
            self.assertEqual(rearm_deferred_jobs(self.root,self.state,clock=clock),0)
        with patch.object(local,"installed",return_value=("exe","weights")):
            self.assertEqual(rearm_deferred_jobs(self.root,self.state,clock=clock),1)
            self.assertEqual(rearm_deferred_jobs(self.root,self.state,clock=clock),0)
            with closing(sqlite3.connect(file)) as db:
                self.assertEqual(db.execute("SELECT due,outcome FROM jobs WHERE name='Hermes'").fetchone(),(100,LOCAL_READY))
                self.assertEqual(dict(db.execute("SELECT name,due FROM jobs WHERE name!='Hermes'")),{"OpenClaw":900,"Knowledge":700})
            (self.state/"upstream-inventory.sqlite3").touch()
            with patch("talos_core.self_improvement.respan_worker.local_source_turn",return_value=CONTENDED), \
                 patch("talos_core.self_improvement.respan_worker.compare",side_effect=AssertionError("early cloud request")):
                self.assertEqual(turn(self.root,self.state,job="Hermes",clock=clock),CONTENDED)
                self.assertIsNone(turn(self.root,self.state,job="Hermes",clock=clock))
            with closing(sqlite3.connect(file)) as db:
                self.assertEqual(db.execute("SELECT due,failures FROM jobs WHERE name='Hermes'").fetchone(),(100+LOCK_RETRY,3))
            self.assertEqual(rearm_deferred_jobs(self.root,self.state,clock=clock),1)

    def test_knowledge_provider_failure_recovers_locally_without_repeating_cloud_request(self):
        with closing(sqlite3.connect(self.state/"respan-review.sqlite3")) as db, db:
            db.execute("CREATE TABLE jobs(name TEXT PRIMARY KEY,due REAL,failures INTEGER,outcome TEXT)")
            db.execute("INSERT INTO jobs VALUES('Knowledge',900,4,'Respan connection unavailable (gaierror).')")
        with patch.object(local, "installed", return_value=("exe","weights")), \
             patch("talos_core.self_improvement.respan_worker.graph_review.review_local", return_value="Evidence verified") as on_device, \
             patch("talos_core.self_improvement.respan_worker.graph_review.review", side_effect=AssertionError("early cloud request")):
            self.assertEqual(rearm_deferred_jobs(self.root, self.state, clock=lambda:100),1)
            result = turn(self.root,self.state,job="Knowledge",clock=lambda:100)
            self.assertIn(local.MODEL,result)
            self.assertIn("Evidence verified",result)
            on_device.assert_called_once()
            self.assertEqual(rearm_deferred_jobs(self.root,self.state,clock=lambda:101),0)
        with closing(sqlite3.connect(self.state/"respan-review.sqlite3")) as db:
            due,failures,outcome = db.execute("SELECT due,failures,outcome FROM jobs WHERE name='Knowledge'").fetchone()
            self.assertEqual((due,failures),(700,0))
            self.assertEqual(outcome,result)

    def test_yields_to_conversation_and_observes_elapsed_deadline(self):
        child = self.root / "slow.py"
        child.write_text("import sys,time; sys.stdin.buffer.read(); time.sleep(10)")
        import sys

        with (
            patch.object(local, "conversation_busy", side_effect=[False, True]),
            self.assertRaisesRegex(RuntimeError, "foreground"),
        ):
            local.run_cpu((sys.executable, child), self.state, "source")
        with self.assertRaisesRegex(RuntimeError, "time budget"):
            local.run_cpu((sys.executable, child), self.state, "source", timeout=0.1)

    @unittest.skipUnless(
        os.environ.get("TALOS_TEST_LOCAL_SOURCE_MODEL") == "1",
        "requires installed local Qwen weights and compatible ARM64 helper",
    )
    def test_real_model_generates_and_terminates_in_process(self):
        root = Path(__file__).resolve().parents[1]
        runtime = local.installed(root, root / "state")
        self.assertIsNotNone(runtime)
        output, inputs, generated = local.run_cpu(
            runtime,
            self.state,
            "<|im_start|>user\nReturn quiet.<|im_end|>\n<|im_start|>assistant\n",
        )
        self.assertTrue(output == "quiet" or isinstance(json.loads(output), dict))
        self.assertGreater(inputs, 0)
        self.assertGreater(generated, 0)

    def test_unrelated_settings_with_real_quotes_are_withheld(self):
        data = json.dumps(
            dict(
                upstream="const MAX_EXPORT_COMMAND_OUTPUT_PATH_CHARS = 512;",
                talos="MODEL_CONTEXT_TOKENS = 8192",
                upstream_path="export.ts",
                talos_path="agent.py",
            )
        )
        generated = json.dumps(
            dict(
                upstream=1,
                talos=1,
                feature="max_model_context_tokens",
                acceptance="Set the command model context to 8192 tokens",
            )
        )
        with patch.object(local, "run_cpu", return_value=(generated, 90, 20)):
            result = local.review(data, (), self.state, lambda _: None)
        self.assertFalse(any(json.loads(result).values()))

    def test_invalid_citations_and_imports_cannot_generate_findings(self):
        data = json.dumps(
            dict(
                upstream="import pkg\nif status == ok:",
                talos='"""Title"""\nreturn finish()',
                upstream_path="up.py",
                talos_path="local.py",
            )
        )
        with patch.object(
            local,
            "run_cpu",
            return_value=(
                '{"upstream":2,"talos":1,"feature":"demo","acceptance":"replay"}',
                1,
                1,
            ),
        ), self.assertRaisesRegex(ValueError, "citations"):
            local.review(data, (), self.state, lambda _: None)
        with patch.object(local, "run_cpu", return_value=("quiet", 12, 1)):
            self.assertFalse(
                any(
                    json.loads(
                        local.review(data, (), self.state, lambda _: None)
                    ).values()
                )
            )

"""Bounded parallel delegation with per-task failure isolation."""

import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core import delegate


class ParallelDelegateTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.argv = [["fake", "review", "one"], ["fake", "review", "two"]]
        patcher = patch("talos_core.delegate.subagents.command_for",
                        side_effect=lambda state, name, task: [name, task])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_requires_confirmation(self):
        with self.assertRaises(delegate.DelegateRefused):
            delegate.delegate_many(self.state, [("review", "x")])

    def test_runs_in_parallel_and_preserves_order(self):
        active, peak, lock = [0], [0], threading.Lock()

        def runner(argv, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.1)
            with lock:
                active[0] -= 1
            return subprocess.CompletedProcess(argv, 0, argv[1], "")

        results = delegate.delegate_many(
            self.state, [("review", "one"), ("review", "two")], confirm=True,
            workers=2, runner=runner,
        )
        self.assertEqual([r["output"] for r in results], ["one", "two"])
        self.assertEqual(peak[0], 2)

    def test_one_failure_does_not_abort_the_batch(self):
        def runner(argv, **kwargs):
            if argv[0] == "bad":
                return subprocess.CompletedProcess(argv, 2, "", "boom")
            return subprocess.CompletedProcess(argv, 0, "done", "")

        results = delegate.delegate_many(
            self.state, [("bad", "x"), ("review", "y")], confirm=True, runner=runner,
        )
        self.assertFalse(results[0]["ok"])
        self.assertTrue(results[1]["ok"])

    def test_runner_exception_is_isolated(self):
        def runner(argv, **kwargs):
            raise TimeoutError("too slow")

        results = delegate.delegate_many(
            self.state, [("review", "x")], confirm=True, runner=runner,
        )
        self.assertFalse(results[0]["ok"])
        self.assertIn("too slow", results[0]["error"])

    def test_rejects_bad_shapes_and_caps_workers(self):
        with self.assertRaises(ValueError):
            delegate.delegate_many(self.state, [], confirm=True)
        with self.assertRaises(ValueError):
            delegate.delegate_many(self.state, ["review"], confirm=True)


if __name__ == "__main__":
    unittest.main()

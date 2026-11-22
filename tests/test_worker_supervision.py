import fcntl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_activity import events
from worker_supervision import WorkerSupervisor


class WorkerSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.script = self.state / 'maintenance.py'
        self.script.write_text("""import fcntl, pathlib, sys, time
root = pathlib.Path(__file__).parent
if not (root/'enabled').exists():
    sys.exit(19)
with (root/'maintenance.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    (root/'ready').touch()
    time.sleep(30)
""")
        self.children = [subprocess.Popen([sys.executable, str(self.script)])]
        self.addCleanup(self.stop_children)
        self.assertEqual(self.children[0].wait(timeout=5), 19)
        self.supervisor = WorkerSupervisor(self.state, self.children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_real_exited_worker_recovers_after_lock_is_held(self):
        self.supervisor.tick(0)
        self.assertEqual(self.children[0].returncode, 19)
        (self.state/'enabled').touch()
        self.supervisor.tick(1)
        self.supervisor.tick(2)
        limit = time.monotonic() + 5
        while not (self.state/'ready').exists() and time.monotonic() < limit:
            time.sleep(.02)
        self.assertTrue((self.state/'ready').exists())
        self.supervisor.tick(7)
        log = [e[3] for e in events(self.state)]
        self.assertTrue(any('exited (19)' in item for item in log))
        self.assertTrue(any('recovered' in item for item in log))
        self.assertIsNone(self.children[0].poll())

    def test_another_live_owner_and_shutdown_do_not_spawn(self):
        with (self.state/'maintenance.lock').open('a') as owner, patch("worker_supervision.subprocess.Popen") as spawn:
            fcntl.flock(owner, fcntl.LOCK_EX)
            self.supervisor.tick(0)
            self.supervisor.tick(10)
            stopped = threading.Event()
            stopped.set()
            self.supervisor.watch(stopped)
            spawn.assert_not_called()
        self.assertFalse(events(self.state))

    def test_repeated_launch_failure_increases_the_delay(self):
        self.supervisor.tick(0)
        with patch("worker_supervision.subprocess.Popen", side_effect=OSError()) as spawn:
            for now in range(14):
                self.supervisor.tick(now)
        self.assertEqual(spawn.call_count, 2)
        self.assertFalse(any('recovered' in event[3] for event in events(self.state)))

import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from contextlib import closing
from talos_core.cognition.conversation_activity import conversation_busy, foreground, QuietWorker
from talos_core.room.room_service import Room


class ConversationActivityTests(unittest.TestCase):
    def test_room_holds_foreground_lock_for_entire_synthesis_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            room = Room(state / "room.sqlite", "fixture", online=False)
            def check_lock(*args):
                self.assertTrue(conversation_busy(state, grace=0))
                raise ValueError("fixture failed")
            with patch.object(room, "_route", side_effect=check_lock):
                with self.assertRaises(ValueError):
                    room.route("POST", "/v1/voice", {"text": "synthetic"})
            self.assertFalse(conversation_busy(state, grace=0))
            self.assertTrue(conversation_busy(state))
            self.assertEqual((state / "conversation.activity").read_bytes(), b"")

    def test_a_crashed_foreground_process_releases_the_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            code = ("from talos_core.cognition.conversation_activity import foreground\n"
                    "from pathlib import Path\nimport sys\n"
                    "with foreground(Path(sys.argv[1])):\n"
                    " print('held', flush=True)\n sys.stdin.read()")
            with subprocess.Popen([sys.executable, "-c", code, str(state)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True,
                                  cwd=Path(__file__).resolve().parents[1]) as child:
                try:
                    self.assertEqual(child.stdout.readline().strip(), "held")
                    self.assertTrue(conversation_busy(state, grace=0))
                finally:
                    child.kill()
                    child.wait()
            self.assertFalse(conversation_busy(state, grace=0))

    def test_worker_cancels_an_independent_process_and_retries_after_quiet(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=1) as executor:
            state = Path(folder)
            marker = state / "started"
            code = ("import sys,time,pathlib,json,os\nfor line in sys.stdin:\n"
                    " if json.loads(line)['kind'] == 'long':\n"
                    "  pathlib.Path(sys.argv[1]).touch()\n  time.sleep(3)\n"
                    " print(json.dumps({'result':os.getpid()}),flush=True)")
            with closing(QuietWorker(state, [sys.executable, "-c", code, str(marker)])) as worker:
                result = executor.submit(worker.request, "long")
                limit = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < limit:
                    time.sleep(.01)
                self.assertTrue(marker.exists())
                with foreground(state), foreground(state):
                    self.assertIsNone(result.result(timeout=2))
                    self.assertIsNone(worker.request("short"))
                self.assertTrue(conversation_busy(state))
                os.utime(state / "conversation.activity", (0, 0))
                first = worker.request("short")
                self.assertEqual(worker.request("short"), first)

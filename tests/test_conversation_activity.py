import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from conversation_activity import conversation_busy, foreground, run_while_quiet
from room_service import Room


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

    def test_worker_cancels_an_independent_process_and_retries_after_quiet(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=1) as executor:
            state = Path(folder)
            marker = state / "started"
            code = "import sys,time,pathlib; pathlib.Path(sys.argv[1]).touch(); time.sleep(3); print('finished')"
            result = executor.submit(run_while_quiet, state, [sys.executable, "-c", code, str(marker)])
            limit = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < limit:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            with foreground(state), foreground(state):
                self.assertIsNone(result.result(timeout=2))
                self.assertIsNone(run_while_quiet(state, ["unreachable"]))
            self.assertTrue(conversation_busy(state))
            os.utime(state / "conversation.activity", (0, 0))
            self.assertEqual(run_while_quiet(state, [sys.executable, "-c", "print(17)"]), "17\n")

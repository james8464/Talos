"""Consent-free AVFoundation readiness without capture."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import av_readiness

GOOD = {
    "camera_permission": "denied",
    "microphone_permission": "not_determined",
    "camera_device": True,
    "microphone_device": True,
    "cameras": 1,
    "microphones": 1,
    "capture_started": False,
    "recording_started": False,
}


class AVReadinessTests(unittest.TestCase):
    def state(self):
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_with(self, payload):
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with patch.object(av_readiness, "available", return_value=True), \
                patch.object(av_readiness, "_binary", return_value=Path("/bin/true")), \
                patch.object(av_readiness.subprocess, "run", return_value=completed):
            return av_readiness.readiness(self.state())

    def test_reports_permissions_and_devices_without_capture(self):
        result = self.run_with(GOOD)
        self.assertEqual(result["camera_permission"], "denied")
        self.assertEqual(result["microphone_permission"], "not_determined")
        self.assertFalse(result["capture_started"])
        self.assertFalse(result["recording_started"])
        self.assertFalse(result["ready"])
        self.assertIn("no capture started", av_readiness.describe(result))

    def test_authorized_devices_are_ready(self):
        result = self.run_with({**GOOD, "camera_permission": "authorized",
                                "microphone_permission": "authorized"})
        self.assertTrue(result["ready"])

    def test_capture_started_is_rejected(self):
        with self.assertRaises(av_readiness.AVReadinessError):
            self.run_with({**GOOD, "capture_started": True})
        with self.assertRaises(av_readiness.AVReadinessError):
            self.run_with({**GOOD, "microphone_permission": "bogus"})

    def test_invalid_json_is_rejected(self):
        completed = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with patch.object(av_readiness, "available", return_value=True), \
                patch.object(av_readiness, "_binary", return_value=Path("/bin/true")), \
                patch.object(av_readiness.subprocess, "run", return_value=completed):
            with self.assertRaises(av_readiness.AVReadinessError):
                av_readiness.readiness(self.state())


class AVReadinessCliTests(unittest.TestCase):
    def test_cli_prints_json(self):
        from talos_core import cli

        output = io.StringIO()
        with patch("talos_core.mac.av_readiness.readiness", return_value=GOOD), \
                redirect_stdout(output):
            self.assertEqual(cli.main(["av-readiness", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["camera_device"], True)


if __name__ == "__main__":
    unittest.main()

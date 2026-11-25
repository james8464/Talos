"""Safe process inventory."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from talos_core.runtime import processes


class ProcessInventoryTests(unittest.TestCase):
    def test_classify_labels_and_never_includes_args(self):
        self.assertEqual(processes.classify("python run_talos.py --headless --mute")["label"],
                         "room service")
        self.assertTrue(processes.classify("python tools/talos_live_features --json")["stale"])
        self.assertTrue(processes.classify("afplay file.wav")["audio"])
        self.assertIsNone(processes.classify("Safari"))
        self.assertIsNone(processes.classify("/bin/zsh -c 'python run_talos.py --headless'"))
        self.assertIsNone(processes.classify("python -m talos_core.cli processes"))

    def test_inventory_groups_processes_and_ports(self):
        ps = SimpleNamespace(returncode=0, stdout=(
            " 1 0 python run_talos.py --headless --mute\n"
            " 2 1 python -m talos_core.self_improvement.source_observer --watch\n"
            " 3 1 python tools/talos_live_features\n"
            " 4 1 afplay clip.wav\n"
        ), stderr="")
        lsof = SimpleNamespace(returncode=0, stdout=(
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "python 1 james 5u IPv4 0 0t0 TCP 127.0.0.1:8765 (LISTEN)\n"
        ), stderr="")

        def runner(command, **kwargs):
            return ps if command[0] == "ps" else lsof

        data = processes.inventory(runner=runner)
        self.assertEqual(len(data["processes"]), 4)
        self.assertEqual(data["stale"], [3])
        self.assertEqual(data["audio"], [4])
        self.assertEqual(data["ports"], [{"port": 8765, "pid": 1}])
        self.assertIn("room service", processes.summary(data))
        self.assertIn("stale: 1", processes.summary(data))

    def test_stop_stale_only_touches_test_leftovers(self):
        data = {"processes": [
            {"pid": 10, "label": "room service"},
            {"pid": 11, "label": "stale GUI"},
            {"pid": 12, "label": "live harness"},
            {"pid": 13, "label": "audio"},
        ]}
        with patch("talos_core.runtime.processes.os.kill") as kill:
            stopped = processes.stop_stale(data)
        self.assertEqual(stopped, [11, 12])
        self.assertEqual([call.args[0] for call in kill.call_args_list], [11, 12])

    def test_stop_stale_can_target_only_headless_browsers(self):
        data = {"processes": [
            {"pid": 20, "label": "headless browser"},
            {"pid": 21, "label": "live harness"},
            {"pid": 22, "label": "room service"},
        ]}
        with patch("talos_core.runtime.processes.os.kill") as kill:
            stopped = processes.stop_stale(data, labels={"headless browser"})
        self.assertEqual(stopped, [20])
        self.assertEqual([call.args[0] for call in kill.call_args_list], [20])


if __name__ == "__main__":
    unittest.main()

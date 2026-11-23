import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from run_talos import ProjectModel


class ProjectModelTests(unittest.TestCase):
    def test_worker_restarts_its_own_child_and_reaps_it_on_shutdown(self):
        with tempfile.TemporaryDirectory() as root:
            program = Path(root) / "fixture"
            program.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            program.chmod(0o700)
            model = ProjectModel("fixture", 11435, True)
            warmed = threading.Event()
            with (
                patch("run_talos.shutil.which", return_value=str(program)),
                patch("run_talos.check_bind"), patch("run_talos.check_ollama_model"),
                patch("run_talos.request_ollama_json"),
                patch("run_talos.warm_model", side_effect=lambda _: warmed.set())
            ):
                model.worker.start()
                try:
                    self.assertTrue(warmed.wait(5))
                    with model.lock:
                        first, = model.children
                    warmed.clear()
                    first.terminate()
                    self.assertTrue(warmed.wait(7))
                    with model.lock:
                        replacement, = model.children
                    self.assertNotEqual(first.pid, replacement.pid)
                finally:
                    model.close()
                self.assertFalse(model.worker.is_alive())
                self.assertIsNotNone(replacement.poll())
                self.assertFalse(model.children)

    def test_supervised_voice_uses_startup_verification_and_rechecks_after_failure(self):
        from unittest.mock import Mock

        from talos_core.room.room_service import Room
        model = ProjectModel("fixture", 11435, True)
        server = model.ready_server = Mock()
        server.poll.return_value = None
        model.voice_verified = True
        with tempfile.TemporaryDirectory() as root:
            room = Room(Path(root) / "room.db", "fixture", model_state=lambda: "ready", voice_generate=model.generate_voice)
            with patch("talos_core.room.room_service.check_ollama_model", side_effect=AssertionError), \
                    patch("run_talos.check_ollama_model") as probe, \
                    patch("run_talos.model_text", return_value='{"talos_says":"A compass would help."}') as generate:
                def speak():
                    return room.route("POST", "/v1/voice/facing", {"text": "What could I take on a long walk?"})
                self.assertEqual(speak()[1]["reply"], "A compass would help.")
                self.assertEqual(speak()[0], 200)
                probe.assert_not_called()
                generate.side_effect = RuntimeError("Model request failed.")
                with self.assertRaises(RuntimeError):
                    speak()
                self.assertFalse(model.voice_verified)
                generate.side_effect = None
                probe.side_effect = RuntimeError("Local model unavailable.")
                with self.assertRaises(RuntimeError):
                    speak()
                self.assertEqual(generate.call_count, 3)
                probe.side_effect = None
                self.assertEqual(speak()[0], 200)
                self.assertEqual(probe.call_count, 2)
                self.assertTrue(model.voice_verified)
                server.poll.return_value = 1
                with self.assertRaises(RuntimeError):
                    speak()
                self.assertEqual(generate.call_count, 4)

    def test_failure_from_retired_server_does_not_invalidate_replacement(self):
        from unittest.mock import Mock
        model = ProjectModel("fixture", 11435, True)
        model.ready_server = Mock()
        model.ready_server.poll.return_value = None
        model.voice_verified = True
        def replacement(*args, **kwargs):
            model.ready_server = Mock()
            model.voice_verified = True
            raise RuntimeError("Old server stopped.")
        with patch("run_talos.model_text", side_effect=replacement), self.assertRaises(RuntimeError):
            model.generate_voice([], model="fixture")
        self.assertTrue(model.voice_verified)

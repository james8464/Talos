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

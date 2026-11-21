import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_runtime


class LocalRuntimeTests(unittest.TestCase):
    def test_requires_all_local_model_files(self):
        with tempfile.TemporaryDirectory() as directory:
            models_dir = Path(directory)
            whisper_dir = models_dir / local_runtime.WHISPER_MODEL
            whisper_dir.mkdir()
            for asset in (
                whisper_dir / "config.json",
                whisper_dir / "weights.npz",
                models_dir / local_runtime.KOKORO_MODEL,
            ):
                asset.write_bytes(b"fixture")
            voices = models_dir / local_runtime.KOKORO_VOICES
            voices.touch()

            with self.assertRaisesRegex(RuntimeError, local_runtime.KOKORO_VOICES):
                local_runtime.check_model_files(models_dir)
            voices.write_bytes(b"fixture")
            local_runtime.check_model_files(models_dir)

    def test_checks_model_via_loopback(self):
        with patch("local_runtime.http.client.HTTPConnection") as factory:
            connection = factory.return_value
            response = io.BytesIO(json.dumps({
                "models": [{"name": local_runtime.OLLAMA_MODEL}]
            }).encode())
            response.status = 200
            connection.getresponse.return_value = response

            local_runtime.check_ollama_model()
            factory.assert_called_once_with("127.0.0.1", 11434, timeout=3)
            connection.request.assert_called_once_with("GET", "/api/tags")
            connection.close.assert_called_once_with()

    def test_rejects_remote_model_even_with_matching_name(self):
        with patch("local_runtime.http.client.HTTPConnection") as factory:
            response = io.BytesIO(json.dumps({
                "models": [{"name": local_runtime.OLLAMA_MODEL, "remote_model": "remote"}]
            }).encode())
            response.status = 200
            factory.return_value.getresponse.return_value = response
            with self.assertRaisesRegex(RuntimeError, "Missing local model"):
                local_runtime.check_ollama_model()

    def test_reports_unavailable_ollama_without_fallback(self):
        with patch("local_runtime.http.client.HTTPConnection") as factory:
            connection = factory.return_value
            connection.request.side_effect = ConnectionRefusedError()
            with self.assertRaisesRegex(RuntimeError, "Start Ollama locally"):
                local_runtime.check_ollama_model()
            connection.close.assert_called_once_with()

    def test_diagnostics_report_multiple_failures_and_keep_checking(self):
        with (
            patch("local_runtime.check_platform", side_effect=RuntimeError("wrong platform")),
            patch("local_runtime.check_dependencies", return_value="installed"),
            patch("local_runtime.check_model_files", side_effect=RuntimeError("missing files")),
            patch("local_runtime.check_ollama_model") as ollama,
        ):
            ollama.return_value = None
            self.assertEqual(local_runtime.runtime_checks(), [
                ("Platform", "wrong platform", False),
                ("Dependencies", "installed", True),
                ("Speech models", "missing files", False),
                ("Local Ollama", "Ready", True),
            ])

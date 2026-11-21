import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agent import OLLAMA_MODEL, chat, interactive_chat, show_status
from agent_memory import open_memory
from talos_identity import capability_context


class TalosConsoleTests(unittest.TestCase):
    def test_capability_facts_match_the_active_interface(self):
        text = capability_context("text")
        voice = capability_context("voice", remembers_voice=True)
        self.assertIn("no access to the microphone", text)
        self.assertIn("active browser microphone", voice)
        for facts in (text, voice):
            self.assertIn("no camera", facts)
        self.assertIn("saved for later recall", voice)
        self.assertIn("temporary", capability_context("voice"))

    def test_text_chat_gives_the_model_current_capability_facts(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(open_memory(Path(directory) / "trial.sqlite3")) as database,
            patch("agent.check_ollama_model"),
            patch("agent.model_text", return_value="A mock reply.") as model,
        ):
            chat(database, "Can you see me?")
        prompt = model.call_args.args[0][0]["content"]
        self.assertIn("no camera", prompt)
        self.assertIn("sardonic wit", prompt)
        self.assertIn("cannot execute such actions", prompt)

    def test_console_keeps_running_after_model_failure_and_reports_real_receipt(self):
        messages = io.StringIO("Can you hear me?\n/task Set a timer\n/inbox\n/quit\n")
        output, errors = io.StringIO(), io.StringIO()
        receipt = {"id": 7, "action": "applied", "tool": "set_reminder"}
        with (
            patch("agent.chat", side_effect=RuntimeError("Model unavailable")),
            patch("agent.owner_task", return_value=receipt) as task,
            patch("agent.inbox_items", return_value=[{"id": 7, "kind": "reminder"}]),
        ):
            self.assertEqual(
                interactive_chat(None, messages, output, errors, apply=True, model=OLLAMA_MODEL),
                0,
            )
        task.assert_called_once_with(None, "Set a timer", apply=True, model=OLLAMA_MODEL)
        self.assertIn("Model unavailable", errors.getvalue())
        self.assertEqual(json.loads(output.getvalue().splitlines()[1]), receipt)

    def test_status_does_not_require_inference(self):
        output = io.StringIO()
        with (
            patch("agent.runtime_checks", return_value=[
                ("Platform", "Ready", True), ("Local Ollama", "Loopback blocked", False),
            ]),
            patch("agent.model_text") as model,
        ):
            show_status(OLLAMA_MODEL, output)
        model.assert_not_called()
        self.assertIn("Camera and room sensors: presence detection", output.getvalue())
        self.assertIn("Loopback blocked", output.getvalue())

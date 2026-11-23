import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agent import OLLAMA_MODEL, chat, interactive_chat, show_status
from talos_core.cognition.talos_identity import capability_context
from talos_core.memory.agent_memory import open_memory


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

    def test_history_and_recall_read_local_turns_without_inference(self):
        from talos_core.memory.transcript_memory import remember

        commands = io.StringIO("/history\n/recall\n/recall violet\n/recall missing\n/quit\n")
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, closing(open_memory(Path(directory) / "trial.db")) as db:
            remember(db, "text", [{"role": "user", "content": "violet folder"}])
            remember(db, "visit/fixture", [{"role": "user", "content": "violet visitor secret"}])
            with patch("agent.model_text") as model:
                interactive_chat(db, commands, output, errors, apply=False, model=OLLAMA_MODEL)
            model.assert_not_called()
        self.assertEqual(output.getvalue().count("user> violet folder"), 2)
        self.assertNotIn("visitor secret", output.getvalue())
        self.assertIn("No matching archived turns.", output.getvalue())
        self.assertIn("Usage: /recall QUERY", errors.getvalue())

    def test_named_sessions_survive_restart_and_keep_prompts_separate(self):
        with tempfile.TemporaryDirectory() as directory, patch("agent.check_ollama_model"), patch(
            "agent.model_text", return_value="Acknowledged."
        ) as model:
            path = Path(directory) / "console.db"
            def run(commands):
                output, errors = io.StringIO(), io.StringIO()
                with closing(open_memory(path)) as db:
                    interactive_chat(db, io.StringIO(commands), output, errors, apply=False, model=OLLAMA_MODEL)
                return output.getvalue(), errors.getvalue()
            run("violet in the default thread\n/new project\nviolet in the project thread\n")
            project_prompt = model.call_args.args[0]
            self.assertEqual(len(project_prompt), 2)
            self.assertNotIn("default thread", project_prompt[0]["content"])
            output, errors = run("/resume project\n/new project\n/resume visit/fixture\n/sessions\n/history\n"
                                 "Which violet?\n/new other\nWhich violet?\n/resume text\n/history\n")
            self.assertIn("* project", output)
            self.assertIn("violet in the default thread", output)
            self.assertIn("violet in the project thread", output)
            self.assertIn("already exists", errors)
            self.assertIn("session name", errors)
            self.assertEqual(model.call_args_list[-2].args[0][1]["content"], "violet in the project thread")
            self.assertEqual(len(model.call_args.args[0]), 2)
            self.assertNotIn("violet", model.call_args.args[0][0]["content"])

    def test_export_publishes_complete_private_session_and_preserves_existing_file(self):
        from talos_core.memory.transcript_memory import remember
        with tempfile.TemporaryDirectory() as folder, closing(open_memory(Path(folder) / "trial.db")) as db:
            path = Path(folder) / "my session.jsonl"
            words = ["δ" + str(i) for i in range(10)]
            remember(db, "text", [{"role": "user", "content": word} for word in words])
            remember(db, "visit/example", [{"role": "user", "content": "private visitor"}])
            commands = io.StringIO(f"/save {path}\n/new empty\n/save {path}\n")
            out, err = io.StringIO(), io.StringIO()
            interactive_chat(db, commands, out, err, apply=False, model=OLLAMA_MODEL)
            self.assertEqual([json.loads(line)["text"] for line in path.read_text().splitlines()], words)
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            self.assertIn("Exported 10", out.getvalue())
            self.assertIn("File exists", err.getvalue())

    def test_branch_snapshot_nested_resume_and_explicit_recall(self):
        from talos_core.memory.transcript_memory import remember
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "test.db"
            def run(commands):
                out, err = io.StringIO(), io.StringIO()
                with closing(open_memory(db_path)) as db:
                    interactive_chat(db, io.StringIO(commands), out, err, apply=False, model=OLLAMA_MODEL)
                return out.getvalue(), err.getvalue()
            with closing(open_memory(db_path)) as db:
                remember(db, "text", [{"role": "user", "content": f"parent {i}"} for i in range(12)])
            run("/branch first\n")
            with closing(open_memory(db_path)) as db:
                remember(db, "text", [{"role": "user", "content": "later parent"}])
                remember(db, "console/first", [{"role": "user", "content": "cobalt alternative"}])
            out, err = run("/resume first\n/fork nested\n/branch first\n/history\n")
            self.assertIn("already exists", err)
            self.assertIn("parent 7", out)
            self.assertNotIn("parent 6", out)
            self.assertNotIn("later parent", out)
            with closing(open_memory(db_path)) as db:
                remember(db, "console/first", [{"role": "user", "content": "later branch"}])
                self.assertEqual(db.execute("SELECT count(*) FROM transcripts").fetchone()[0], 15)
                self.assertEqual(db.execute("SELECT count(*) FROM console_forks").fetchone()[0], 2)
            path = root / "export.jsonl"
            run(f"/resume nested\n/save {path}\n")
            self.assertEqual([json.loads(line)["text"] for line in path.read_text().splitlines()],
                             [*(f"parent {i}" for i in range(12)), "cobalt alternative"])
            with patch("agent.check_ollama_model"), patch("agent.model_text", return_value="Answer") as model:
                out, _ = run("Find cobalt\n/recall cobalt\n")
                self.assertNotIn("cobalt alternative", model.call_args.args[0][0]["content"])
                self.assertIn("cobalt alternative", out)
                run("/resume nested\nContinue\n")
                prompt = model.call_args.args[0]
                self.assertEqual(prompt[-2]["content"], "cobalt alternative")
                self.assertNotIn("later branch", str(prompt))

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

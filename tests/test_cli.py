import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from talos_core import cli
from talos_core.runtime.local_runtime import OLLAMA_MODEL


class CliTests(unittest.TestCase):
    def test_version_and_help_are_discoverable(self):
        with self.assertRaises(SystemExit) as exit_code, redirect_stdout(io.StringIO()):
            cli.main(["--version"])
        self.assertEqual(exit_code.exception.code, 0)
        text = cli.build_parser().format_help()
        for command in ("doctor", "decompose", "status", "test", "improve", "computer", "session"):
            self.assertIn(command, text)

    def test_doctor_reports_a_clean_tree(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "talos_core").mkdir()
        (root / "talos_core/__init__.py").write_text("")
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["doctor", "--root", str(root), "--quick", "--no-lint", "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"ok": true', output.getvalue())

    def test_decompose_writes_a_plan_and_renders_leaves(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main([
                "decompose", "Refactor the memory pipeline and migrate storage",
                "--state", str(state), "--file", "talos_core/memory/personal_index.py",
            ])
        self.assertEqual(code, 0)
        self.assertIn("leaves", output.getvalue())
        self.assertEqual(len(list((state / "plans").glob("*.json"))), 1)

    def test_console_renders_brain_without_speech(self):
        database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "console.sqlite"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["console", "--db", str(database), "--script", "/status", "/quit"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertGreaterEqual(len(text), 1000)
        self.assertIn("model: qwen2.5:1.5b", text)

    def test_session_help_documents_scripting_and_text_only_launch(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exit_code, redirect_stdout(output):
            cli.main(["session", "--help"])
        self.assertEqual(exit_code.exception.code, 0)
        text = output.getvalue()
        self.assertIn("--script", text)
        self.assertIn("--apply", text)
        self.assertIn("Text only", text)

    def test_session_prints_header_and_runs_scripted_commands(self):
        database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "session.sqlite"
        output = io.StringIO()
        with (
            patch("agent.local_model_names", return_value=[OLLAMA_MODEL]),
            redirect_stdout(output),
        ):
            code = cli.main(["session", "--db", str(database), "--script", "/status", "/quit"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("Talos 0.2.0 · text session", text)
        self.assertIn(f"Model    {OLLAMA_MODEL}\n", text)
        self.assertIn("Runtime  local Ollama ready · 1 local model installed", text)
        self.assertIn("Audio    off · text only (no microphone, speaker or audio playback)", text)
        self.assertIn("Mode     shadow", text)
        self.assertIn(f"Session text · shadow tasks · model {OLLAMA_MODEL}", text)

    def test_session_prints_a_text_reply_from_stdin(self):
        database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "session.sqlite"
        output = io.StringIO()
        with (
            patch("agent.local_model_names", return_value=[OLLAMA_MODEL]),
            patch("agent.chat", return_value="A scripted reply.") as chat,
            redirect_stdout(output),
        ):
            code = cli.main(
                ["session", "--db", str(database), "--apply", "--script", "Hello there", "/quit"]
            )
        self.assertEqual(code, 0)
        self.assertIn("Talos> A scripted reply.", output.getvalue())
        self.assertIn("Mode     actions enabled", output.getvalue())
        chat.assert_called_once_with(
            ANY, "Hello there", model=OLLAMA_MODEL, session="text"
        )

    def test_terminal_alias_reports_unavailable_runtime_and_keeps_running(self):
        database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "session.sqlite"
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("agent.local_model_names", side_effect=RuntimeError("Ollama is not running.")),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            code = cli.main(["terminal", "--db", str(database), "--script", "/quit"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn(f"Model    {OLLAMA_MODEL}\n", text)
        self.assertIn("Runtime  local Ollama unavailable · Ollama is not running.", text)
        self.assertNotIn("Traceback", errors.getvalue())

    def test_session_reports_a_database_that_cannot_open(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        blocker = folder / "not-a-directory"
        blocker.write_text("")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = cli.main(["session", "--db", str(blocker / "session.sqlite")])
        self.assertEqual(code, 1)
        self.assertIn("could not open a text session", errors.getvalue())

    def test_graph_query_reports_nodes_and_sources(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["graph", "decomposition", "manager", "--limit", "2"])
        self.assertEqual(code, 0)
        payload = output.getvalue()
        self.assertIn("code_graph", payload)
        self.assertIn("decomposition.py", payload)

    def test_status_surfaces_permissions_and_health(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["status", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("Talos", output.getvalue())
        self.assertIn("Mac access", output.getvalue())
        self.assertIn("Audio", output.getvalue())
        self.assertIn("Processes:", output.getvalue())

    def test_acp_command_option_does_not_overwrite_subcommand(self):
        args = cli.build_parser().parse_args(
            ["acp", "--command", "python fixture.py", "--prompt", "hi", "--confirm"]
        )
        self.assertEqual(args.command, "acp")
        self.assertEqual(args.acp_command, "python fixture.py")

    def test_ask_repl_reads_turns_from_the_terminal(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with patch("builtins.input", side_effect=["hello", "/quit"]), \
             patch("talos_core.interfaces.room_client.turn",
                   return_value={"reply": "hi there"}), \
             redirect_stdout(output):
            code = cli.main(["ask", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("Talos room", output.getvalue())
        self.assertIn("hi there", output.getvalue())

    def test_stream_command_prints_deltas(self):
        output = io.StringIO()
        with patch("talos_core.runtime.text_stream.stream_reply",
                   return_value=iter(["Hello", " world"])), redirect_stdout(output):
            code = cli.main(["stream", "hi"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "Hello world\n")

    def test_codejob_queues_reports_and_declines(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "example.py").write_text("VALUE = 1\n")
        state = root / "state"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["codejob", "start", "Add a constant.", "--file", "example.py",
                             "--root", str(root), "--state", str(state), "--verify-only"])
        self.assertEqual(code, 0)
        job = json.loads(output.getvalue())
        self.assertEqual(job["status"], "queued")
        output = io.StringIO()
        with patch("talos_core.self_improvement.initiative_coder.local_infer",
                   return_value=lambda prompt: "{}"), redirect_stdout(output):
            code = cli.main(["codejob", "run", "--id", job["id"],
                             "--root", str(root), "--state", str(state)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "declined")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["codejob", "status", "--state", str(state)]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["status"], "declined")

    def test_codejob_detach_forwards_state_root_and_model(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "example.py").write_text("VALUE = 1\n")
        state = root / "state"
        with patch("talos_core.cli.subprocess.Popen") as popen, \
                redirect_stdout(io.StringIO()):
            code = cli.main(["codejob", "start", "Add a constant.", "--file", "example.py",
                             "--root", str(root), "--state", str(state),
                             "--verify-only", "--detach", "--model", "test-model"])
        self.assertEqual(code, 0)
        argv = popen.call_args.args[0]
        self.assertIn(str(state), argv)
        self.assertIn(str(root), argv)
        self.assertIn("test-model", argv)
        self.assertIn("--id", argv)

    def test_highlight_coordinates_do_not_click(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fake = Mock()
        fake.highlight.return_value = True
        output = io.StringIO()
        with patch("talos_core.mac.cursor.CursorOverlay", return_value=fake), \
             redirect_stdout(output):
            code = cli.main(["highlight", "--x", "10", "--y", "20", "--hold", "0",
                             "--state", str(state)])
        self.assertEqual(code, 0)
        fake.highlight.assert_called_once()
        fake.close.assert_called_once()
        self.assertIn('"ok": true', output.getvalue())

    def test_design_command_creates_artifacts(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["design", "--kind", "poster", "--title", "Terminal Design",
                             "--state", str(state)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(Path(payload["path"]).is_file())
        document = io.StringIO()
        with redirect_stdout(document):
            code = cli.main(["design", "--kind", "document", "--title", "Terminal Doc",
                             "--body", "Body text", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertEqual(Path(json.loads(document.getvalue())["path"]).suffix, ".docx")

    def test_apps_reports_native_integrations(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["apps", "--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("App status", output.getvalue())
        self.assertIn("Calendar", output.getvalue())

    def test_cloud_command_uses_the_optional_bridge(self):
        output = io.StringIO()
        with patch("talos_core.cognition.cloud_model.complete", return_value="Cloud OK") as complete:
            with redirect_stdout(output):
                code = cli.main(["cloud", "hello", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("Cloud OK", output.getvalue())
        complete.assert_called_once()


if __name__ == "__main__":
    unittest.main()

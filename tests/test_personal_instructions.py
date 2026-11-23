import json
import tempfile
import unittest
from pathlib import Path

import agent
from talos_core.cognition import personal_instructions
from talos_core.memory.agent_memory import open_memory


class PersonalInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = self.state / "agent.sqlite3"

    def test_save_load_context_and_permissions(self):
        value = personal_instructions.save(self.state, "Use British spelling. Keep replies short.")
        self.assertIn("British", value)
        self.assertEqual(personal_instructions.load(self.state), value)
        self.assertIn("Operator personal instructions", personal_instructions.context(self.state))
        self.assertEqual((self.state / personal_instructions.FILE).stat().st_mode & 0o777, 0o600)

    def test_instructions_are_bounded_and_empty_is_rejected(self):
        long = "word " * 2000
        saved = personal_instructions.save(self.state, long)
        self.assertLessEqual(len(saved), personal_instructions.MAX_CHARS)
        with self.assertRaises(ValueError):
            personal_instructions.save(self.state, "   ")

    def test_console_prompt_includes_local_instructions(self):
        personal_instructions.save(self.state, "Prefer metric units.")
        with open_memory(self.db_path) as db:
            messages, _ = agent.console_prompt(db, "hello", "text")
        self.assertIn("Prefer metric units.", messages[0]["content"])

    def test_cli_reads_and_sets(self):
        import io
        from contextlib import redirect_stdout

        from talos_core import cli

        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["instructions", "--state", str(self.state),
                                        "--set", "Be", "brief"]), 0)
        self.assertEqual(json.loads(out.getvalue())["instructions"], "Be brief")
        with redirect_stdout(io.StringIO()) as out:
            cli.main(["instructions", "--state", str(self.state)])
        self.assertEqual(json.loads(out.getvalue())["instructions"], "Be brief")


if __name__ == "__main__":
    unittest.main()

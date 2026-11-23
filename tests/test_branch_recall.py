import io
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agent import interactive_chat
from agent_memory import open_memory
from transcript_memory import fork_session, remember, search_transcripts


class BranchRecallTests(unittest.TestCase):
    def test_old_inherited_evidence_is_available_after_restart_without_sibling_or_future_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.db"
            def say(db, session, *texts):
                remember(db, session, [{"role": "user", "content": text} for text in texts])
            with closing(open_memory(path)) as db:
                say(db, "text", "The notebook is at the station.")
                with db:
                    fork_session(db, "text", "console/first")
                    fork_session(db, "console/first", "console/empty")
                say(db, "text", "notebook in later parent")
                say(db, "console/first", "notebook in later intermediate")
                say(db, "console/other", *["notebook sibling"] * 30)
                say(db, "console/empty", *["other turn"] * 12)
                say(db, "visit/private", "notebook guest")
                with db:
                    db.execute("CREATE TABLE IF NOT EXISTS console_sessions(name TEXT PRIMARY KEY)")
                    db.execute("INSERT INTO console_sessions VALUES('empty')")
            with closing(open_memory(path)) as db, patch("agent.check_ollama_model"), \
                    patch("agent.model_text", return_value="At the station.") as model:
                out, err = io.StringIO(), io.StringIO()
                interactive_chat(db, io.StringIO("/resume empty\n/search notebook\nWhere is the notebook?\n"),
                                 out, err, apply=False, model="fixture")
                self.assertEqual(err.getvalue(), "")
                for visible in (model.call_args.args[0][0]["content"], out.getvalue()):
                    self.assertIn("The notebook is at the station.", visible)
                    for hidden in ("later parent", "later intermediate", "sibling", "guest"):
                        self.assertNotIn(hidden, visible)
                root_hits = search_transcripts(db, "notebook", in_session="text")
                self.assertEqual({r["session"] for r in root_hits}, {"text"})

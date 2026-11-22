import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import mac_context
from agent import interactive_chat
from agent_memory import open_memory
from vault_watch import VaultPublisher


class VaultWatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.publisher = VaultPublisher(self.state, self.root)
        self.folder = self.publisher.folder

    def test_commits_create_and_retract_shared_evidence_and_verified_findings(self):
        mac_context.configure(self.state, ["Mail"])
        with closing(sqlite3.connect(self.state / mac_context.INDEX_NAME)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""CREATE TABLE memory_connections(project, app, source, record);
                CREATE TABLE personal(app, source, record, body);
                CREATE TABLE ai_memory_reviews(project,findings);
                CREATE TABLE ai_memory_dismissals(project,finding_id);""")
            proofs = []
            for index, app in enumerate(("Files", "Mail"), start=1):
                text = f"Private text {index}"
                record = f"private-record-{index}"
                db.execute("INSERT INTO personal VALUES(?,?,?,?)", (app,"source",record,text))
                for project in ("atlas", "maps"):
                    db.execute("INSERT INTO memory_connections VALUES(?,?,?,?)", (project,app,"source",record))
                proofs.append([index,app,"source",record,hashlib.sha256(text.encode()).hexdigest()])
            db.execute("INSERT INTO ai_memory_reviews VALUES(?,?)", ("atlas",json.dumps([
                dict(kind="relationship", claim="A supported hypothesis", proofs=proofs)])))
            self.publisher.poll()
            self.assertFalse(list((self.folder / "Projects").glob("*.md")))
            db.commit()
            self.publisher.poll()
            evidence = list((self.folder / "Evidence").glob("*.md"))
            self.assertEqual(len(evidence), 2)
            for path in evidence:
                for project in ("atlas", "maps"):
                    self.assertIn(path.stem, (self.folder / "Projects" / f"{project}.md").read_text())
                self.assertNotIn("private-record", path.read_text())
                self.assertNotIn("Private text", path.read_text())
            finding, = (self.folder / "Findings").glob("*.md")
            self.assertIn("A supported hypothesis", finding.read_text())
            times = {p: p.stat().st_mtime_ns for p in self.folder.rglob("*.md")}
            self.assertIsNone(self.publisher.poll())
            self.assertEqual(times, {p: p.stat().st_mtime_ns for p in times})
            mac_context.configure(self.state, [])
            self.publisher.poll()
            self.assertFalse(finding.exists())
            self.assertEqual(len(list((self.folder / "Evidence").glob("*.md"))), 1)

    def test_code_edges_update_and_unrelated_notes_survive(self):
        module = self.root / "alpha.py"
        module.write_text("import beta\n")
        (self.root / "beta.py").write_text("answer = 2\n")
        self.publisher.poll()
        out = self.folder / "Code" / "alpha.py source.md"
        self.assertIn("[[Talos maps/Code/beta.py source]]", out.read_text())
        hand = out.with_name("Handwritten.md")
        hand.write_text("keep")
        (self.root / "beta.py").unlink()
        self.publisher.poll()
        self.assertNotIn("beta.py source", out.read_text())
        self.assertFalse(out.with_name("beta.py source.md").exists())
        self.assertEqual(hand.read_text(), "keep")
        self.assertIn("Removed beta.py", (self.folder / "Recent source edits.md").read_text())


class ConsoleParityTests(unittest.TestCase):
    def test_model_selection_clear_redraw_version_and_copy(self):
        with tempfile.TemporaryDirectory() as folder, closing(open_memory(Path(folder) / "chat.db")) as db:
            out, errors = io.StringIO(), io.StringIO()
            def response(messages, *, model, **kwargs):
                return "selected " + model
            with patch("agent.local_model_names",return_value=["small","large"]), \
                 patch("agent.check_ollama_model"), patch("agent.model_text",side_effect=response), \
                 patch("agent.subprocess.run") as clipboard:
                clipboard.return_value.returncode = 0
                interactive_chat(db, io.StringIO("/models\n/model large\nquestion\n/redraw\n/copy\n/clear\n/history\n/v\n"),
                                 out, errors, apply=False, model="small")
            self.assertFalse(errors.getvalue())
            self.assertIn("small, large", out.getvalue())
            self.assertIn("selected large", out.getvalue())
            self.assertIn("No recent text turns", out.getvalue())
            self.assertIn("Talos 0.", out.getvalue())
            self.assertEqual(clipboard.call_args.kwargs["input"], "selected large")

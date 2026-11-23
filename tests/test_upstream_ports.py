import io
import json
import os
import tempfile
import unittest
from contextlib import chdir, closing
from pathlib import Path

from agent import interactive_chat
from talos_core.memory.agent_memory import open_memory
from talos_core.memory.transcript_memory import remember
from talos_core.self_improvement.upstream_comparison import compare
from talos_core.self_improvement.upstream_review import inventory, parity_status, propose


class UpstreamPortsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.root / "test.sqlite"

    def console(self, commands):
        output, errors = io.StringIO(), io.StringIO()
        with closing(open_memory(self.db)) as db, chdir(self.root):
            interactive_chat(db, io.StringIO(commands), output, errors, apply=False, model="fixture")
        return output.getvalue(), errors.getvalue()

    def test_hermes_session_title_replay(self):
        self.console("/new atlas\n/title Launch   Plan\n")
        with closing(open_memory(self.db)) as db:
            remember(db, "console/atlas", [{"role": "user", "content": "Original conversation."}])
        out, err = self.console("/resume Launch Plan\n/title\n/history\n/sessions\n")
        self.assertFalse(err)
        self.assertIn("Current session: atlas", out)
        self.assertIn("Original conversation.", out)
        self.assertIn("atlas · Launch Plan", out)
        _, err = self.console("/new other\n/title Launch Plan\n")
        self.assertIn("another session", err)

    def test_openclaw_html_export_replay(self):
        with closing(open_memory(self.db)) as db:
            remember(db, "text", [{"role": "user", "content": "<script>hello</script>"},
                                  {"role": "assistant", "content": "second reply"}])
        out, err = self.console("/export-session\n/export\n/export ../outside.html\n")
        path = self.root / "talos-text.html"
        data = path.read_text()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertLess(data.index("user</h2>"), data.index("assistant</h2>"))
        self.assertIn("&lt;script&gt;hello&lt;/script&gt;", data)
        self.assertNotIn("<script>", data)
        self.assertIn("2 messages", out)
        self.assertEqual((self.root / "talos-text-2.html").read_text(), data)
        self.assertIn("inside the current workspace", err)

    def test_reference_inventory_and_idempotent_review(self):
        state = self.root / "state"
        hermes = state / "hermes-src/hermes_cli"
        claw = state / "openclaw-src/src/auto-reply"
        hermes.mkdir(parents=True)
        claw.mkdir(parents=True)
        (hermes / "commands.py").write_text('CommandDef("title", "Title the active session", "Session")')
        (claw / "commands-registry.shared.ts").write_text('defineBuiltinCommand("context", "Show context")')
        (self.root / "agent.py").write_text('def interactive_chat(cmd):\n match cmd:\n  case "/context": pass\n')
        (state / "private.txt").write_text("excluded")
        os.symlink(state / "private.txt", hermes / "link.py")
        self.assertEqual(inventory(self.root, state), {"Talos": 1, "Hermes": 1, "OpenClaw": 1})
        event = json.loads(propose(self.root, state))
        self.assertEqual(event["upstream"], "Hermes")
        self.assertIn("commands.py:1", event["reference"])
        self.assertEqual(propose(self.root, state), "No new console proposal")

    def test_model_review_requires_evidence_and_resumes_after_a_failure(self):
        state = self.root / "state"
        remote = state / "hermes-src/persistence/conversation"
        remote.mkdir(parents=True)
        (state / "openclaw-src").mkdir()
        (remote / "history.py").write_text("def upstream_history(): pass")
        (self.root / "talos_core/memory/transcript_memory.py").parent.mkdir(parents=True)
        (self.root / "talos_core/memory/transcript_memory.py").write_text("def talos_history(): pass")
        inventory(self.root, state)
        idea = dict(upstream_quote="upstream_history", talos_quote="talos_history",
                    feature="Compare local history retention", acceptance="Replay an isolated session")
        with self.assertRaisesRegex(ValueError, "verifiable"):
            compare(self.root, state, "test", infer=lambda *_: json.dumps({**idea, "talos_quote": "unknown"}))
        self.assertIn("Compared Hermes", compare(self.root, state, "test", infer=lambda *_: json.dumps(idea)))
        self.assertIn("No pending excerpts", compare(self.root, state, "test", infer=lambda *_: self.fail()))
        proposal = json.loads((state / "feature-requests.jsonl").read_text())
        self.assertEqual(proposal["origin"], "local-upstream-review")
        (self.root / "talos_core/memory/transcript_memory.py").write_text("def talos_history(): return []")
        abstain = lambda *_: json.dumps(dict.fromkeys(idea, ""))
        self.assertIn("Compared Hermes", compare(self.root, state, "test", infer=abstain))

    def test_discovery_reviews_an_unmapped_source_and_nominates_a_local_counterpart(self):
        state = self.root / "state"
        new_area = state / "openclaw-src/extensions/telegram"
        new_area.mkdir(parents=True)
        (state / "hermes-src").mkdir()
        (new_area / "delivery.ts").write_text("export function deliver() {}")
        (self.root / "agent.py").write_text("def start(): pass")
        (self.root / "talos_core/interfaces").mkdir(parents=True)
        (self.root / "talos_core/interfaces/telegram_gateway.py").write_text("def deliver(): pass")
        inventory(self.root, state)
        def abstain(prompt, model):
            self.assertEqual(json.loads(prompt)["talos_path"], "talos_core/interfaces/telegram_gateway.py")
            return json.dumps(dict.fromkeys(("upstream_quote", "talos_quote", "feature", "acceptance"), ""))
        self.assertIn("Compared OpenClaw extensions/telegram/delivery.ts", compare(self.root, state, "test", infer=abstain))
        self.assertIn("No pending excerpts", compare(self.root, state, "test", infer=lambda *_: self.fail()))

    def test_all_declared_command_names_are_eligible_for_replay(self):
        state = self.root / "state"
        hermes = state / "hermes-src/hermes_cli"
        hermes.mkdir(parents=True)
        (hermes / "commands.py").write_text('CommandDef("history", "Show history")\nCommandDef("journey", "Show journey")')
        (self.root / "agent.py").write_text('def interactive_chat(cmd):\n match cmd:\n  case "/history": pass\n')
        self.assertIn("1/2 names dispatch locally", parity_status(self.root, state, "Hermes"))
        self.assertIn("Missing · /journey", parity_status(self.root, state, "Hermes"))
        self.assertIn("/journey", json.loads(propose(self.root, state))["acceptance"])
        self.assertEqual(propose(self.root, state), "No new console proposal")

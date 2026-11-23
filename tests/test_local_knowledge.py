import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import graph_review
import mac_context
from vault_graph import publish_connections


class LocalKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.enterContext(
            closing(sqlite3.connect(self.state / mac_context.INDEX_NAME))
        )
        self.db.executescript("""
            CREATE TABLE memory_connections(project,app,source,record,PRIMARY KEY(project,app,source,record));
            CREATE VIRTUAL TABLE personal USING fts5(app UNINDEXED,source UNINDEXED,record UNINDEXED,body);
        """)
        self.events = []
        self.prompts = []
        self.maps = self.state / "map"
        self.maps.mkdir()

    def evidence(self, app, n, project="privateatlas", mention=None):
        row = (app, "private/path/" + app, "private-record-" + str(n))
        self.db.execute(
            "INSERT INTO memory_connections VALUES(?,?,?,?)", (project, *row)
        )
        self.db.execute(
            "INSERT INTO personal VALUES(?,?,?,?)",
            (*row, mention or project + " private text"),
        )
        self.db.commit()
        return row

    def ask(self, answer=None):
        def cpu(runtime, state, prompt, *, grammar):
            self.prompts.append(prompt)
            self.assertIn(
                grammar, (graph_review.ID_GRAMMAR, graph_review.FOCUS_GRAMMAR)
            )
            if callable(answer):
                result = answer()
            elif answer is None:
                result = (
                    '{"focus":"alias"}'
                    if grammar == graph_review.FOCUS_GRAMMAR
                    else '{"id":1}'
                )
            else:
                result = answer
            return result, 100, 7

        with patch.object(graph_review.local_source_model, "run_cpu", cpu):
            return graph_review.review_local(
                self.state, ("test-runtime",), self.events.append
            )

    def test_verified_group_grows_and_retracts_after_source_change(self):
        for app in ("Files", "Notes", "Calendar"):
            for i in range(2):
                self.evidence(app, i)
        self.assertIn("6 distinct indexed records across 3 source families", self.ask())
        publish_connections(self.db, self.maps, set())
        (group,) = list((self.maps / "Evidence groups").glob("*.md"))
        text = group.read_text()
        self.assertEqual(text.count("[[Talos maps/Evidence/Evidence "), 6)
        self.assertIn(group.stem, (self.maps / "Projects/privateatlas.md").read_text())
        with patch.object(
            graph_review, "choose_focus", return_value="no more projects"
        ):
            self.ask()
        self.assertEqual(len(self.prompts), 1)
        for private in (
            "privateatlas",
            "private/path",
            "private-record",
            "private text",
        ):
            self.assertNotIn(private, str(self.prompts) + str(self.events))
        self.assertNotIn("private/path", text)
        # Underlying text changed before the separate graph refresh ran.
        self.db.execute(
            "UPDATE personal SET body='unrelated' WHERE app='Calendar' AND record='private-record-1'"
        )
        self.db.commit()
        publish_connections(self.db, self.maps, set())
        self.assertFalse(group.exists())

    def test_same_family_excluded_and_opt_out_retracts_previous_group(self):
        for app in ("Files", "Cloud documents", "Messages"):
            for i in range(2):
                self.evidence(app, i)
        self.assertEqual(
            list(
                graph_review.source_groups(
                    self.db, {"Files", "Cloud documents"}, verified=False
                )
            ),
            [],
        )
        with patch.object(
            graph_review.mac_context, "enabled", return_value={"Messages"}
        ):
            self.assertIn("across 2 source families", self.ask())
        publish_connections(self.db, self.maps, {"Messages"})
        self.assertEqual(len(list((self.maps / "Evidence groups").glob("*.md"))), 1)
        publish_connections(self.db, self.maps, set())
        self.assertEqual(list((self.maps / "Evidence groups").glob("*.md")), [])

    def test_changed_or_invalid_selection_and_foreground_never_publish(self):
        for app in ("Files", "Notes"):
            for i in range(2):
                self.evidence(app, i)

        def change():
            self.db.execute("DELETE FROM memory_connections WHERE app='Notes'")
            self.db.commit()
            return '{"id":1}'

        with self.assertRaisesRegex(ValueError, "Invalid local knowledge selection"):
            self.ask('{"id":11}')
        with patch.object(graph_review, "conversation_busy", side_effect=[False, True]):
            self.assertIn("yielded", self.ask())
        self.assertIn("changed before commit", self.ask(change))
        self.assertFalse(
            self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='reviewed_source_groups'"
            ).fetchone()
        )

    def test_abstention_and_focus_survive_restart_without_spending_more_tokens(self):
        for app in ("Notes", "Files"):
            for i in range(2):
                self.evidence(app, i)
        self.assertIn("Abstained", self.ask('{"id":0}'))
        self.assertIn("alias", self.ask())
        self.assertIn("No new", self.ask())
        self.assertEqual(len(self.prompts), 2)
        self.assertTrue(
            self.db.execute("SELECT abstained FROM local_graph_review").fetchone()[0]
        )
        self.assertEqual(
            self.db.execute("SELECT model FROM project_review_focus").fetchone()[0],
            graph_review.local_source_model.MODEL,
        )

    def test_abstained_page_does_not_starve_later_eligible_projects(self):
        for project in range(7):
            for app in ("Files", "Notes"):
                for item in range(2):
                    self.evidence(app, project * 10 + item, project=f"fixture{project}")
        self.assertIn("Abstained", self.ask('{"id":0}'))
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM local_graph_abstentions").fetchone()[
                0
            ],
            6,
        )
        self.assertIn("Verified a project evidence group", self.ask())
        self.assertEqual(
            self.db.execute("SELECT project FROM reviewed_source_groups").fetchone()[0],
            "fixture6",
        )

    def test_local_project_pair_deduplicates_remote_and_rechecks_exact_proofs(self):
        for i in range(2):
            proof = self.evidence(
                "Notes", i, mention="privatenova privateatlas private text"
            )
            self.db.execute(
                "INSERT INTO memory_connections VALUES(?,?,?,?)",
                ("privatenova", *proof),
            )
        self.db.commit()
        self.assertIn("shared-evidence link backed by 2", self.ask())
        self.assertEqual(graph_review.candidates(self.db, {"Notes"}), [])
        self.assertEqual(
            self.db.execute("SELECT model FROM reviewed_project_pairs").fetchone(),
            (graph_review.local_source_model.MODEL,),
        )

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from talos_core.self_improvement import graph_review
from talos_core.mac import mac_context
from talos_core.memory import memory_connections
from talos_core.memory import memory_review
from talos_core.memory.personal_index import sync
from talos_core.self_improvement.upstream_comparison import discovery_candidate


class ProcessingTests(unittest.TestCase):
    def test_fresh_source_selection_stops_early_and_resumes_verified_receipts(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.executescript("""
                CREATE TABLE files(tree,path,bytes,sha256,PRIMARY KEY(tree,path)) WITHOUT ROWID;
                CREATE TABLE comparison_progress(tree,path,target,source_hash,target_hash,position,checked,
                    PRIMARY KEY(tree,path));
                INSERT INTO files VALUES('Talos','agent.py',20,'local');
            """)

            def add_sources(start, end):
                db.executemany(
                    "INSERT INTO files VALUES('Hermes',?,20,'source')",
                    ((f"tools/file-{i:05}.py",) for i in range(start, end)),
                )

            def first_pick_steps():
                steps = 0

                def count():
                    nonlocal steps
                    steps += 10
                    return 0

                db.set_progress_handler(count, 10)
                try:
                    self.assertEqual(
                        discovery_candidate(db, "Hermes")[3], "tools/file-00000.py"
                    )
                finally:
                    db.set_progress_handler(None, 0)
                return steps

            add_sources(0, 32)
            small = first_pick_steps()
            add_sources(32, 4096)
            self.assertLess(first_pick_steps(), 2 * small)
            db.execute(
                "DELETE FROM files WHERE tree='Hermes' AND path>'tools/file-00001.py'"
            )
            db.execute("""INSERT INTO comparison_progress VALUES
                ('Hermes','tools/file-00000.py','agent.py','source','local',10,100)""")
            self.assertEqual(
                discovery_candidate(db, "Hermes")[3], "tools/file-00001.py"
            )
            db.execute("""INSERT INTO comparison_progress VALUES
                ('Hermes','tools/file-00001.py','agent.py','source','local',7,80)""")
            self.assertEqual(
                discovery_candidate(db, "Hermes")[3:],
                ("tools/file-00001.py", 20, "source", "local", 7),
            )
            db.execute("UPDATE comparison_progress SET position=20")
            self.assertIsNone(discovery_candidate(db, "Hermes"))
            db.execute("UPDATE files SET sha256='changed' WHERE tree='Talos'")
            self.assertEqual(
                discovery_candidate(db, "Hermes")[3:],
                ("tools/file-00000.py", 20, "source", "changed", 0),
            )
            db.execute("UPDATE files SET sha256='local' WHERE tree='Talos'")
            db.execute(
                "UPDATE files SET sha256='upstream-edit' WHERE path='tools/file-00001.py'"
            )
            self.assertEqual(
                discovery_candidate(db, "Hermes")[3:],
                ("tools/file-00001.py", 20, "upstream-edit", "local", 0),
            )

    def test_export_delta_preserves_unchanged_proofs_duplicate_keys_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            library = state / "Library"

            def update(rows):
                return sync(state, library, knowledge=[("Notes", rows)])

            original = [
                ("keep", "a fact"),
                ("edit", "before"),
                ("gone", "old"),
                ("twice", "one"),
                ("twice", "two"),
            ]
            update(original)
            sync(
                state,
                library,
                knowledge=[("Projects", [("anchor", "separate source")])],
            )
            with closing(sqlite3.connect(state / mac_context.INDEX_NAME)) as db:

                def rows():
                    return db.execute(
                        "SELECT rowid,source,record,body FROM personal WHERE app='Notes' ORDER BY rowid"
                    ).fetchall()

                before = rows()
                _, source, _, _ = before[0]

                def proof(row):
                    ident, origin, key, text = row
                    return [
                        ident,
                        "Notes",
                        origin,
                        key,
                        hashlib.sha256(text.encode()).hexdigest(),
                    ]

                update(
                    [
                        ("twice", "one"),
                        ("edit", "after"),
                        ("keep", "a fact"),
                        ("new", "added"),
                        ("twice", "updated"),
                    ]
                )
                after = rows()
                self.assertEqual(
                    [(r[0], r[2], r[3]) for r in after[:4]],
                    [
                        (before[0][0], "keep", "a fact"),
                        (before[1][0], "edit", "after"),
                        (before[3][0], "twice", "one"),
                        (before[4][0], "twice", "updated"),
                    ],
                )
                self.assertEqual(after[4][2:], ("new", "added"))
                self.assertTrue(
                    memory_review.proof_is_current(db, proof(before[0]), {"Notes"})
                )
                self.assertFalse(
                    memory_review.proof_is_current(db, proof(before[1]), {"Notes"})
                )
                self.assertFalse(
                    memory_review.proof_is_current(db, proof(before[2]), {"Notes"})
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count FROM source_summary WHERE source=?", (source,)
                    ).fetchone(),
                    (5,),
                )
                version = db.execute("PRAGMA data_version").fetchone()
                update([(r[2], r[3]) for r in after])
                self.assertEqual(db.execute("PRAGMA data_version").fetchone(), version)

                def failed():
                    yield "keep", "partial"
                    raise ValueError("interrupted export")

                with self.assertRaisesRegex(ValueError, "interrupted export"):
                    update(failed())
                self.assertEqual(rows(), after)
                self.assertEqual(update([]).get("Notes", 0), 0)
                self.assertEqual(rows(), [])

    def test_project_catalog_migrates_once_and_tracks_changes_transactionally(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            library = state / "Library"
            project = lambda name: (name + ":" + ("a" * 40), name)
            sync(
                state,
                library,
                knowledge=[("Projects", [project("Atlas"), project("Nova")])],
            )
            path = state / mac_context.INDEX_NAME
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DROP TABLE indexed_projects")
                db.execute(
                    "INSERT INTO personal VALUES('Projects','legacy',?,?)",
                    project("AtLaS"),
                )
                db.execute(
                    "INSERT INTO source_summary VALUES('legacy','Projects','',1,'[]')"
                )
            memory_connections.refresh(state)

            def known():
                with closing(sqlite3.connect(path)) as db:
                    return list(
                        db.execute(
                            "SELECT name,source FROM indexed_projects ORDER BY name,source"
                        )
                    )

            before = known()
            self.assertEqual([name for name, _ in before], ["atlas", "atlas", "nova"])
            with closing(sqlite3.connect(path)) as db:

                def guard(action, table, column, *unused):
                    # A fresh catalog must not read any FTS content to discover project names.
                    if action == sqlite3.SQLITE_READ and table.startswith("personal"):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                db.set_authorizer(guard)
                memory_connections.ensure_project_catalog(db)

            def interrupted():
                raise RuntimeError("interrupted second source")
                yield

            with self.assertRaisesRegex(RuntimeError, "interrupted second source"):
                sync(
                    state,
                    library,
                    knowledge=[
                        ("Projects", [project("Delta")]),
                        ("Notes", interrupted()),
                    ],
                )
            self.assertEqual(known(), before)
            sync(
                state,
                library,
                knowledge=[("Projects", [project("Delta"), ("invalid", "untracked")])],
            )
            self.assertEqual([name for name, _ in known()], ["delta"])
            # Simulate a worker that was started before this release. The record
            # count is unchanged, so a count-only freshness check would miss it.
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "UPDATE personal SET record=? WHERE app='Projects' AND record!='invalid'",
                    (project("Omega")[0],),
                )
                db.execute(
                    "UPDATE source_summary SET signature='' WHERE app='Projects'"
                )
            memory_connections.refresh(state)
            self.assertEqual([name for name, _ in known()], ["omega"])
            sync(state, library, knowledge=[("Projects", [])])
            self.assertEqual(known(), [])

    def test_reverse_evidence_lookup_tracks_new_and_removed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            library = state / "Library"
            sync(
                state,
                library,
                knowledge=[
                    (
                        "Projects",
                        [(f"{name}:" + ("a" * 40), name) for name in ("Atlas", "Nova")],
                    ),
                    ("Notes", [("a", "Atlas Nova meeting"), ("b", "Atlas Nova trip")]),
                ],
            )
            memory_connections.refresh(state)
            with closing(sqlite3.connect(state / mac_context.INDEX_NAME)) as db:
                self.assertEqual(
                    len(
                        list(
                            db.execute(
                                "PRAGMA index_info(memory_connections_by_record)"
                            )
                        )
                    ),
                    4,
                )
                self.assertEqual(
                    graph_review.candidates(db, {"Notes"}), [("atlas", "nova", 2, 1)]
                )
                self.assertEqual(graph_review.candidates(db, {"Files"}), [])
            sync(state, library, knowledge=[("Notes", [("a", "Atlas Nova meeting")])])
            memory_connections.refresh(state)
            with closing(sqlite3.connect(state / mac_context.INDEX_NAME)) as db:
                self.assertEqual(graph_review.candidates(db, {"Notes"}), [])


if __name__ == "__main__":
    unittest.main()

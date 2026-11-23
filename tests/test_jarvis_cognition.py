"""End-to-end tests for Jarvis unified knowledge database access & self-thinking loops."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import agent
from talos_core.cognition import cognitive_engine, goal_store
from talos_core.memory import commitment_memory, transcript_memory
from talos_core.memory.agent_memory import open_memory
from talos_core.room.room_service import Room
from talos_core.runtime import background_work, maintenance
from talos_core.self_improvement import overnight_goal


def _seed_personal_index(state: Path) -> None:
    index_path = state / "personal-index-v2.sqlite3"
    with closing(sqlite3.connect(index_path)) as db:
        db.executescript(
            """
            CREATE VIRTUAL TABLE personal USING fts5(app, source, record, body);
            CREATE TABLE ai_memory_reviews (
                project TEXT,
                fingerprint TEXT,
                checked_ns INTEGER,
                findings TEXT
            );
            CREATE TABLE project_review_focus (
                project TEXT,
                focus TEXT,
                model TEXT
            );
            CREATE TABLE reviewed_project_pairs (
                a TEXT,
                b TEXT,
                model TEXT
            );
            CREATE TABLE memory_connections (
                project TEXT,
                app TEXT,
                source TEXT,
                record TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO personal(app, source, record, body) VALUES (?, ?, ?, ?)",
            ("notes", "Architecture Note", "Arc Reactor v4", "Palladium core replaced by vibranium lattice at 42 GW."),
        )
        db.execute(
            "INSERT INTO personal(app, source, record, body) VALUES (?, ?, ?, ?)",
            ("Mail", "Pepper Potts", "Stark Expo Keynote", "Keynote slide deck deadline moved to Friday 09:00 UTC."),
        )
        db.execute(
            "INSERT INTO ai_memory_reviews(project, fingerprint, checked_ns, findings) VALUES (?, ?, ?, ?)",
            (
                "Talos",
                "fp-1",
                1700000000,
                json.dumps({"findings": ["Unified telemetry bus reduces room voice round-trip jitter by 38%."]}),
            ),
        )
        db.execute(
            "INSERT INTO project_review_focus(project, focus, model) VALUES (?, ?, ?)",
            ("Talos", "Low-latency local voice synthesis and proactive commitment radar", "qwen3:4b"),
        )
        db.execute(
            "INSERT INTO reviewed_project_pairs(a, b, model) VALUES (?, ?, ?)",
            ("Talos", "OpenClaw", "qwen3:4b"),
        )
        db.execute(
            "INSERT INTO memory_connections(project, app, source, record) VALUES (?, ?, ?, ?)",
            ("Talos", "notes", "Architecture Note", "Arc Reactor v4"),
        )
        db.commit()


class JarvisCognitionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = self.state / "agent.sqlite3"
        (self.state / "mac-context.json").write_text(
            json.dumps(["Mail", "Messages", "Contacts"]),
            encoding="utf-8",
        )
        _seed_personal_index(self.state)
        vault = self.state / "Talos Memory"
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "Mark_VII_Flight_Control.md").write_text(
            "# Mark VII Flight Control\nThruster gimbal calibration uses titanium-gold ailerons at 180 Hz.\n",
            encoding="utf-8",
        )

    def test_unified_knowledge_access_spans_every_store_and_enforces_privacy(self):
        now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
        with closing(open_memory(self.db_path)) as db:
            cognitive_engine.ensure_tables(db)
            commitment_memory.schema(db)
            with db:
                # 1. Personal facts
                db.execute(
                    "INSERT INTO personal_facts(session, subject, value) VALUES ('text', 'workshop_code', 'Gamma-9-Delta')",
                )
                # 2. Commitments & context_evidence promises
                db.execute(
                    """
                    INSERT INTO commitments(session, commitment, status, created_at, review_after)
                    VALUES ('text', 'Send Pepper the Stark Expo schematic', 'open', ?, ?)
                    """,
                    (now.isoformat(), (now + timedelta(hours=2)).isoformat()),
                )
                src = db.execute(
                    """
                    INSERT INTO context_sources(path, kind, owner, topic, person, retain, speak)
                    VALUES ('/tmp/notes.md', 'document', 'James', 'Expo', 'Happy Hogan', 1, 0)
                    """
                ).lastrowid
                db.execute(
                    """
                    INSERT INTO context_evidence(source, seq, stamp, kind, detail, due, pointer)
                    VALUES (?, 1, ?, 'promise', 'Deliver prototype flight stabilizer to Happy Hogan', 'Friday', 'line 12')
                    """,
                    (src, now.timestamp()),
                )
            # 3. Goals
            goal_store.save_goal(
                db,
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "outcome": "Calibrate Arc Reactor v4 harmonic dampers",
                    "topic": "Arc Reactor v4",
                    "deadline": (now + timedelta(days=2)).isoformat(),
                },
            )
            # 4. Inbox task + transcripts
            with db:
                dec = db.execute(
                    "INSERT INTO decisions(recorded_at, event, proposal) VALUES (?, '{}', '{}')",
                    (now.isoformat(),),
                ).lastrowid
                db.execute(
                    "INSERT INTO inbox(id, kind, text, due_at) VALUES (?, 'task', ?, ?)",
                    (dec, "Inspect Malibu workshop optical bus", now.isoformat()),
                )
            transcript_memory.remember(
                db,
                "s1",
                [{"role": "user", "content": "Remember that Project Veronica orbital telemetry uses channel 77."}],
            )

            status = cognitive_engine.knowledge_status(db, state=self.state)
            self.assertTrue(status["stores"]["personal_index"]["available"])
            self.assertTrue(status["stores"]["vault"]["available"])
            self.assertGreaterEqual(status["stores"]["personal_index"]["count"], 2)
            self.assertEqual(status["stores"]["ai_memory_reviews"]["count"], 1)
            self.assertEqual(status["stores"]["memory_connections"]["count"], 1)
            self.assertEqual(status["stores"]["vault"]["count"], 1)
            self.assertEqual(status["stores"]["personal_facts"]["count"], 1)
            self.assertGreaterEqual(status["stores"]["commitments"]["count"], 1)
            self.assertEqual(status["stores"]["goals"]["count"], 1)
            self.assertGreaterEqual(status["stores"]["inbox"]["count"], 1)

            # Query across personal index + goals + memory reviews
            arc_res = cognitive_engine.query_knowledge(db, "Arc Reactor telemetry", state=self.state)
            stores = {hit["store"] for hit in arc_res["results"]}
            self.assertTrue(any(s.startswith("personal_index") for s in stores))
            self.assertIn("goals", stores)
            self.assertIn("ai_memory_reviews", stores)

            # Query Vault Markdown
            vault_res = cognitive_engine.query_knowledge(db, "titanium gold ailerons", state=self.state)
            self.assertTrue(any(h["store"] == "vault" for h in vault_res["results"]))

            # Query Personal Facts + Commitments + Promises + Inbox + Transcripts
            self.assertTrue(
                any(h["store"] == "personal_facts" for h in cognitive_engine.query_knowledge(db, "Gamma-9-Delta", state=self.state)["results"])
            )
            self.assertTrue(
                any(h["store"] == "commitments" for h in cognitive_engine.query_knowledge(db, "Stark Expo schematic", state=self.state)["results"])
            )
            self.assertTrue(
                any(h["store"].startswith("context:") for h in cognitive_engine.query_knowledge(db, "Happy Hogan stabilizer", state=self.state)["results"])
            )
            self.assertTrue(
                any(h["store"].startswith("inbox:") for h in cognitive_engine.query_knowledge(db, "Malibu optical bus", state=self.state)["results"])
            )
            self.assertTrue(
                any(h["store"] == "transcripts" for h in cognitive_engine.query_knowledge(db, "Veronica channel 77", state=self.state)["results"])
            )

            # Privacy enforcement: unverified voice (`private_ok=False`) blocks private personal index, vault, facts, transcripts, and speak=0 promises
            public_arc = cognitive_engine.query_knowledge(
                db, "vibranium lattice titanium Gamma-9-Delta Veronica Happy Hogan", state=self.state, private_ok=False
            )
            leaked_stores = {
                h["store"]
                for h in public_arc["results"]
                if h["store"].startswith("personal_index")
                or h["store"].startswith("context:")
                or h["store"] in {"vault", "personal_facts", "transcripts", "ai_memory_reviews"}
            }
            self.assertEqual(leaked_stores, set())

            # App permission gating: disabling Mail in mac-context.json hides Mail records from personal_index
            (self.state / "mac-context.json").write_text(json.dumps(["Messages"]), encoding="utf-8")
            mail_hits = cognitive_engine.query_knowledge(db, "Pepper Potts Keynote", state=self.state)["results"]
            self.assertFalse(any(h["store"].startswith("personal_index:Mail") for h in mail_hits))

    def test_self_thinking_captures_obligations_critiques_turns_and_stages_intentions(self):
        now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
        with closing(open_memory(self.db_path)) as db:
            cognitive_engine.ensure_tables(db)
            background_work.ensure(db)

            # Implicit obligation capture (idempotent)
            captured = cognitive_engine.capture_implicit_intent(
                db,
                "Don't let me forget to review the orbital defence telemetry tomorrow morning.",
                session="room",
                now=now,
            )
            self.assertIsNotNone(captured)
            self.assertIn("review the orbital defence telemetry", captured["commitment"])
            dup = cognitive_engine.capture_implicit_intent(
                db,
                "Don't let me forget to review the orbital defence telemetry tomorrow morning.",
                session="room",
                now=now,
            )
            self.assertIsNone(dup)

            # Overdue commitment + urgent goal
            with db:
                db.execute(
                    """
                    INSERT INTO commitments(session, commitment, status, created_at, review_after)
                    VALUES ('room', 'Upload clean-slate firmware checksum', 'open', ?, ?)
                    """,
                    ((now - timedelta(days=1)).isoformat(), (now - timedelta(hours=3)).isoformat()),
                )
            goal_store.save_goal(
                db,
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "outcome": "Finalize Jericho acoustic shield test",
                    "topic": "Jericho shield",
                    "deadline": (now + timedelta(hours=6)).isoformat(),
                },
            )

            # Turn self-critique: user correction + unverified action claim
            c1 = cognitive_engine.critique_turn(
                db,
                "No, that's wrong — I asked for the vibranium core temperature.",
                "The palladium core is at 300 K.",
                session="room",
                tool="none",
                latency_ms=420,
                now=now,
            )
            self.assertIsNotNone(c1)
            self.assertEqual(c1["kind"], "user_correction")

            c2 = cognitive_engine.critique_turn(
                db,
                "Set a reminder for 8pm",
                "I've set a reminder for 8pm for you.",
                session="room",
                tool="none",
                latency_ms=300,
                now=now,
            )
            self.assertIsNotNone(c2)
            self.assertEqual(c2["kind"], "unverified_claim")

            # Run autonomous think_once cycle
            thought = cognitive_engine.think_once(db, self.state, now=now)
            self.assertGreaterEqual(thought["staged_count"], 2)
            self.assertTrue((self.state / "jarvis-brief.json").exists())
            brief_disk = json.loads((self.state / "jarvis-brief.json").read_text(encoding="utf-8"))
            self.assertIn("Jericho acoustic shield", json.dumps(brief_disk))

            # Verify autonomous background work & inbox follow-up were staged once and not duplicated on repeat tick
            jobs_before = db.execute("SELECT COUNT(*) FROM background_work").fetchone()[0]
            inbox_before = db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
            self.assertGreaterEqual(jobs_before, 1)
            self.assertGreaterEqual(inbox_before, 1)

            thought_again = cognitive_engine.think_once(db, self.state, now=now + timedelta(minutes=1))
            self.assertEqual(thought_again["staged_count"], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM background_work").fetchone()[0], jobs_before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], inbox_before)

    def test_planner_console_commands_and_auto_recall_access_knowledge_database(self):
        with closing(open_memory(self.db_path)) as db:
            # Verify planner read_tool("search_knowledge", ...) works
            tool_out = agent.read_tool(db, "search_knowledge", "vibranium lattice")
            self.assertIn("vibranium lattice", json.dumps(tool_out))

            # Verify console_prompt injects unified knowledge context (non-transcript stores)
            messages, stats = agent.console_prompt(db, "Tell me about the vibranium lattice in Arc Reactor v4", "s-console")
            joined = "\n".join(m["content"] for m in messages)
            self.assertIn("vibranium lattice", joined)
            self.assertTrue(len(stats) > 0)

            # Verify interactive_chat /knowledge (/kb) and /brief commands
            out = io.StringIO()
            err = io.StringIO()
            with patch("agent.check_ollama_model"):
                agent.interactive_chat(
                    db,
                    io.StringIO("/kb vibranium lattice\n/brief\n/quit\n"),
                    out,
                    err,
                    apply=False,
                    model="fixture",
                )
            self.assertEqual(err.getvalue(), "")
            rendered = out.getvalue()
            self.assertIn("vibranium", rendered.lower())
            self.assertIn("Recent knowledge insight", rendered)

    def test_room_service_cognitive_endpoints_and_local_queries(self):
        room = Room(self.db_path, "fixture")
        status, kb_info = room.route("GET", "/v1/cognitive/knowledge", {})
        self.assertEqual(status, 200)
        self.assertTrue(kb_info["stores"]["personal_index"]["available"])
        self.assertTrue(kb_info["stores"]["vault"]["available"])

        status, search_res = room.route("POST", "/v1/cognitive/search", {"query": "titanium gold ailerons"})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(search_res["results"]), 1)
        self.assertIn("ailerons", json.dumps(search_res["results"]).lower())

        status, brief_res = room.route("GET", "/v1/cognitive/brief", {})
        self.assertEqual(status, 200)
        self.assertIn("headline", brief_res)

        # Direct room turn answered locally from unified knowledge without needing external model
        status, turn_res = room.route(
            "POST",
            "/v1/turn",
            {"text": "Search my knowledge base for vibranium lattice", "source": "text"},
        )
        self.assertEqual(status, 200)
        self.assertIn("vibranium", turn_res["reply"].lower())

    def test_overnight_revisit_goals_and_maintenance_run_cognitive_cycle(self):
        with closing(open_memory(self.db_path)) as db:
            goal_store.save_goal(
                db,
                {
                    "id": "00000000-0000-0000-0000-000000000003",
                    "outcome": "Verify overnight Jarvis synthesis",
                    "topic": "Jarvis synthesis",
                    "deadline": datetime.now(UTC).isoformat(),
                },
            )
        now_ts = datetime.now(UTC).timestamp()
        with patch("talos_core.cognition.goal_store.refresh_in_background"):
            overnight_goal.revisit_goals(self.state, now_ts)
        self.assertTrue((self.state / "jarvis-brief.json").exists())

        # Also verify maintenance.py cognitive job executes cleanly end-to-end
        job_out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps({"kind": "cognitive"}) + "\n")), patch("sys.stdout", job_out):
            maintenance.job_loop(self.state, "fixture", self.state)
        payload = json.loads(job_out.getvalue().strip())
        self.assertIn("result", payload)
        self.assertNotIn("error", payload)


if __name__ == "__main__":
    unittest.main()

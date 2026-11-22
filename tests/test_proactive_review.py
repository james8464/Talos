import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import wave
import base64
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from unittest.mock import patch

import run_talos
from tools import talos_service
import graph_review
import mac_context
import respan_worker
import room_checkins
import talos_activity
from agent_memory import open_memory
from room_service import Room
from room_voice import DEFAULT_VOICE, KokoroVoice
from upstream_review import inventory
from vault_graph import publish_connections


class CheckInTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.state / "room.sqlite"
        self.room = Room(self.path, "fixture", online=False)
        self.db = self.enterContext(closing(open_memory(self.path)))

    def say(self, text, **kwargs):
        return self.room.route("POST", "/v1/voice/facing", {"source":"voice","text":text, **kwargs})[1]

    def due(self, item, elapsed=1):
        with self.db:
            self.db.execute("UPDATE inbox SET due_at=? WHERE id=?",
                            ((datetime.now(UTC)-timedelta(seconds=elapsed)).isoformat(), item))
        return self.room.route("GET", "/v1/speech", {})[1]["item"]

    def ack(self, item, outcome="played"):
        return self.room.route("POST", "/v1/speech/ack", {"id":item,"playback":outcome})

    def test_spoken_checkin_restart_delivery_retry_and_human_response(self):
        key = str(uuid4())
        initial = self.say("Hey Talos, check in with me in three hours.", request_id=key)
        self.assertIn("180 minutes", initial["reply"])
        self.assertIn("already", self.say("check in with me in three hours", request_id=key)["reply"])
        self.room = Room(self.path, "fixture", online=False)
        self.assertIsNone(self.room.route("GET","/v1/speech",{})[1]["item"])
        first = self.due(initial["id"])
        self.assertEqual((first["check_in"], first["text"]), (True, room_checkins.QUESTION))
        self.assertEqual(self.ack(first["id"])[0], 200)
        self.assertEqual(self.ack(first["id"])[0], 200)
        rows = self.db.execute("SELECT id,due_at FROM inbox ORDER BY id").fetchall()
        self.assertEqual(len(rows),2)
        self.assertAlmostEqual((datetime.fromisoformat(rows[1][1])-datetime.now(UTC)).total_seconds(),600,delta=3)
        last = self.due(rows[1][0])
        self.assertEqual(last["text"],room_checkins.RETRY)
        self.ack(last["id"])
        self.assertIn("closed",self.say("I’m okay.")["reply"])
        self.assertIsNone(self.room.route("GET","/v1/speech",{})[1]["item"])
        self.assertEqual(self.db.execute("SELECT status FROM room_checkins").fetchone()[0], "answered")

    def test_self_report_offers_a_checkin_without_scheduling_or_storing_condition(self):
        self.assertIn("Would you like", self.say("Talos, I’m feeling unwell.")["reply"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM inbox").fetchone()[0], 0)
        self.assertIsNone(room_checkins.handle(self.db, "I'm not sick.", apply=True))
        self.assertIsNone(room_checkins.handle(self.db, "My friend is sick.", apply=True))

    def test_interruption_silence_and_cancel_never_generate_extra_prompts(self):
        first = self.say("Check on me in one minute")["id"]
        self.assertIsNone(self.due(first, 121))
        self.assertEqual(self.db.execute("SELECT status FROM room_checkins").fetchone()[0],"expired")
        second = self.say("Check on me in two minutes")["id"]
        self.due(second)
        self.ack(second, "interrupted")
        self.assertEqual(self.db.execute("SELECT count(*) FROM inbox").fetchone()[0],2)
        third = self.say("Check on me in one minute")["id"]
        self.due(third)
        self.assertIn("closed", self.say("I am fine")["reply"])
        self.ack(third)
        self.assertEqual(self.db.execute("SELECT count(*) FROM inbox").fetchone()[0],3)
        fourth = self.say("Check on me in one minute")["id"]
        self.assertIn("cancelled",self.say("Stop the check-in")["reply"])
        self.assertIsNone(self.due(fourth))
        self.room.apply = False
        self.assertIn("preview",self.say("Check on me in ten minutes")["reply"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM inbox").fetchone()[0],4)

    @unittest.skipUnless(os.getenv("TALOS_TEST_KOKORO")=="1", "requires the installed Kokoro voice")
    def test_spoken_request_checkin_and_answer_have_playable_audio(self):
        self.room.voice = KokoroVoice(Path(__file__).resolve().parents[1]/"models/kokoro-room", DEFAULT_VOICE)
        result = self.say("Talos, check in with me in one minute.")
        first = self.due(result["id"])
        self.ack(first["id"])
        for text in (result["reply"],first["text"],self.say("I'm okay")["reply"]):
            status, data = self.room.route("POST","/v1/voice",{"text":text})
            self.assertEqual(status,200)
            with wave.open(io.BytesIO(base64.b64decode(data["wav"]))) as wav:
                self.assertGreater(wav.getnframes(),wav.getframerate())
                self.assertTrue(any(wav.readframes(wav.getnframes())))

    @unittest.skipUnless(os.getenv("TALOS_TEST_SPOKEN_CHECKIN")=="1", "requires installed local Whisper and Kokoro")
    def test_synthesized_user_speech_is_transcribed_and_follows_through(self):
        root = Path(__file__).resolve().parents[1]
        self.room.voice = KokoroVoice(root/"models/kokoro-room", DEFAULT_VOICE)
        recognizer = (
            "import json; from telegram_voice import render_note, transcribe_note; "
            "print(json.dumps([transcribe_note(render_note(phrase)) for phrase in "
            "('Check in with me in one minute.', 'I am okay.')]))"
        )
        result = subprocess.run([str(root/"state/telegram-voice-env/bin/python"),"-c",recognizer],
                                capture_output=True,text=True,cwd=root,check=False,timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        request, answer = json.loads(result.stdout)
        started = self.say(request)
        self.assertIn("id", started, request)
        question = self.due(started["id"])
        self.assertTrue(question["check_in"])
        self.ack(started["id"])
        closed = self.say(answer)["reply"]
        self.assertIn("closed",closed,answer)
        for reply in (started["reply"],question["text"],closed):
            encoded = self.room.route("POST","/v1/voice",{"text":reply})[1]["wav"]
            with wave.open(io.BytesIO(base64.b64decode(encoded))) as wav:
                self.assertTrue(any(wav.readframes(wav.getnframes())))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root/"state"
        a = self.state/"openclaw-src/src/auto-reply"
        h = self.state/"hermes-src/cron"
        a.mkdir(parents=True)
        h.mkdir(parents=True)
        (a/"heartbeat.ts").write_text("export const acknowledgement = 'quiet';")
        (h/"missed_runs.py").write_text("def expire_once(): return True")
        for p in ("respan_worker.py","room_checkins.py"):
            (self.root/p).write_text("def current_behavior(): return True")
        inventory(self.root,self.state)

    def test_both_pins_reviewed_quoted_proposal_and_silent_abstention(self):
        def call(messages, **options):
            payload = json.loads(messages[1]["content"])
            if payload["upstream_path"].endswith(".py"):
                return "HEARTBEAT_OK", {}
            self.assertEqual(payload["upstream_path"],"src/auto-reply/heartbeat.ts")
            return json.dumps(dict(upstream_quote="acknowledgement",talos_quote="current_behavior",
                                   feature="Suppress empty reports",acceptance="Replay an empty poll")), {"prompt_tokens":44}
        for project in ("OpenClaw","Hermes"):
            self.assertIn("Compared "+project,respan_worker.turn(self.root,self.state,job=project,call=call))
        items=(self.state/"feature-requests.jsonl").read_text().splitlines()
        self.assertEqual(len(items),1)
        self.assertEqual(json.loads(items[0])["origin"],"respan-upstream-review")
        self.assertIsNone(respan_worker.turn(self.root,self.state,job="Hermes",call=call))
        rows=talos_activity.events(self.state,0)
        self.assertIn("44 prompt_tokens"," ".join(r[3] for r in rows))
        self.assertIn("no supported improvement"," ".join(r[3] for r in rows))

    def test_unavailable_provider_backs_off_without_advancing_cursor(self):
        def fail(*args,**kwargs):
            raise RuntimeError("Respan returned HTTP 429.")
        for now, due in ((100,160),(160,280)):
            self.assertIn("429",respan_worker.turn(self.root,self.state,job="Hermes",clock=lambda:now,call=fail))
            with closing(sqlite3.connect(self.state/respan_worker.DB)) as db:
                self.assertEqual(db.execute("SELECT due FROM jobs WHERE name='Hermes'").fetchone()[0],due)
        with closing(sqlite3.connect(self.state/"upstream-inventory.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM comparison_progress").fetchone()[0],0)

    def test_full_queue_retains_review_and_promotes_it_after_completion(self):
        queue = self.state/"feature-requests.jsonl"
        queue.write_text("".join(json.dumps({"id":str(i),"status":"proposed"})+"\n" for i in range(5)))
        proposal = json.dumps(dict(upstream_quote="expire_once",talos_quote="current_behavior",
                                   feature="Retire missed runs",acceptance="Replay a restart"))
        respan_worker.turn(self.root,self.state,job="Hermes",clock=lambda:100,call=lambda *a,**k:(proposal,{}))
        self.assertEqual(len(queue.read_text().splitlines()),5)
        with queue.open("a") as out:
            out.write(json.dumps({"id":"0","status":"done"})+"\n")
        respan_worker.turn(self.root,self.state,job="Hermes",clock=lambda:701,call=lambda *a,**k:("NO_REPLY",{}))
        events=[json.loads(line) for line in queue.read_text().splitlines()]
        self.assertEqual(events[-1]["feature"],"Retire missed runs")
        self.assertEqual(len(events),7)

    def test_service_opt_in_and_attach_require_a_healthy_room(self):
        self.assertNotIn("--respan-reviews",talos_service.configuration(self.root)["ProgramArguments"])
        self.assertIn("--respan-reviews",talos_service.configuration(self.root,respan=True)["ProgramArguments"])
        token = self.root/"token"
        token.touch()
        with patch.object(run_talos,"TOKEN_PATH",token), patch.object(run_talos.talos_activity,"probe",return_value="stopped"), \
             patch.object(run_talos,"build_app") as build, patch.object(run_talos.subprocess,"run") as launch:
            with self.assertRaises(RuntimeError):
                run_talos.attach_room(8765)
            build.assert_not_called()
            with patch.object(run_talos.talos_activity,"probe",return_value="running · model ready"):
                run_talos.attach_room(8765)
            launch.assert_called_once()
        with patch.object(run_talos.subprocess,"Popen") as spawn:
            run_talos.start_memory_workers("fixture",local_only=True,children=[])
            self.assertNotIn("respan_worker.py",str(spawn.call_args_list))
            run_talos.start_memory_workers("fixture",local_only=True,children=[],respan=True,room_owner=True)
            self.assertIn("--room-owner",spawn.call_args.args[0])

    def test_graph_choice_is_anonymous_and_revocation_retracts_edge(self):
        idx=self.state/mac_context.INDEX_NAME
        with closing(sqlite3.connect(idx)) as db, db:
            db.execute("CREATE TABLE memory_connections(project,app,source,record,PRIMARY KEY(project,app,source,record))")
            db.executemany("INSERT INTO memory_connections VALUES(?,?,?,?)",
                           [(p,app,"private path","secret"+app) for p in ("atlas","nova") for app in ("Files","Notes")])
        prompts=[]
        def choose(system,prompt):
            prompts.append(prompt)
            return '{"id":1}' if "shared_records" in prompt else '{"focus":"alias"}'
        self.assertIn("Accepted one",graph_review.review(self.state,choose,lambda _:None))
        self.assertIn("alias",graph_review.review(self.state,choose,lambda _:None))
        self.assertFalse(any(word in str(prompts) for word in ("atlas","nova","secret","private path")))
        out=self.root/"maps"
        out.mkdir()
        with closing(sqlite3.connect(idx)) as db, db:
            publish_connections(db,out,set())
            self.assertIn("Projects/nova", (out/"Projects/atlas.md").read_text())
            db.execute("DELETE FROM memory_connections WHERE app='Notes'")
            publish_connections(db,out,set())
            self.assertNotIn("Projects/nova",(out/"Projects/atlas.md").read_text())

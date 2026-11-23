import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.retire_upstream import Port, retire

import run_talos
from talos_core.runtime import talos_activity
from talos_core.recovery import PollBackoff, reconnect_delay, stable_connection
from talos_core.memory.vault_watch import VaultPublisher


class RecoveryTests(unittest.TestCase):
    def test_retired_scheduling_helpers_through_talos(self):
        script = """
            import assert from 'node:assert/strict';
            import { pruneMapToMaxSize } from './map-size.ts';
            import { parseStrictFiniteNumber } from './number-coercion.ts';
            import { normalizeOptionalString } from './string-coerce.ts';
            import { isOffsetlessIsoDateTime } from './iso-time.ts';
            import { computeNextRunAtMs } from './schedule.ts';
            const m = new Map([['a',1],['b',2]]);
            pruneMapToMaxSize(m,1);
            assert.deepEqual([...m.keys()],['b']);
            assert.equal(parseStrictFiniteNumber('12junk'),undefined);
            assert.equal(normalizeOptionalString(' Europe/Paris '),'Europe/Paris');
            assert.equal(isOffsetlessIsoDateTime('2026-10-25T02:30:00'),true);
            const next = computeNextRunAtMs({kind:'cron',expr:'30 2 * * *',tz:'Europe/Paris'},
                                            Date.parse('2026-10-24T23:00:00Z'));
            assert.equal(new Date(next).toISOString(),'2026-10-25T00:30:00.000Z');
        """
        subprocess.run(["node","--input-type=module","-e",script],
                       cwd=Path(__file__).resolve().parents[1]/"talos_core/scheduling",check=True)

    def test_poll_ladder_reset_independence_and_expiry(self):
        policy = PollBackoff()
        self.assertEqual([policy.record("room", False, 10) for _ in range(6)], [5, 10, 30, 60, 60, 60])
        self.assertEqual(policy.record("worker", False, 9), 5)
        self.assertEqual(policy.record("room", True, 10), 5)
        policy.prune(3610)
        self.assertIn("room", policy.history)
        self.assertNotIn("worker", policy.history)
        policy.reset("room")
        self.assertEqual(policy.record("room", False, 3611), 5)

    def test_terminal_quiet_polls_and_immediate_event_refresh(self):
        output = io.StringIO()
        stop = Mock()
        stop.wait.side_effect = [False] * 4 + [True]
        with patch.object(talos_activity.time, "monotonic", side_effect=[0,2,4,6,8]), \
             patch.object(talos_activity, "snapshot", side_effect=[["idle"],["idle"],["reviewing"]]) as snapshot, \
             patch.object(talos_activity, "events", side_effect=[[],[],[],[],[(1,8,"Respan","Read source")]]) as events:
            talos_activity.display(Path("."), stop=stop, output=output)
        self.assertEqual(events.call_count,5)
        self.assertEqual(snapshot.call_count,3)
        self.assertIn("reviewing",output.getvalue())
        self.assertIn("Read source",output.getvalue())

    def test_reconnect_full_jitter_cap_and_stability(self):
        with patch("talos_core.recovery.random.random",return_value=.25):
            self.assertAlmostEqual(reconnect_delay(-3),.075)
            self.assertEqual(reconnect_delay(5000),3.75)
        self.assertEqual(reconnect_delay(2,base=2,cap=6,jitter=False),6)
        self.assertFalse(stable_connection(None,10))
        self.assertFalse(stable_connection(10,14.999))
        self.assertTrue(stable_connection(10,15))

    def test_supervisor_only_resets_after_sustained_readiness(self):
        model = run_talos.ProjectModel("fixture",12345,True)
        attempts = []
        starts = iter((None, 99, 95))
        def disconnect():
            model.ready_since = next(starts)
            raise RuntimeError("fixture disconnect")
        def wait(_):
            if len(attempts) == 3:
                model.stopping.set()
        with patch.object(model,"serve",disconnect), patch.object(model.stopping,"wait",wait), \
             patch.object(run_talos.time,"monotonic",return_value=100), patch.object(run_talos.talos_activity,"record"), \
             patch.object(run_talos,"reconnect_delay",side_effect=lambda a,**kw: attempts.append(a) or 2):
            model.run()
        self.assertEqual(attempts,[0,1,0])

    def test_archiving_checks_every_digest_and_preserves_exact_recoverable_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root/"state"
            state.mkdir()
            files = []
            ports = []
            for tree in ("OpenClaw","Hermes"):
                doc = state / (tree.lower()+"-src") / "sample.ts"
                doc.parent.mkdir()
                doc.write_text(tree)
                files.append(doc)
                ports.append(Port(tree,"sample.ts",hashlib.sha256(tree.encode()).hexdigest(),"run_talos.py","Fixture"))
            files[1].write_text("locally edited")
            with self.assertRaises(ValueError):
                retire(root,state,ports)
            self.assertTrue(files[0].exists())
            files[1].write_text("Hermes")
            retire(root,state,ports)
            retire(root,state,ports)
            for file, port in zip(files,ports):
                self.assertFalse(file.exists())
                backup = state/".migrated-upstream"/port.project/port.sha/port.path
                self.assertEqual(backup.read_text(),port.project)
            rows = [json.loads(line) for line in (state/"feature-requests.jsonl").read_text().splitlines()]
            self.assertEqual([r["status"] for r in rows],["proposed","done"]*2)

            publisher = VaultPublisher(state, root)
            publisher.poll()
            hub = state/"Talos migrations.md"
            self.assertIn("[[Talos migrations|", (state/"Talos live.md").read_text())
            for line in hub.read_text().splitlines():
                if line.startswith("- [["):
                    relative = line[4:].split("|",1)[0]
                    self.assertTrue((state/(relative+".md")).is_file())

    def test_root_vault_dashboard_follows_events_without_rewriting_user_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/"state"
            scheduling = Path(tmp)/"talos_core/scheduling"
            scheduling.mkdir(parents=True)
            (scheduling/"clock.ts").write_text("export const value = 1;")
            (scheduling/"timer.ts").write_text('import { value } from "./clock.ts";')
            publisher = VaultPublisher(state,Path(tmp))
            publisher.poll()
            note = state/"Talos live.md"
            self.assertIn("[[Talos Memory/Talos maps/Talos memory map|",note.read_text())
            code = publisher.folder/"Code/talos_core scheduling timer.ts source.md"
            self.assertIn("talos_core scheduling clock.ts source",code.read_text())
            talos_activity.record(state,"Respan","Read upstream file")
            publisher.poll()
            self.assertIn("Read upstream file",note.read_text())
            previous = note.stat().st_mtime_ns
            publisher.poll()
            self.assertEqual(previous,note.stat().st_mtime_ns)
            note.write_text("my existing note")
            talos_activity.record(state,"Model","Retry scheduled")
            publisher.poll()
            self.assertEqual(note.read_text(),"my existing note")

import json
import tempfile
import unittest
from pathlib import Path

from talos_core.self_improvement import feature_queue
from talos_core.memory import knowledge_map
from talos_core.runtime.maintenance import queue_summary
from talos_core.self_improvement.section_patches import proposals


class DispositionTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.queue = self.state / feature_queue.FILE
        self.items = [dict(status="proposed", id=f"{i:016x}", source="fixture.py", upstream="Hermes",
                           origin="OpenClaude-section-review", feature=f"Feature {i}", acceptance="Replay")
                      for i in range(5)]
        self.queue.write_text("".join(json.dumps(item)+"\n" for item in self.items))

    def act(self, **args):
        return feature_queue.review(self.state, "fixture", **args)

    def test_decline_releases_slot_without_becoming_a_verified_change(self):
        item = self.items[0]
        self.assertIn(item["id"], proposals(self.state,"OpenClaude","Hermes"))
        for _ in range(2):
            self.assertEqual(self.act(decline=(item["id"],"  Already implemented.  ")),"decline recorded")
        self.assertEqual(len(self.queue.read_text().splitlines()),6)
        self.assertNotIn(item["id"],self.act(pending=True))
        self.assertNotIn(item["id"],proposals(self.state,"OpenClaude","Hermes"))
        self.assertEqual(queue_summary(self.queue),["4 requests pending · 0 completed · 1 declined"])
        self.assertEqual(self.act(infer=lambda *a,**k:json.dumps({"feature":"Fresh proposal","acceptance":"Replay this behavior"})),
                         "proposal queued")
        self.assertEqual(queue_summary(self.queue),["5 requests pending · 0 completed · 1 declined"])
        folder = self.state/"Talos Memory/Talos maps"
        folder.mkdir(parents=True)
        knowledge_map.publish_work(self.state,folder)
        note = (folder/"Changes/Feature 0 00000000.md").read_text()
        self.assertIn("# Declined proposal",note)
        self.assertIn("Already implemented.",note)
        self.assertFalse((self.state/"Talos migrations.md").exists())

    def test_first_disposition_wins_replays_conflicts_and_stale_events(self):
        first, second = (p["id"] for p in self.items[:2])
        self.act(decline=(first,"Duplicate behavior"))
        self.act(complete=(second,"Passed replay"))
        with self.assertRaisesRegex(ValueError,"already declined"):
            self.act(complete=(first,"This was never run"))
        with self.assertRaisesRegex(ValueError,"already done"):
            self.act(decline=(second,"Late rejection"))
        with self.assertRaises(ValueError):
            self.act(decline=(self.items[2]["id"],"  "))
        with self.queue.open("a") as stream:
            feature_queue.append(stream,self.items[0])
            feature_queue.append(stream,dict(status="done",id=first,verification="stale worker"))
        self.assertEqual(queue_summary(self.queue),["3 requests pending · 1 completed · 1 declined",
                                                    "Last verified change · Passed replay"])
        self.assertNotIn(first,proposals(self.state,"OpenClaude","Hermes"))
        with self.queue.open("r+") as stream:
            _, terminal = feature_queue.queue_entries(stream)
        self.assertEqual(terminal,{first,second})

    def test_large_module_excerpt_prioritizes_called_helpers_and_keeps_a_limit(self):
        source = self.state/"index.py"
        source.write_text('"""'+'x'*12000+'"""\n\ndef read():\n    return 3\n\ndef sync():\n    return read()\n')
        selected = feature_queue.review_excerpt(source,120)
        self.assertTrue(selected.startswith("def sync():"))
        self.assertIn("return read()",selected)
        self.assertIn("def read():",selected)
        self.assertLessEqual(len(selected),120)

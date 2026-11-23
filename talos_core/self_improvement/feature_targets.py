"""Fixed source-only review input and queue filename."""

import json


TARGETS = ("talos_core/memory/personal_index.py", "talos_core/mac/mac_context.py", "mac/VisitorMemory.swift", "talos_core/room/room_dialogue.py",
           "talos_core/memory/commitment_memory.py", "mac/VoiceEndpoint.swift", "talos_core/self_improvement/idle_lab.py", "talos_core/room/room_work.py")
FILE = "feature-requests.jsonl"


def change_events(stream, *, repair=False):
    """Read finished JSONL records. A writer holding the queue lock may repair a torn tail."""
    stream.seek(0)
    while True:
        start = stream.tell()
        line = stream.readline()
        if not line:
            return
        if not line.endswith("\n"):
            if repair:
                stream.seek(start)
                stream.truncate()
            return
        yield json.loads(line)

def proposal_ledger(stream, *, repair=False):
    """Fold the append-only queue. The first terminal disposition permanently closes an ID."""
    proposed, outcomes = {}, {}
    for event in change_events(stream, repair=repair):
        key, status = event["id"], event["status"]
        if status == "proposed" and key not in outcomes:
            proposed[key] = event
        elif status in ("done", "declined"):
            outcomes.setdefault(key, event)
    return proposed, outcomes

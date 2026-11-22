"""Fixed source-only review input and queue filename."""

import json


TARGETS = ("personal_index.py", "mac_context.py", "mac/VisitorMemory.swift", "room_dialogue.py",
           "commitment_memory.py", "mac/VoiceEndpoint.swift", "idle_lab.py", "room_work.py")
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

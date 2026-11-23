"""Measure synthetic turn-to-WAV service latency with and without commitment indexing."""

import json
import math
from pathlib import Path
from statistics import median
import tempfile
import threading
import time
from contextlib import closing

from talos_core.memory.agent_memory import open_memory
from talos_core.memory.commitment_memory import refresh_one, select
from talos_core.room.room_service import Room
from talos_core.room.room_voice import DEFAULT_VOICE, KokoroVoice


def main():
    voice = KokoroVoice(
        Path(__file__).resolve().parents[1] / "models" / "kokoro-room", DEFAULT_VOICE
    )
    with tempfile.TemporaryDirectory(prefix="talos-idle-bench-") as temp:
        root = Path(temp)
        room = Room(
            root / "test.db",
            "fixture",
            voice=voice,
            online=False,
            model_state=lambda: "warming",
        )
        with closing(open_memory(room.db_path)) as db:
            for i in range(20):
                path = root / f"thread-{i}.csv"
                path.write_text(
                    "date,sender,text\n"
                    + "".join(
                        f"2026-11-18T12:00:00+00:00,James,I will send sample {n} on Friday.\n"
                        for n in range(100)
                    )
                )
                select(db, path, "imessage", "James", "Test", "Maya")
        times = {False: [], True: []}
        for active in (False, True, True, False):
            stop = threading.Event()
            ready = threading.Event()

            def index():
                with closing(open_memory(room.db_path)) as db:
                    with db:
                        db.execute("UPDATE context_sources SET checked=0,digest=''")
                    ready.set()
                    while not stop.is_set() and refresh_one(db, time.time()):
                        stop.wait(0.05)

            worker = threading.Thread(target=index)
            if active:
                worker.start()
                ready.wait()
            try:
                for n in range(6):
                    start = time.perf_counter()
                    _, turn = room.route(
                        "POST",
                        "/v1/turn",
                        {
                            "source": "voice",
                            "text": f"Remind me in {n + 1} minutes to check the sample.",
                        },
                    )
                    _, audio = room.route("POST", "/v1/voice", {"text": turn["reply"]})
                    assert audio["wav"]
                    times[active].append(round(1000 * (time.perf_counter() - start)))
            finally:
                stop.set()
                if active:
                    worker.join()
        for active, samples in times.items():
            print(
                json.dumps(
                    {
                        "indexing_started": active,
                        "n": len(samples),
                        "median_ms": median(samples),
                        "p95_ms": sorted(samples)[math.ceil(0.95 * len(samples)) - 1],
                        "samples_ms": samples,
                    }
                )
            )


if __name__ == "__main__":
    main()

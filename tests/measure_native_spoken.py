"""Compare full/streaming voice on controlled model timing and real Kokoro, without live devices."""

import argparse
import json
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from talos_core.cognition.spoken_stream import SpokenStream
from talos_core.room.room_service import Handler, Room
from talos_core.room.room_voice import DEFAULT_VOICE, KokoroVoice


def benchmark(models, samples):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="talos-speech-benchmark-") as tmp, ExitStack() as context:
        temp = Path(tmp)
        binary = temp / "measure"
        subprocess.run(["swiftc", "-swift-version", "5", "-D", "TALOS_CONTROLLER_REPLAY", "-parse-as-library",
                        "-module-cache-path", str(temp / "swift-cache"), *sorted((root / "mac").glob("*.swift")),
                        root / "tests/MeasureSpokenPCM.swift", "-o", binary], check=True, timeout=120)
        service = context.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), Handler))
        voice = KokoroVoice(models, DEFAULT_VOICE)
        service.room = Room(temp / "trial.db", "fixture", online=False, model_state=lambda: "ready", voice=voice)
        service.token = "fixture"
        def full(*args, **kwargs):
            time.sleep(0.6)
            return json.dumps({"talos_says": "Spring follows winter. The seasons repeat each year."})
        def chunks(*args, **kwargs):
            time.sleep(0.1)
            yield '["Spring follows winter."'
            time.sleep(0.5)
            yield ',"The seasons repeat each year."]'
        context.enter_context(patch("talos_core.room.room_service.model_text", side_effect=full))
        context.enter_context(patch("talos_core.room.room_service.check_ollama_model"))
        context.enter_context(patch.object(SpokenStream, "tokens", chunks))
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            subprocess.run([binary, str(service.server_port), service.token, temp, str(samples)], check=True,
                           timeout=15 + samples * 20)
        finally:
            service.shutdown()
            worker.join()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--models", type=Path, required=True, help="Existing local Kokoro speech assets")
    cli.add_argument("--samples", type=int, default=5)
    args = cli.parse_args()
    if args.samples < 1:
        cli.error("Use a positive sample count.")
    benchmark(args.models, args.samples)

"""Run from the project root with PYTHONPATH=. against installed local models."""

import argparse
import asyncio
import json
import statistics
import time
import wave
from pathlib import Path

from talos_core.runtime.local_runtime import request_ollama_json
from talos_core.cognition.turn_timing import classify

# All captions are synthetic. Test at a silence long enough for the baseline timer to fire.
CAPTIONS = [
    ("Could you put a reminder on my calendar for", True),
    ("Tomorrow morning I need to", True),
    ("Please pull up our notes about the", True),
    ("The change I want to make is", True),
    ("And when Casey gets here", True),
    ("Can you tell whether I'm speaking?", False),
    ("What do I still owe the design team?", False),
    ("Save the revised estimate.", False),
    ("Show the agenda for the review.", False),
    ("Make that eleven minutes.", False),
]


def compare(model):
    times, missed, false_holds = [], [], []
    unavailable = 0
    for caption, unfinished in CAPTIONS:
        start = time.monotonic()
        try:
            hold = classify(caption, model)["hold"]
        except RuntimeError:
            unavailable += 1
            hold = False  # same fallback as the room
        times.append(round(1000 * (time.monotonic() - start)))
        if unfinished and not hold:
            missed.append(caption)
        elif not unfinished and hold:
            false_holds.append(caption)
    return {
        "model": model,
        "fixtures": len(times),
        "missed_continuations": missed,
        "false_holds": false_holds,
        "unavailable": unavailable,
        "median_probe_ms": statistics.median(times),
        "max_probe_ms": max(times),
    }


async def acoustic_replay(hold_paths, end_paths):
    from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )

    analyzer = LocalSmartTurnAnalyzerV3()
    analyzer.set_sample_rate(16000)
    try:
        for expected_hold, paths in ((True, hold_paths), (False, end_paths)):
            for path in paths:
                with wave.open(str(path), "rb") as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                        raise ValueError(f"{path.name} must be mono 16 kHz PCM16.")
                    if not 0 < wav.getnframes() <= 8 * 16000:
                        raise ValueError(f"{path.name} must contain up to eight seconds ending at the pause.")
                    pcm = wav.readframes(wav.getnframes())
                analyzer.clear()
                analyzer.append_audio(pcm, is_speech=True)
                start = time.monotonic()
                state, _ = await analyzer.analyze_end_of_turn()
                hold = state == EndOfTurnState.INCOMPLETE
                print(
                    json.dumps(
                        {
                            "file": path.name,
                            "expected_hold": expected_hold,
                            "hold": hold,
                            "correct": hold == expected_hold,
                            "inference_ms": round(1000 * (time.monotonic() - start)),
                        }
                    )
                )
    finally:
        await analyzer.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*")
    parser.add_argument("--audio-hold", type=Path, action="append", default=[])
    parser.add_argument("--audio-end", type=Path, action="append", default=[])
    args = parser.parse_args()
    names = args.models
    if args.audio_hold or args.audio_end:
        try:
            asyncio.run(acoustic_replay(args.audio_hold, args.audio_end))
        except ImportError as error:
            parser.exit(2, f"Pipecat's local Smart Turn runtime is required for audio replay ({error}).\n")
    if not names:
        if not args.audio_hold and not args.audio_end:
            parser.error("Supply a local model name or labelled audio fixture.")
        raise SystemExit(0)
    installed = {m["name"]: m.get("size") for m in request_ollama_json("GET", "/api/tags")["models"]}
    print(
        json.dumps({"silence_timer_missed": sum(hold for _, hold in CAPTIONS), "silence_timer_false_holds": 0})
    )
    for name in names:
        print(
            json.dumps({**compare(name), "disk_bytes": installed[name]})
            if name in installed
            else json.dumps({"model": name, "status": "not installed"}),
            flush=True,
        )

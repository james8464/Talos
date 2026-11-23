"""Compare cold, warm and mixed Ollama context sizes on an idle Mac.

Run with PYTHONPATH=. python3 tests/measure_voice_context.py --models models/ollama.
Each JSON line is a measured /api/chat call. Wall time includes the complete
model response. The classifier's normal 1.2-second deadline is reported separately.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

import agent
from talos_core.self_improvement.idle_lab import power_and_idle
from talos_core.runtime.local_runtime import check_ollama_model, request_ollama_json
from talos_core.room.room_service import Room
from talos_core.cognition.turn_timing import classify


def fixture_requests(model, directory):
    requests = []

    def capture(method, path, *, payload, timeout):
        requests.append(payload)
        response = {"hold": True} if "hold" in payload["format"]["properties"] else {"talos_says": "Yellow."}
        return {"done": True, "message": {"content": json.dumps(response)}}

    with patch("talos_core.cognition.turn_timing.check_ollama_model"), patch("talos_core.cognition.turn_timing.request_ollama_json", capture):
        classify("Please remind me to bring the", model)
        classify("Is the museum open on Sundays?", model)
    with patch("talos_core.room.room_service.check_ollama_model"), patch.object(agent, "request_ollama_json", capture):
        room = Room(directory / "fixture.db", model, apply=False, speak=False, online=False)
        status, result = room.route("POST", "/v1/voice/facing", {"text": "What color is a ripe banana?"})
        if status != 200 or result.get("reply") != "Yellow." or len(requests) != 3:
            raise RuntimeError("Could not capture the ordinary spoken-reply request.")
    return list(zip(("unfinished", "finished", "voice"), requests))


def resident_memory(server_pid):
    # Sample only our server and its direct model runners. Darwin's proc_taskinfo
    # starts with pti_virtual_size and pti_resident_size, both 64-bit byte counts.
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    children = (ctypes.c_int * 64)()
    if libproc.proc_listchildpids(server_pid, children, ctypes.sizeof(children)) < 0:
        raise OSError(ctypes.get_errno(), "Could not identify benchmark model runners")
    pids = sorted(pid for pid in children if pid > 0)
    rss = 0
    for pid in [server_pid, *pids]:
        task_info = (ctypes.c_uint64 * 64)()
        if libproc.proc_pidinfo(pid, 4, 0, task_info, ctypes.sizeof(task_info)) < 16:
            return pids, None
        rss += task_info[1]
    return pids, round(rss / (1024 * 1024), 2)


def run_samples(server, model, cases, repetitions):
    for policy, classifier_ctx, voice_ctx in (
        ("baseline", 8192, 8192), ("small", 2048, 2048), ("mixed", 2048, 8192)
    ):
        if request_ollama_json("GET", "/api/ps").get("models"):
            request_ollama_json("POST", "/api/generate", payload={"model": model, "keep_alive": 0}, timeout=20)
            for _ in range(200):
                if not request_ollama_json("GET", "/api/ps").get("models"):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("Could not establish a cold start for the next policy.")
        for index in range(repetitions + 1):
            for name, original in cases:
                ctx = voice_ctx if name == "voice" else classifier_ctx
                payload = {**original, "options": {**original["options"], "num_ctx": ctx}}
                start = time.monotonic()
                result = request_ollama_json("POST", "/api/chat", payload=payload, timeout=90)
                elapsed = round((time.monotonic() - start) * 1000, 2)
                try:
                    reply = json.loads(result["message"]["content"])
                    correct = ("yellow" in reply["talos_says"].lower() if name == "voice"
                               else reply["hold"] is (name == "unfinished"))
                except (KeyError, TypeError, ValueError):
                    correct = False
                loaded = request_ollama_json("GET", "/api/ps")["models"]
                runners, rss = resident_memory(server.pid)
                print(json.dumps({"policy": policy, "iteration": index, "case": name, "requested_ctx": ctx,
                                  "loaded_ctx": loaded[0].get("context_length") if loaded else None,
                                  "runners": runners, "rss_mib": rss, "wall_ms": elapsed,
                                  "load_ms": round(result["load_duration"] / 1e6, 2),
                                  "output_tokens": result.get("eval_count"), "fixture_correct": correct,
                                  "within_classifier_deadline": None if name == "voice" else elapsed <= 1200}),
                      flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path(__file__).resolve().parents[1] / "models/ollama")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--wait", type=int, default=0, help="Maximum seconds to wait for an idle window.")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 100 or args.wait < 0:
        parser.error("Use 1–100 repetitions and a nonnegative idle wait.")
    deadline = time.monotonic() + args.wait
    while not power_and_idle():
        if time.monotonic() >= deadline:
            raise SystemExit("Idle window unavailable; no server started.")
        time.sleep(1)
    with tempfile.TemporaryDirectory(prefix="talos-voice-context-") as tmp, socket.socket() as sock:
        cases = fixture_requests(args.model, Path(tmp))
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(args.models.resolve()),
               "OLLAMA_NO_CLOUD": "1", "OLLAMA_NOPRUNE": "1", "OLLAMA_NUM_PARALLEL": "1",
               "OLLAMA_MAX_LOADED_MODELS": "1", "LLAMA_ARG_DEVICE": "none", "LLAMA_ARG_N_GPU_LAYERS": "0"}
        server = subprocess.Popen(["ollama", "serve"], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        stopped = threading.Event()
        interrupted = threading.Event()

        def guard():
            while not stopped.wait(.5):
                if not power_and_idle():
                    interrupted.set()
                    if server.poll() is None:
                        os.killpg(server.pid, signal.SIGTERM)
                    return

        monitor = threading.Thread(target=guard, daemon=True)
        monitor.start()
        try:
            with patch.dict(os.environ, TALOS_OLLAMA_PORT=str(port)):
                for _ in range(40):
                    if server.poll() is not None:
                        raise RuntimeError("Isolated server exited.")
                    try:
                        request_ollama_json("GET", "/api/version")
                        break
                    except RuntimeError:
                        time.sleep(.2)
                check_ollama_model(args.model)
                run_samples(server, args.model, cases, args.repetitions)
        finally:
            stopped.set()
            monitor.join()
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()
            if interrupted.is_set():
                raise SystemExit("Idle window closed; isolated server stopped.")


if __name__ == "__main__":
    main()

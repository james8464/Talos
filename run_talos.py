"""Build and start the local Talos room stack with a project-owned model store."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent_memory import STATE_PATH
from local_runtime import check_ollama_model, request_ollama_json
from room_service import DEFAULT_PORT, Room, run
from room_voice import DEFAULT_VOICE, KokoroVoice, prepare_assets, voice_python

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
BUNDLE = STATE / "Talos.app"
TOKEN_PATH = STATE / "room.token"
MODEL_DIR = ROOT / "models" / "ollama"
SOURCES = ROOT / "mac"


def build_app() -> Path:
    source = SOURCES / "Talos.swift"
    plist = SOURCES / "Info.plist"
    executable = BUNDLE / "Contents" / "MacOS" / "Talos"
    if executable.is_file() and executable.stat().st_mtime >= max(
        source.stat().st_mtime, plist.stat().st_mtime
    ):
        return BUNDLE
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="talos-build-", dir=STATE) as directory:
        app = Path(directory) / "Talos.app"
        contents = app / "Contents"
        macos = contents / "MacOS"
        macos.mkdir(parents=True)
        shutil.copyfile(plist, contents / "Info.plist")
        subprocess.run(
            ["swiftc", "-swift-version", "5", "-O", "-parse-as-library",
             "-module-cache-path", str(STATE / "swift-cache"),
             str(source), "-o", str(macos / "Talos")], check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        if BUNDLE.exists():
            shutil.rmtree(BUNDLE)
        app.rename(BUNDLE)
    return BUNDLE


def check_bind(port: int) -> None:
    with socket.socket() as candidate:
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"Cannot open Talos loopback port {port}: {error}") from error


def prepare_model(process: subprocess.Popen, ollama: str, model: str, env: dict, offline: bool) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("The project Ollama server exited during startup.")
        try:
            request_ollama_json("GET", "/api/tags", timeout=1)
        except RuntimeError:
            time.sleep(0.25)
        else:
            break
    else:
        raise RuntimeError("The project Ollama server did not become reachable.")
    try:
        check_ollama_model(model)
    except RuntimeError:
        if offline:
            raise
        if "cloud" in model.lower():
            raise RuntimeError("Select a locally runnable model tag.")
        print(f"Preparing local model {model}. The first download requires internet access.", flush=True)
        subprocess.run([ollama, "pull", model], env=env, check=True)
        check_ollama_model(model)


def prepare_voice(voice_name: str, *, offline: bool) -> KokoroVoice | None:
    current_environment = Path(sys.prefix).resolve() == (STATE / "kokoro-env").resolve()
    try:
        if not current_environment:
            interpreter = voice_python(STATE, offline=offline)
            if interpreter is None:
                raise RuntimeError("Use uv for initial Kokoro setup, or select --system-voice.")
            os.execv(interpreter, [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]])
        model_dir = ROOT / "models" / "kokoro-room"
        prepare_assets(model_dir, offline=offline)
        voice = KokoroVoice(model_dir, voice_name)
    except Exception as error:
        print(f"Kokoro unavailable: {error}. Using the macOS voice.", file=sys.stderr, flush=True)
        return None
    print(f"Local Kokoro voice ready: {voice.name}", flush=True)
    return voice


def warm_model(model: str) -> None:
    print(f"Warming local model {model}.", flush=True)
    try:
        request_ollama_json("POST", "/api/generate", timeout=90, payload={
            "model": model, "prompt": "", "stream": False, "keep_alive": -1,
            "options": {"num_ctx": 8192},
        })
    except RuntimeError as error:
        print(f"Model warm-up did not finish: {error}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and launch the Talos room app and endpoint.")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="English Kokoro voice ID.")
    parser.add_argument("--system-voice", action="store_true", help="Use the macOS voice without Kokoro.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ollama-port", type=int, default=11435)
    parser.add_argument("--offline", action="store_true", help="Require an already downloaded model.")
    parser.add_argument("--shadow", action="store_true", help="Evaluate actions without writing or speaking them.")
    parser.add_argument("--headless", action="store_true", help="Run the endpoint without opening the app.")
    parser.add_argument("--build-only", action="store_true", help="Build the native app without starting models.")
    args = parser.parse_args()
    process = None
    try:
        if args.build_only:
            print(build_app())
            return 0
        if args.port == args.ollama_port or not all(
            1024 <= port <= 65535 for port in (args.port, args.ollama_port)
        ):
            raise ValueError("Choose two different ports between 1024 and 65535.")
        check_bind(args.port)
        check_bind(args.ollama_port)
        app = None if args.headless else build_app()
        ollama = shutil.which("ollama")
        if not ollama:
            raise RuntimeError("Install Ollama to run the local model. The native app has already been built.")
        voice = None if args.system_voice else prepare_voice(args.voice, offline=args.offline)
        MODEL_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["TALOS_OLLAMA_PORT"] = str(args.ollama_port)
        env = {**os.environ, "OLLAMA_NO_CLOUD": "1", "OLLAMA_MODELS": str(MODEL_DIR),
               "OLLAMA_HOST": f"127.0.0.1:{args.ollama_port}"}
        process = subprocess.Popen([ollama, "serve"], env=env)
        prepare_model(process, ollama, args.model, env, args.offline)
        warm_model(args.model)

        def launch_app() -> None:
            if app:
                subprocess.run(["/usr/bin/open", "-n", str(app), "--args",
                                "--token-file", str(TOKEN_PATH), "--port", str(args.port)], check=True)

        run(args.port, Room(STATE_PATH, args.model, apply=not args.shadow, voice=voice, online=not args.offline),
            TOKEN_PATH, ready=launch_app)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Talos startup: {error}", file=sys.stderr)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

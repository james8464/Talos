"""Build and start the local Talos room stack with a project-owned model store."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
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


def build_app(*, trial_dir: Path | None = None) -> Path:
    state = trial_dir or STATE
    bundle = state / "Talos.app" if trial_dir else BUNDLE
    sources = sorted(SOURCES.glob("*.swift"))
    plist = SOURCES / "Info.plist"
    executable = bundle / "Contents" / "MacOS" / "Talos"
    if executable.is_file() and executable.stat().st_mtime >= max(
        plist.stat().st_mtime, *(source.stat().st_mtime for source in sources)
    ):
        return bundle
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="talos-build-", dir=state) as directory:
        app = Path(directory) / "Talos.app"
        contents = app / "Contents"
        macos = contents / "MacOS"
        macos.mkdir(parents=True)
        shutil.copyfile(plist, contents / "Info.plist")
        subprocess.run(
            ["swiftc", "-swift-version", "5", "-O", "-parse-as-library",
             "-module-cache-path", str(state / "swift-cache"),
             *map(str, sources), "-o", str(macos / "Talos")], check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        if bundle.exists():
            shutil.rmtree(bundle)
        app.rename(bundle)
    return bundle


def check_bind(port: int) -> None:
    with socket.socket() as candidate:
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"Cannot open Talos loopback port {port}: {error}") from error


class ProjectModel:
    """Own only this launcher's processes. The room stays available during model recovery."""

    def __init__(self, model, port, offline):
        self.model, self.port, self.offline = model, port, offline
        self.state = "starting"
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.children = set()
        self.worker = threading.Thread(target=self.run, name="talos-model", daemon=True)

    @contextmanager
    def child(self, args, env):
        with self.lock:
            if self.stopping.is_set():
                raise RuntimeError("Model shutdown requested.")
            process = subprocess.Popen(args, env=env)
            self.children.add(process)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            with self.lock:
                self.children.discard(process)

    def serve(self):
        ollama = shutil.which("ollama")
        if not ollama:
            raise RuntimeError("Install Ollama for open-ended conversation.")
        check_bind(self.port)
        env = {**os.environ, "OLLAMA_NO_CLOUD": "1", "OLLAMA_MODELS": str(MODEL_DIR),
               "OLLAMA_HOST": f"127.0.0.1:{self.port}"}
        with self.child([ollama, "serve"], env) as server:
            for _ in range(60):
                if server.poll() is not None or self.stopping.is_set():
                    raise RuntimeError("Project Ollama stopped during startup.")
                try:
                    request_ollama_json("GET", "/api/tags", timeout=1)
                    break
                except RuntimeError:
                    self.stopping.wait(0.25)
            else:
                raise RuntimeError("Project Ollama did not become reachable.")
            try:
                check_ollama_model(self.model)
            except RuntimeError:
                if self.offline or "cloud" in self.model.lower():
                    raise
                self.state = "preparing model"
                with self.child([ollama, "pull", self.model], env) as pull:
                    if pull.wait() != 0:
                        raise RuntimeError("The local model download failed.")
                check_ollama_model(self.model)
            self.state = "warming up"
            warm_model(self.model)
            self.state = "ready"
            server.wait()
            if not self.stopping.is_set():
                raise RuntimeError("Project Ollama exited.")

    def run(self):
        retry = 2
        while not self.stopping.is_set():
            try:
                self.state = "starting"
                self.serve()
            except (OSError, RuntimeError) as error:
                if self.stopping.is_set():
                    break
                if self.state == "ready":
                    retry = 2
                self.state = "unavailable"
                print(f"Talos model: {error} Retrying in {retry}s.", file=sys.stderr, flush=True)
                self.stopping.wait(retry)
                retry = min(60, retry * 2)

    def close(self):
        self.stopping.set()
        with self.lock:
            children = list(self.children)
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.worker.join(timeout=8)


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
    request_ollama_json("POST", "/api/generate", timeout=90, payload={
        "model": model, "prompt": "", "stream": False, "keep_alive": -1,
        "options": {"num_ctx": 8192},
    })


def run_trial(args) -> None:
    """Reuse the room's installed models but isolate the app, token and all receipts."""
    port = args.port if args.port != DEFAULT_PORT else 8876
    check_bind(port)
    os.environ["TALOS_OLLAMA_PORT"] = str(args.ollama_port)
    check_ollama_model(args.model)
    voice = None if args.system_voice else prepare_voice(args.voice, offline=True)
    with tempfile.TemporaryDirectory(prefix="talos-room-trial-") as directory:
        trial = Path(directory)
        bundle = None if args.headless else build_app(trial_dir=trial)
        token = trial / "room.token"
        app_process = None

        def launch() -> None:
            nonlocal app_process
            if bundle is None:
                return
            app_process = subprocess.Popen([str(bundle / "Contents/MacOS/Talos"), "--room-trial",
                                            "--token-file", str(token), "--port", str(port)])

        try:
            run(port, Room(trial / "trial.sqlite", args.model, voice=voice, online=False),
                token, ready=launch)
        finally:
            if app_process and app_process.poll() is None:
                app_process.terminate()
                app_process.wait(timeout=5)


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
    parser.add_argument("--room-trial", action="store_true", help="Open an offline practice room with temporary data and sensors initially off.")
    args = parser.parse_args()
    backend = None
    try:
        if args.room_trial:
            run_trial(args)
            return 0
        if args.build_only:
            print(build_app())
            return 0
        if args.port == args.ollama_port or not all(
            1024 <= port <= 65535 for port in (args.port, args.ollama_port)
        ):
            raise ValueError("Choose two different ports between 1024 and 65535.")
        check_bind(args.port)
        app = None if args.headless else build_app()
        voice = None if args.system_voice else prepare_voice(args.voice, offline=args.offline)
        MODEL_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["TALOS_OLLAMA_PORT"] = str(args.ollama_port)
        backend = ProjectModel(args.model, args.ollama_port, args.offline)
        backend.worker.start()

        def launch_app() -> None:
            if app:
                subprocess.run(["/usr/bin/open", "-n", str(app), "--args",
                                "--token-file", str(TOKEN_PATH), "--port", str(args.port)], check=True)

        run(args.port, Room(STATE_PATH, args.model, apply=not args.shadow, voice=voice, online=not args.offline,
                            model_state=lambda: backend.state, app_launch_enabled=app is not None),
            TOKEN_PATH, ready=launch_app)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Talos startup: {error}", file=sys.stderr)
        return 1
    finally:
        if backend is not None:
            backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

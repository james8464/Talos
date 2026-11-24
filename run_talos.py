"""Build and start the local Talos room stack with a project-owned model store."""

import argparse
import errno
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.client import HTTPException
from pathlib import Path

from agent import model_text
from talos_core.memory.agent_memory import STATE_PATH
from talos_core.recovery import reconnect_delay, stable_connection
from talos_core.room.room_service import DEFAULT_PORT, Room, run
from talos_core.room.room_voice import DEFAULT_VOICE, KokoroVoice, prepare_assets, voice_python
from talos_core.runtime import talos_activity
from talos_core.runtime.local_runtime import check_ollama_model, request_ollama_json
from talos_core.self_improvement.worker_supervision import WorkerSupervisor

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
        # The room server reuses a recently closed socket; the preflight must
        # allow the same TIME_WAIT reuse or a restart looks like a conflict.
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"Cannot open Talos loopback port {port}: {error}") from error


def muted_mode(mute: bool, unmute: bool, environ: dict | None = None) -> bool:
    """Talos is silent unless the operator explicitly opts into speech."""
    environment = os.environ if environ is None else environ
    return bool(mute or environment.get("TALOS_MUTE") == "1" or not unmute)


class ProjectModel:
    """Own only this launcher's processes. The room stays available during model recovery."""

    def __init__(self, model, port, offline):
        self.model, self.port, self.offline = model, port, offline
        self.state = "starting"
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.children = set()
        self.ready_server = None
        self.ready_since = None
        self.voice_verified = False
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

    def generate_voice(self, messages, *, operation=None, **options):
        with self.lock:
            server = self.ready_server
            verified = self.voice_verified
        if server is None or server.poll() is not None or options.get("model") != self.model:
            raise RuntimeError("The supervised local voice model is unavailable.")
        try:
            if not verified:
                check_ollama_model(self.model)
                with self.lock:
                    if self.ready_server is not server:
                        raise RuntimeError("The supervised model restarted.")
                    self.voice_verified = True
            return (operation or model_text)(messages, **options)
        except (OSError, RuntimeError, ValueError, HTTPException):
            with self.lock:
                if self.ready_server is server:
                    self.voice_verified = False
            raise

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
                        raise RuntimeError("The local model download failed.") from None
                check_ollama_model(self.model)
            self.state = "warming up"
            warm_model(self.model)
            with self.lock:
                self.ready_server = server
                self.voice_verified = True
                self.ready_since = time.monotonic()
                self.state = "ready"
            try:
                server.wait()
            finally:
                with self.lock:
                    self.ready_server = None
                    self.voice_verified = False
            if not self.stopping.is_set():
                raise RuntimeError("Project Ollama exited.")

    def run(self):
        attempt = 0
        while not self.stopping.is_set():
            try:
                self.ready_since = None
                self.state = "starting"
                self.serve()
            except (OSError, RuntimeError) as error:
                if self.stopping.is_set():
                    break
                if stable_connection(self.ready_since, time.monotonic()):
                    attempt = 0
                retry = reconnect_delay(attempt, base=2, cap=60)
                self.state = "unavailable"
                talos_activity.record(STATE, "Model", f"Local model disconnected · attempt {attempt + 1} · retry in {retry:.1f}s")
                print(f"Talos model: {error} Retrying in {retry:.1f}s.", file=sys.stderr, flush=True)
                self.stopping.wait(retry)
                attempt += 1

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
            run(port, Room(trial / "trial.sqlite", args.model, speak=not (args.mute or os.environ.get("TALOS_MUTE") == "1"),
                           voice=voice, online=False),
                token, ready=launch)
        finally:
            if app_process and app_process.poll() is None:
                app_process.terminate()
                app_process.wait(timeout=5)


def start_memory_workers(model, *, local_only, children, respan=False, room_owner=False, sections=False, stop=None):
    """Launch background workers as modules so they work from any directory."""
    heartbeat = ["talos_core.self_improvement.overnight_goal", "--heartbeat", "--watch", "--model", model]
    if local_only:
        heartbeat.append("--local-only")
    workers = [
        heartbeat,
        ["talos_core.memory.vault_watch"],
        ["talos_core.runtime.maintenance", "--model", model],
        ["talos_core.self_improvement.self_repair", "--watch"],
        ["talos_core.self_improvement.respan_worker",
         *([] if respan else ["--local-only"]),
         *(["--room-owner"] if room_owner else [])],
    ]
    if sections:
        workers.extend(
            ["talos_core.self_improvement.section_workers", "--engine", engine,
             *(["--room-owner"] if room_owner else [])]
            for engine in ("OpenCode", "OpenClaude")
        )
    for module, *options in workers:
        children.append(subprocess.Popen([sys.executable, "-m", module, *options], cwd=ROOT))
    if stop is not None:
        supervisor = threading.Thread(target=WorkerSupervisor(STATE, children).watch, args=(stop,),
                                      name="Talos worker supervisor", daemon=True)
        supervisor.start()
        return supervisor


def attach_room(port, *, wait=False):
    deadline = time.monotonic() + (30 if wait else 0)
    while True:
        status = talos_activity.probe(port)
        if status.startswith("running") and TOKEN_PATH.is_file():
            subprocess.run(["/usr/bin/open", str(build_app()), "--args", "--token-file", str(TOKEN_PATH),
                            "--port", str(port)], check=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Cannot attach to the room on {port}: {status}. See state/service-error.log.")
        time.sleep(.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and launch the Talos room app and endpoint.")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="English Kokoro voice ID.")
    parser.add_argument("--system-voice", action="store_true", help="Use the macOS voice without Kokoro.")
    parser.add_argument("--mute", action="store_true", help="Disable all speech output and synthesis.")
    parser.add_argument("--unmute", action="store_true",
                        help="Allow speech output. Talos is silent by default.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ollama-port", type=int, default=11435)
    parser.add_argument("--section-reviews", action="store_true", help="Run OpenCode and OpenClaude source-section reviews in the background.")
    parser.add_argument("--respan-reviews", action="store_true", help="Enable scheduled Respan source and anonymized graph review even with offline room turns.")
    parser.add_argument("--offline", action="store_true", help="Require an already downloaded model.")
    parser.add_argument("--attach", action="store_true", help="Open the native app against a running Talos service.")
    parser.add_argument("--memory-only", action="store_true", help="Run memory workers and a live terminal feed without the room server.")
    parser.add_argument("--shadow", action="store_true", help="Evaluate actions without writing or speaking them.")
    parser.add_argument("--headless", action="store_true", help="Run the endpoint without opening the app.")
    parser.add_argument("--build-only", action="store_true", help="Build the native app without starting models.")
    parser.add_argument("--room-trial", action="store_true", help="Open an offline practice room with temporary data and sensors initially off.")
    args = parser.parse_args()
    muted = muted_mode(args.mute, args.unmute)
    if muted:
        os.environ["TALOS_MUTE"] = "1"
    backend = None
    children = []
    activity_stop = threading.Event()
    viewer = None
    supervisor = None
    try:
        if args.attach:
            attach_room(args.port, wait=True)
            return 0
        if args.memory_only:
            if args.shadow or args.build_only or args.room_trial:
                raise ValueError("Memory-only mode runs the live memory workers. Choose one launch mode.")
            os.environ["TALOS_OLLAMA_PORT"] = str(args.ollama_port)
            supervisor = start_memory_workers(args.model, local_only=args.offline, children=children,
                sections=args.section_reviews or not args.offline,
                respan=args.respan_reviews or not args.offline, stop=activity_stop)
            talos_activity.display(STATE, args.port, stop=activity_stop)
            return 0
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
        try:
            check_bind(args.port)
        except RuntimeError as error:
            if not args.headless and getattr(error.__cause__, "errno", None) == errno.EADDRINUSE:
                attach_room(args.port)
                if sys.stdout.isatty():
                    talos_activity.display(STATE, args.port)
                return 0
            raise
        if sys.stdout.isatty():
            print("Starting Talos. Live activity will appear when the room is ready.", flush=True)
        app = None if args.headless else build_app()
        voice = None if muted or args.system_voice else prepare_voice(args.voice, offline=args.offline)
        MODEL_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["TALOS_OLLAMA_PORT"] = str(args.ollama_port)
        backend = ProjectModel(args.model, args.ollama_port, args.offline)
        backend.worker.start()
        if not args.shadow:
            supervisor = start_memory_workers(args.model, local_only=args.offline, children=children,
                sections=args.section_reviews or not args.offline,
                respan=args.respan_reviews or not args.offline, room_owner=True, stop=activity_stop)

        def launch_app() -> None:
            nonlocal viewer
            if sys.stdout.isatty():
                viewer = threading.Thread(target=talos_activity.display, args=(STATE, args.port),
                                          kwargs={"stop": activity_stop}, daemon=True)
                viewer.start()
            if app:
                subprocess.run(["/usr/bin/open", "-n", str(app), "--args",
                                "--token-file", str(TOKEN_PATH), "--port", str(args.port)], check=True)

        run(args.port, Room(STATE_PATH, args.model, apply=not args.shadow, speak=not muted,
                           voice=voice, online=not args.offline,
                            model_state=lambda: backend.state, voice_generate=backend.generate_voice),
            TOKEN_PATH, ready=launch_app)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Talos startup: {error}", file=sys.stderr)
        return 1
    finally:
        activity_stop.set()
        if supervisor is not None:
            supervisor.join()
        if viewer is not None:
            viewer.join(timeout=2)
        for worker in reversed(children):
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
        if backend is not None:
            backend.close()
    return 0


def shutdown_on_signal(signum, frame):
    signal.signal(signum, signal.SIG_IGN)
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown_on_signal)
    raise SystemExit(main())

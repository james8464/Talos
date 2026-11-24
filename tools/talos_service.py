"""Manage Talos's headless, per-user macOS service."""

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "local.talos.room"
LAUNCHCTL = "/bin/launchctl"


def configuration(root=ROOT, *, respan=False, sections=False):
    # Adapted from OpenClaw's launchd-plist.ts. See talos_core/LICENSE-OpenClaw.
    state = root / "state"
    project_python = root / ".venv" / "bin" / "python"
    local_python = state / "kokoro-env" / "bin" / "python"
    # The muted service does not load Kokoro, so prefer the project environment:
    # it carries the native Mac-control dependencies (pyobjc/Quartz).
    python = (project_python if os.access(project_python, os.X_OK)
              else local_python if os.access(local_python, os.X_OK)
              else Path(sys.executable))
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(root / "run_talos.py"),
                             "--headless", "--offline", "--system-voice", "--mute",
                             *(["--respan-reviews"] if respan else []),
                             *(["--section-reviews"] if sections else [])],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                                "TALOS_MUTE": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ExitTimeOut": 30,
        "ProcessType": "Interactive",
        "ThrottleInterval": 10,
        "Umask": 0o077,
        "StandardInPath": "/dev/null",
        "StandardOutPath": str(state / "service.log"),
        "StandardErrorPath": str(state / "service-error.log"),
    }


def launchctl(*args, check=False, capture=False):
    return subprocess.run([LAUNCHCTL, *args], check=check, capture_output=capture)


def install(plist, domain, *, respan=False, sections=False, force=False):
    service = f"{domain}/{LABEL}"
    data = plistlib.dumps(configuration(respan=respan, sections=sections))
    loaded = launchctl("print", service, capture=True).returncode == 0
    changed = not plist.is_file() or plist.read_bytes() != data
    if loaded and not changed and not force:
        return
    if not loaded:
        sys.path.insert(0, str(ROOT))
        from run_talos import check_bind
        from talos_core.room.room_service import DEFAULT_PORT

        try:
            check_bind(DEFAULT_PORT)
        except RuntimeError as error:
            raise RuntimeError(f"Service preflight failed. {error}") from error
    (ROOT / "state").mkdir(mode=0o700, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    if changed:
        with tempfile.NamedTemporaryFile(dir=plist.parent, delete=False) as stream:
            stream.write(data)
        Path(stream.name).replace(plist)
    if loaded:
        launchctl("bootout", service, capture=True)
        wait_for_release()
    for attempt in range(4):
        result = launchctl("bootstrap", domain, str(plist), capture=True)
        if result.returncode == 0:
            return
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"launchctl bootstrap failed with status {result.returncode}.")


def wait_for_release(timeout=10):
    """Wait for the previous room process to release its loopback port."""
    from run_talos import check_bind
    from talos_core.room.room_service import DEFAULT_PORT

    deadline = time.monotonic() + timeout
    while True:
        try:
            check_bind(DEFAULT_PORT)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def restart(plist, domain, *, respan=False, sections=False):
    """Stop the current room, wait for its port, then start the installed version."""
    service = f"{domain}/{LABEL}"
    if launchctl("print", service, capture=True).returncode == 0:
        launchctl("bootout", service, capture=True)
        wait_for_release()
    for attempt in range(5):
        try:
            install(plist, domain, respan=respan, sections=sections, force=True)
            return
        except RuntimeError as error:
            if "preflight" not in str(error) or attempt == 4:
                raise
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("print", "install", "restart", "status", "stop"))
    parser.add_argument("--section-reviews", action="store_true", help="Start OpenCode and OpenClaude section workers at login.")
    parser.add_argument("--respan-reviews", action="store_true", help="Run the Respan reviewer with the room at login.")
    args = parser.parse_args()
    action = args.action
    if action == "print":
        sys.stdout.buffer.write(plistlib.dumps(configuration(respan=args.respan_reviews, sections=args.section_reviews)))
        return 0
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    plist = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    try:
        if action == "install":
            install(plist, domain, respan=args.respan_reviews, sections=args.section_reviews)
            print(f"Registered {LABEL}.")
        elif action == "restart":
            restart(plist, domain, respan=args.respan_reviews, sections=args.section_reviews)
            print(f"Restarted {LABEL}.")
        elif action == "status":
            return launchctl("print", service).returncode
        else:
            launchctl("bootout", service, check=True)
            plist.unlink(missing_ok=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Talos service: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

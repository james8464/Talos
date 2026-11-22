"""Manage Talos's headless, per-user macOS service."""

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "local.talos.room"
LAUNCHCTL = "/bin/launchctl"


def configuration(root=ROOT, *, respan=False):
    # Adapted from OpenClaw's launchd-plist.ts. See talos_core/LICENSE-OpenClaw.
    state = root / "state"
    local_python = state / "kokoro-env" / "bin" / "python"
    python = local_python if os.access(local_python, os.X_OK) else Path(sys.executable)
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(root / "run_talos.py"),
                             "--headless", "--offline", "--system-voice", *(["--respan-reviews"] if respan else [])],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
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


def install(plist, domain, *, respan=False):
    service = f"{domain}/{LABEL}"
    data = plistlib.dumps(configuration(respan=respan))
    loaded = launchctl("print", service, capture=True).returncode == 0
    changed = not plist.is_file() or plist.read_bytes() != data
    if loaded and not changed:
        return
    if not loaded:
        sys.path.insert(0, str(ROOT))
        from room_service import DEFAULT_PORT
        from run_talos import check_bind

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
        launchctl("bootout", service, check=True)
    launchctl("bootstrap", domain, str(plist), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("print", "install", "status", "stop"))
    parser.add_argument("--respan-reviews", action="store_true", help="Run the Respan reviewer with the room at login.")
    args = parser.parse_args()
    action = args.action
    if action == "print":
        sys.stdout.buffer.write(plistlib.dumps(configuration(respan=args.respan_reviews)))
        return 0
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    plist = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    try:
        if action == "install":
            install(plist, domain, respan=args.respan_reviews)
            print(f"Registered {LABEL}.")
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

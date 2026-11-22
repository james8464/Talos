"""Register a five-minute Talos heartbeat for the logged-in macOS user."""

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "local.talos.heartbeat"


def enable(at_login):
    state = ROOT / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    destination = Path.home() / "Library/LaunchAgents" if at_login else state
    destination.mkdir(exist_ok=True)
    plist = destination / (LABEL + ".plist")
    config = dict(
        Label=LABEL,
        ProgramArguments=[
            sys.executable,
            str(ROOT / "overnight_goal.py"),
            "--heartbeat",
        ],
        RunAtLoad=True,
        StartInterval=300,
        ProcessType="Background",
        WorkingDirectory=str(ROOT),
        EnvironmentVariables={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        StandardErrorPath=str(state / "heartbeat-error.log"),
    )
    data = plistlib.dumps(config)
    changed = not plist.exists() or plist.read_bytes() != data
    if changed:
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as tmp:
            tmp.write(data)
        Path(tmp.name).replace(plist)
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + LABEL
    loaded = (
        subprocess.run(["launchctl", "print", service], capture_output=True).returncode
        == 0
    )
    if loaded and changed:
        subprocess.run(["launchctl", "bootout", service], check=True)
        loaded = False
    if not loaded:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at-login", action="store_true")
    try:
        enable(parser.parse_args().at_login)
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Heartbeat registration failed: {error}\n")

"""Consent-free camera and microphone readiness probe.

This compiles a small AVFoundation helper that reads authorization status and
device presence. It never opens a capture session, never records, and never
prompts for permission: a not-determined permission is reported, not requested.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "av_readiness.swift"
PERMISSIONS = {"authorized", "denied", "restricted", "not_determined", "unknown"}


class AVReadinessError(RuntimeError):
    pass


def available() -> bool:
    return SOURCE.is_file() and bool(shutil.which("swiftc"))


def _binary(state: Path, compiler: str) -> Path:
    target = Path(state) / "native" / "talos-av-readiness"
    if target.is_file() and target.stat().st_mtime >= SOURCE.stat().st_mtime:
        return target
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".av-readiness-", dir=target.parent)
    os.close(fd)
    try:
        completed = subprocess.run(
            [compiler, "-O", str(SOURCE), "-o", temporary],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode != 0:
            raise AVReadinessError(
                "AVFoundation helper failed to compile: "
                + " ".join((completed.stderr or "").split())[:300])
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


def readiness(state: Path | str, *, timeout: int = 60) -> dict:
    """Return permission/device state. The returned flags confirm no capture ran."""
    if not available():
        raise AVReadinessError("The native AVFoundation compiler is unavailable.")
    helper = _binary(Path(state), shutil.which("swiftc"))
    try:
        completed = subprocess.run(
            [str(helper)], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise AVReadinessError(f"AVFoundation readiness failed: {error}") from error
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300]
        raise AVReadinessError(detail or "AVFoundation readiness returned an error.")
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as error:
        raise AVReadinessError("AVFoundation readiness returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise AVReadinessError("AVFoundation readiness returned invalid JSON.")
    for key in ("camera_permission", "microphone_permission"):
        if payload.get(key) not in PERMISSIONS:
            raise AVReadinessError(f"Invalid {key} value.")
    for key in ("camera_device", "microphone_device", "capture_started", "recording_started"):
        if type(payload.get(key)) is not bool:
            raise AVReadinessError(f"Invalid {key} value.")
    if payload["capture_started"] or payload["recording_started"]:
        raise AVReadinessError("Readiness helper unexpectedly started capture.")
    payload["ready"] = (
        payload["camera_permission"] == "authorized" and payload["microphone_permission"] == "authorized"
        and payload["camera_device"] and payload["microphone_device"]
    )
    return payload


def describe(payload: dict) -> str:
    camera = "camera" if payload["camera_device"] else "no camera"
    microphone = "microphone" if payload["microphone_device"] else "no microphone"
    return (f"Camera {payload['camera_permission']} ({camera}), "
            f"microphone {payload['microphone_permission']} ({microphone}); "
            "no capture started.")

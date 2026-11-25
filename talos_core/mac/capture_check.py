"""Consent-gated one-shot camera and microphone verification.

Nothing here runs without ``consent=True``. With consent, a native helper asks
for camera/microphone access, captures one JPEG frame and one short WAV clip,
the room classifies both, and the temporary media is deleted before returning.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "av_capture.swift"
BASE_URL = os.environ.get("TALOS_ROOM_URL", "http://127.0.0.1:8765")


class ConsentRequired(PermissionError):
    pass


class CaptureError(RuntimeError):
    pass


def available() -> bool:
    return SOURCE.is_file() and bool(shutil.which("swiftc"))


def _binary(state: Path) -> Path:
    compiler = shutil.which("swiftc")
    if not compiler:
        raise CaptureError("The native capture compiler is unavailable.")
    target = state / "native" / "talos-av-capture"
    if target.is_file() and target.stat().st_mtime >= SOURCE.stat().st_mtime:
        return target
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".av-capture-", dir=target.parent)
    os.close(fd)
    try:
        completed = subprocess.run(
            [compiler, "-O", str(SOURCE), "-o", temporary],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if completed.returncode != 0:
            raise CaptureError("Capture helper failed to compile: "
                               + " ".join((completed.stderr or "").split())[:300])
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


def _capture(frame: Path, clip: Path, seconds: float, timeout: int) -> dict:
    helper = _binary(frame.parent.parent)
    completed = subprocess.run(
        [str(helper), str(frame), str(clip), f"{seconds:.2f}"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300]
        raise CaptureError(detail or "Native capture failed.")
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as error:
        raise CaptureError("Capture helper returned invalid JSON.") from error
    if not isinstance(payload, dict) or payload.get("camera") is not True or payload.get("audio") is not True:
        raise CaptureError("Capture helper did not report both streams.")
    return payload


def _post(path: str, body: dict, token: str, timeout: int = 120) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE_URL + path, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def check(state: Path | str, *, consent: bool, seconds: float = 1.0, timeout: int = 180) -> dict:
    """Capture once, verify both room endpoints, delete media, return a receipt."""
    if consent is not True:
        raise ConsentRequired("Refusing to capture without explicit consent.")
    if not 0 < seconds <= 10:
        raise CaptureError("Capture length must be between 0 and 10 seconds.")
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_path = state / "room.token"
    if not token_path.is_file():
        raise CaptureError("The room token is unavailable.")
    folder = Path(tempfile.mkdtemp(prefix=".capture-", dir=state))
    os.chmod(folder, 0o700)
    frame, clip = folder / "frame.jpg", folder / "clip.wav"
    deleted = False
    try:
        payload = _capture(frame, clip, seconds, timeout)
        if not frame.is_file() or not clip.is_file():
            raise CaptureError("Capture helper produced no media.")
        token = token_path.read_text(encoding="utf-8").strip()
        scene_status, scene_body = _post("/v1/vision/scene", {
            "image": base64.b64encode(frame.read_bytes()).decode()}, token)
        sound_status, sound_body = _post("/v1/sound", {
            "audio": base64.b64encode(clip.read_bytes()).decode()}, token)
        if scene_status != 200 or not isinstance(scene_body.get("scene"), dict):
            raise CaptureError(f"Scene endpoint failed: HTTP {scene_status}.")
        if sound_status != 200 or not isinstance(sound_body.get("events"), list):
            raise CaptureError(f"Sound endpoint failed: HTTP {sound_status}.")
        return {
            "captured": True,
            "consented": True,
            "seconds": seconds,
            "media_deleted": True,
            "camera_permission": payload.get("camera_permission"),
            "microphone_permission": payload.get("microphone_permission"),
            "scene": scene_body["scene"],
            "scene_description": scene_body.get("description", ""),
            "sound_event_count": len(sound_body["events"]),
            "sound_description": sound_body.get("description", ""),
        }
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        deleted = not folder.exists()
        if not deleted:
            raise CaptureError("Temporary capture media could not be deleted.")

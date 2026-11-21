"""Local Kokoro speech and its isolated setup."""

import base64
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path

from room_dialogue import SENSOR_LINES

STARTUP_LINE = "Talos online. I was beginning to think you had forgotten me."
DEFAULT_VOICE = "bm_george"
KOKORO_VERSION = "0.6.0"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
ASSET_HASHES = {
    MODEL_FILE: "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
    VOICES_FILE: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def voice_python(state: Path, *, offline: bool) -> Path | None:
    """Return an isolated Python with Kokoro installed without touching system Python."""
    interpreter = state / "kokoro-env" / "bin" / "python"
    probe = f"from importlib.metadata import version; assert version('kokoro-onnx') == '{KOKORO_VERSION}'"
    if interpreter.is_file() and subprocess.run(
        [str(interpreter), "-c", probe], capture_output=True,
    ).returncode == 0:
        return interpreter
    if offline or not (uv := shutil.which("uv")):
        return None
    print("Preparing the local Kokoro speech environment.", flush=True)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "UV_CACHE_DIR": str(state / "uv-cache")}
    if not interpreter.is_file():
        subprocess.run([uv, "venv", "--python", "3.12", str(state / "kokoro-env")], env=env, check=True)
    subprocess.run([uv, "pip", "install", "--python", str(interpreter),
                    f"kokoro-onnx=={KOKORO_VERSION}"], env=env, check=True)
    return interpreter


def prepare_assets(directory: Path, *, offline: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSET_HASHES.items():
        path = directory / name
        if path.is_file():
            continue
        if offline:
            raise RuntimeError(f"Kokoro asset {name} has not been downloaded.")
        print(f"Downloading local speech asset {name}.", flush=True)
        with tempfile.NamedTemporaryFile(prefix=name + ".", suffix=".part", dir=directory,
                                         delete=False) as output:
            pending = Path(output.name)
        try:
            with urllib.request.urlopen(RELEASE + name, timeout=45) as response, pending.open("wb") as output:
                shutil.copyfileobj(response, output)
            with pending.open("rb") as downloaded:
                if hashlib.file_digest(downloaded, "sha256").hexdigest() != expected:
                    raise RuntimeError(f"The downloaded Kokoro asset {name} failed its checksum.")
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def wav_base64(samples, sample_rate: int) -> str:
    pcm = (samples.clip(-1, 1) * 32767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return base64.b64encode(output.getvalue()).decode("ascii")


class KokoroVoice:
    def __init__(self, model_dir: Path, name: str):
        from kokoro_onnx import Kokoro

        self.model = Kokoro(str(model_dir / MODEL_FILE), str(model_dir / VOICES_FILE))
        self.name = name
        self.language = "en-gb" if name.startswith("b") else "en-us"
        self.lock = threading.Lock()
        self.fixed_lines = {}

    def speak(self, text: str) -> str:
        reusable = text in SENSOR_LINES or text in (
            "How long from now should I remind you?",
            "Sure. What's on your mind?",
            "You have a local Talos notification.",
            "Please give a whole number of minutes, hours, or days, up to 30 days.",
        )
        with self.lock:
            if reusable and text in self.fixed_lines:
                return self.fixed_lines[text]
            samples, rate = self.model.create(text, voice=self.name, speed=1.06, lang=self.language)
            wav = wav_base64(samples, rate)
            if reusable:
                self.fixed_lines[text] = wav
            return wav

"""Local speech recognition for bounded Telegram Ogg/Opus voice notes."""

import fcntl
import hashlib
import io
import os
import shutil
import urllib.request
from functools import lru_cache
from pathlib import Path

MAX_VOICE_SECONDS = 90
MAX_VOICE_BYTES = 2_000_000
SAMPLE_RATE = 16_000


MODEL_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
MODEL_DIGESTS = {
    "model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
    "config.json": "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}
DEFAULT_MODEL = Path(__file__).resolve().parent / "models/telegram-whisper-tiny"


def setup_voice():
    """Download only the pinned public model. This command needs no Telegram credentials."""
    def verified(path, digest):
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == digest

    DEFAULT_MODEL.parent.mkdir(parents=True, exist_ok=True)
    with (DEFAULT_MODEL.parent / ".telegram-voice-setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        installed = DEFAULT_MODEL.exists()
        stage = DEFAULT_MODEL if installed else DEFAULT_MODEL.with_name(".telegram-whisper-pending")
        stage.mkdir(mode=0o700, exist_ok=True)
        for name, digest in MODEL_DIGESTS.items():
            dest = stage / name
            if verified(dest, digest):
                continue
            if installed:
                raise VoiceUnavailable(f"Model verification failed for {name}.")
            url = f"https://huggingface.co/Systran/faster-whisper-tiny/resolve/{MODEL_REVISION}/{name}"
            pending = stage / (name + ".part")
            try:
                with urllib.request.urlopen(url, timeout=60) as src, pending.open("wb") as target:
                    shutil.copyfileobj(src, target)
                if not verified(pending, digest):
                    raise VoiceUnavailable(f"Model verification failed for {name}.")
                pending.replace(dest)
            finally:
                pending.unlink(missing_ok=True)
        load_model(stage)
        if not installed:
            stage.rename(DEFAULT_MODEL)


class VoiceUnavailable(RuntimeError):
    pass


def model_directory():
    configured = os.environ.get("TALOS_TELEGRAM_WHISPER_MODEL")
    path = Path(configured).expanduser().resolve() if configured else DEFAULT_MODEL
    # faster-whisper can fetch a missing tokenizer even with local model weights.
    if not all((path / name).is_file() for name in ("model.bin", "tokenizer.json", "config.json")):
        raise VoiceUnavailable("The configured local Whisper model is incomplete.")
    return path


@lru_cache(maxsize=1)
def load_model(path):
    try:
        from faster_whisper import WhisperModel
        return WhisperModel(str(path), device="cpu", compute_type="int8",
                            cpu_threads=2, local_files_only=True)
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        raise VoiceUnavailable("Local speech recognition is unavailable.") from error


def decode_note(data):
    import av
    import numpy as np

    if len(data) > MAX_VOICE_BYTES or not data.startswith(b"OggS"):
        raise ValueError("Expected a bounded Ogg voice note.")
    with av.open(io.BytesIO(data), format="ogg") as media:
        if len(media.streams) != 1 or media.streams[0].type != "audio":
            raise ValueError("Expected a single audio stream.")
        stream = media.streams.audio[0]
        if stream.codec_context.name != "opus":
            raise ValueError("Expected Opus audio.")
        converter = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        samples = bytearray()

        def collect(frame):
            for mono in converter.resample(frame):
                if len(samples) + mono.samples * 2 > MAX_VOICE_SECONDS * SAMPLE_RATE * 2:
                    raise ValueError("Voice note exceeds the duration limit.")
                samples.extend(mono.to_ndarray().tobytes())

        for frame in media.decode(stream):
            frame.pts = None
            collect(frame)
        collect(None)
    if not samples:
        raise ValueError("Empty voice note.")
    return np.frombuffer(samples, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_note(data):
    path = model_directory()
    try:
        samples = decode_note(data)
    except ImportError as error:
        raise VoiceUnavailable("Install the local speech dependencies.") from error
    # Adapted from Hermes tools/transcription_local.py at 0f602c5 (MIT).
    segments, _ = load_model(path).transcribe(
        samples, beam_size=5, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False, no_speech_threshold=0.6, log_prob_threshold=-1.0)
    return " ".join(s.text.strip() for s in segments
                    if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)).strip()


@lru_cache(maxsize=1)
def local_voice():
    from room_voice import DEFAULT_VOICE, KokoroVoice, prepare_assets
    directory = Path(__file__).resolve().parent / "models/kokoro-room"
    prepare_assets(directory, offline=True)
    return KokoroVoice(directory, DEFAULT_VOICE)


def render_note(text):
    """Encode the existing local Talos voice as an in-memory Telegram voice message."""
    import av
    import base64
    wav = base64.b64decode(local_voice().speak(text))
    buf = io.BytesIO()
    with av.open(io.BytesIO(wav), format="wav") as source, av.open(buf, "w", format="ogg") as target:
        encoder = target.add_stream("libopus", rate=48000)
        encoder.layout = "mono"
        encoder.bit_rate = 24000
        for frame in source.decode(audio=0):
            for packet in encoder.encode(frame):
                target.mux(packet)
        for packet in encoder.encode():
            target.mux(packet)
    return buf.getvalue()

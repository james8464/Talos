"""Transcribe one 16 kHz mono WAV with the MLX Whisper model.

Prints one JSON object with the transcript text. It decodes the WAV with the
standard library and passes samples to MLX Whisper directly, so it needs no
ffmpeg and never plays audio.
"""

import json
import sys
import wave

import mlx_whisper
import numpy as np


def main() -> None:
    with wave.open(sys.argv[1], "rb") as clip:
        if clip.getframerate() != 16_000 or clip.getnchannels() != 1:
            raise SystemExit("expected 16 kHz mono audio")
        samples = np.frombuffer(
            clip.readframes(clip.getnframes()), dtype=np.int16
        ).astype(np.float32)
    result = mlx_whisper.transcribe(
        samples / 32768.0, path_or_hf_repo=sys.argv[2]
    )
    print(json.dumps({"text": (result.get("text") or "").strip()}))


if __name__ == "__main__":
    main()

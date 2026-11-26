"""Synthesize one phrase with the local Kokoro voice to a WAV file.

The clip is written to the path given by the caller; this never plays audio.
"""

import json
import sys
import wave

import numpy as np
from kokoro_onnx import Kokoro


def main() -> None:
    model_path, voices_path, voice, speed, text, output = sys.argv[1:7]
    model = Kokoro(model_path, voices_path)
    language = "en-gb" if voice.startswith("b") else "en-us"
    samples, rate = model.create(
        text, voice=voice, speed=float(speed), lang=language
    )
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    print(
        json.dumps(
            {
                "sample_rate": rate,
                "seconds": len(samples) / rate,
                "bytes": len(pcm.tobytes()),
            }
        )
    )


if __name__ == "__main__":
    main()

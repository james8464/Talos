"""One policy for every Talos audio boundary.

Set ``TALOS_MUTE=1`` (or pass ``--mute`` to the launcher) in environments where
the machine must never produce sound. Synthesis still returns an error so a
caller can fall back to text instead of silently claiming it spoke.
"""

import os


def muted() -> bool:
    return os.environ.get("TALOS_MUTE") == "1"


def require_audio() -> None:
    if muted():
        raise RuntimeError("Audio output is muted.")

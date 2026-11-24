"""One policy for every Talos audio boundary.

Set ``TALOS_MUTE`` to a truthy value (``1``, ``true``, ``yes`` or ``on``), or
pass ``--mute`` to the launcher, in environments where the machine must never
produce sound. Synthesis still returns an error so a caller can fall back to
text instead of silently claiming it spoke.
"""

import os

MUTED_VALUES = frozenset({"1", "true", "yes", "on"})


def truthy(value) -> bool:
    return str(value or "").strip().casefold() in MUTED_VALUES


def muted() -> bool:
    return truthy(os.environ.get("TALOS_MUTE"))


def require_audio() -> None:
    if muted():
        raise RuntimeError("Audio output is muted.")

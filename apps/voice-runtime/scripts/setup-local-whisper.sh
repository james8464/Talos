#!/bin/sh
# Creates the local, offline Whisper environment the voice runtime uses when
# Soniox is unavailable. Audio still never plays; this only transcribes.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
uv venv "$root/.venv" --python 3.12
uv pip install --python "$root/.venv/bin/python" mlx-whisper numpy
printf 'Set this to transcribe with local Whisper:\n  TALOS_LOCAL_WHISPER_PYTHON=%s\n' \
  "$root/.venv/bin/python"

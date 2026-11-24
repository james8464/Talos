#!/bin/zsh
# Silent by default: this machine must not produce audio.
cd -- "${0:A:h}" || exit 1
export TALOS_MUTE=1
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python run_talos.py --mute "$@"
fi
if [[ -x state/kokoro-env/bin/python ]]; then
    exec state/kokoro-env/bin/python run_talos.py --mute "$@"
fi
exec python3 run_talos.py --mute "$@"

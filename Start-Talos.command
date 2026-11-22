#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ -x state/kokoro-env/bin/python ]]; then
    exec state/kokoro-env/bin/python run_talos.py "$@"
fi
exec python3 run_talos.py "$@"

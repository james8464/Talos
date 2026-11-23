#!/bin/zsh
cd -- "${0:A:h}" || exit 1
interpreter=python3
[[ -x state/kokoro-env/bin/python ]] && interpreter=state/kokoro-env/bin/python
"$interpreter" tools/talos_service.py install --respan-reviews --section-reviews || exit 1
./Start-Talos.command --attach || exit 1
exec ./Talos-Activity.command

#!/bin/zsh
cd -- "${0:A:h}" || exit 1
python3 tools/talos_service.py install --respan-reviews || exit 1
./Start-Talos.command --attach || exit 1
exec ./Talos-Activity.command

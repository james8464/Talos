#!/bin/sh
# Runs the bundled teamhub CLI with the Node built into Talos.
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app="$dir/../../../../MacOS/Talos"
[ -x "$app" ] || app="$dir/../../../../Talos.exe"
[ -x "$app" ] || app="$dir/../../../../talos"
[ -x "$app" ] || app="${TALOS_NODE:-node}"
script="$dir/teamhub.js"
case "$app" in
  *.exe)
    # WSL passes Linux paths as literal Windows arguments and only carries
    # explicitly listed Linux environment variables to Windows executables.
    if command -v wslpath >/dev/null 2>&1; then
      script=$(wslpath -w "$script") || exit 1
      WSLENV="${WSLENV:+$WSLENV:}ELECTRON_RUN_AS_NODE:WSL_DISTRO_NAME"
      export WSLENV
    fi
    ;;
esac
ELECTRON_RUN_AS_NODE=1 exec "$app" --use-env-proxy "$script" "$@"

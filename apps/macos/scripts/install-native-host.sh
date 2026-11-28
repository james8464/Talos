#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/Extension/talos-browser/manifest.json"
HOST_NAME="app.talos.native"

BIN="${1:-${TALOS_NATIVE_HOST_BIN:-}}"
if [ -z "${BIN}" ]; then
  for candidate in \
    "$ROOT/.build/Build/Products/Debug/TalosNativeHost" \
    "$ROOT/.build/Build/Products/Release/TalosNativeHost" \
    "/Applications/Talos.app/Contents/Resources/TalosNativeHost"; do
    if [ -x "$candidate" ]; then BIN="$candidate"; break; fi
  done
fi
if [ -z "${BIN}" ] || [ ! -x "${BIN}" ]; then
  echo "TalosNativeHost was not found. Run 'make build' first or pass its path." >&2
  exit 1
fi

INSTALL_BIN_DIR="$HOME/Library/Application Support/Talos/bin"
mkdir -p "$INSTALL_BIN_DIR"
INSTALLED_HOST="$INSTALL_BIN_DIR/TalosNativeHost"
cp "$BIN" "$INSTALLED_HOST"
chmod +x "$INSTALLED_HOST"

EXT_ID="$(python3 - "$MANIFEST" <<'PY'
import base64, hashlib, json, sys
manifest = json.load(open(sys.argv[1]))
key = base64.b64decode(manifest["key"])
digest = hashlib.sha256(key).hexdigest()[:32]
print("".join(chr(ord("a") + int(c, 16)) for c in digest))
PY
)"

HOST_DIR="${TALOS_NATIVE_HOST_DIR:-$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts}"
mkdir -p "$HOST_DIR"
HOST_MANIFEST="$HOST_DIR/$HOST_NAME.json"

python3 - "$HOST_MANIFEST" "$INSTALLED_HOST" "$EXT_ID" <<'PY'
import json, sys
path, host, ext = sys.argv[1], sys.argv[2], sys.argv[3]
json.dump({
    "name": "app.talos.native",
    "description": "Talos browser bridge",
    "path": host,
    "type": "stdio",
    "allowed_origins": [f"chrome-extension://{ext}/"],
}, open(path, "w"), indent=2)
PY

echo "Installed native host: $INSTALLED_HOST"
echo "Registered manifest:    $HOST_MANIFEST"
echo "Extension ID:           $EXT_ID"
echo "Load unpacked extension: $ROOT/Extension/talos-browser"

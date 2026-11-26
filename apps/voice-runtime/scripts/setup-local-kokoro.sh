#!/bin/sh
# Installs the free local Kokoro voice and downloads its model. Nothing is
# played; this only prepares synthesis.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
models="$root/.kokoro"
base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
mkdir -p "$models"

fetch() {
  name=$1
  want=$2
  if [ -f "$models/$name" ] &&
    [ "$(shasum -a 256 "$models/$name" | awk '{print $1}')" = "$want" ]; then
    return
  fi
  curl -fL --retry 3 -o "$models/$name.part" "$base/$name"
  got=$(shasum -a 256 "$models/$name.part" | awk '{print $1}')
  [ "$got" = "$want" ] || { rm -f "$models/$name.part"; echo "checksum failed for $name" >&2; exit 1; }
  mv "$models/$name.part" "$models/$name"
}

fetch kokoro-v1.0.int8.onnx ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c
fetch voices-v1.0.bin bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d

uv venv "$root/.venv" --python 3.12
uv pip install --python "$root/.venv/bin/python" kokoro-onnx==0.6.1 numpy
printf 'Local Kokoro voice ready in %s\n' "$models"

"""Build the local CPU reviewer for the verified Docker Desktop llama.cpp release."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_source_model import WEIGHT, file_id, model_path

RUNTIME = Path("/Applications/Docker.app/Contents/Resources/model-runner")
REV = "ac4cddeb0dbd778f650bf568f6f08344a06abe3a"
HASHES = {
    "ggml-alloc.h": "94e4cd069b9313b2ceb35dacec901981e0bb478d8bb31035b7126be091998c23",
    "ggml-backend.h": "a620e815b43a44cc72d5f216629a3a91980335b61bc37eb6b2d0813368c3704f",
    "ggml-cpu.h": "1aafe97e576ea38c0da57517fb2492955d0b69c1a799842481fc069d6d9d28ef",
    "ggml-opt.h": "3586de1bc8a934b5c72339e2b6937b0641e8f149b512231e666f67de0736eea2",
    "ggml.h": "98c5fcf96279e16c09d42dcba482be1b03434839db723bba4b65518e424ba181",
    "gguf.h": "2ddb276a5bece743433160ad863279473431c9d4c171d468bf860a3cda3fbf3f",
    "llama.h": "56b0a7b4de7a20a07e0faf8a5a8c7b85b86fa3a1367385dc98cafe1a6dc7ad46",
}


def install(root, headers):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This backend requires Apple Silicon.")
    version = subprocess.run(
        [RUNTIME / "bin/com.docker.llama-server", "--version"],
        capture_output=True,
        timeout=10,
        check=True,
        text=True,
    )
    if not re.search(r"\(ac4cdde\)", version.stdout + version.stderr):
        raise ValueError(
            "Docker Desktop's llama.cpp build does not match the pinned SDK."
        )
    weights = model_path(root)
    verified_weights = file_id(weights)
    with weights.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != WEIGHT.split(":")[1] or verified_weights != file_id(weights):
        raise ValueError("The project Qwen3 GGUF does not match its pinned digest.")
    output = root / "state/local-reviewer"
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        stage = Path(temporary)
        for name, digest in HASHES.items():
            if headers:
                data = (headers / name).read_bytes()
            else:
                folder = "include" if name == "llama.h" else "ggml/include"
                with urlopen(
                    f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{REV}/{folder}/{name}",
                    timeout=20,
                ) as reply:
                    data = reply.read(160000)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"SDK verification failed for {name}")
            (stage / name).write_bytes(data)
        lib = RUNTIME / "lib"
        executable = stage / "complete"
        subprocess.run(
            [
                "/usr/bin/clang++",
                "-std=c++17",
                "-O2",
                "-I",
                stage,
                root / "tools/native/source_reviewer.cpp",
                "-L",
                lib,
                "-Wl,-rpath," + str(lib),
                "-lllama",
                "-lggml",
                "-o",
                executable,
            ],
            check=True,
            timeout=60,
        )
        os.chmod(executable, 0o700)
        executable.replace(output / "complete")
        # Invalidate the helper on any replacement/upgrade of its ABI dependency.
        marker = stage / "engine-id"
        marker.write_text(
            json.dumps([file_id(lib / "libllama.dylib"), verified_weights])
        )
        marker.replace(output / "engine-id")
    return output / "complete"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headers",
        type=Path,
        help="Use an offline directory of headers from the pinned SDK.",
    )
    args = parser.parse_args()
    print(install(ROOT, args.headers))

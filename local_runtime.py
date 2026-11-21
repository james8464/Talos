"""Preflight checks for the local Talos voice runtime."""

import http.client
import json
import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
WHISPER_MODEL = "whisper-base-mlx"
KOKORO_MODEL = "kokoro-v1.0.onnx"
KOKORO_VOICES = "voices-v1.0.bin"
OLLAMA_MODEL = "qwen2.5:1.5b"
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434


def check_model_files(models_dir: Path = MODELS_DIR) -> None:
    assets = (
        models_dir / WHISPER_MODEL / "config.json",
        models_dir / WHISPER_MODEL / "weights.npz",
        models_dir / KOKORO_MODEL,
        models_dir / KOKORO_VOICES,
    )
    missing = [str(asset) for asset in assets if not asset.is_file() or not asset.stat().st_size]
    if missing:
        raise RuntimeError(
            "Missing model files. Follow the one-time setup in README.md:\n"
            + "\n".join(missing)
        )


def request_ollama_json(
    method: str, path: str, *, payload: dict | None = None, timeout: int = 3
) -> dict:
    """Call only the loopback Ollama server, with no proxy or redirect handling."""
    port = int(os.environ.get("TALOS_OLLAMA_PORT", OLLAMA_PORT))
    connection = http.client.HTTPConnection(OLLAMA_HOST, port, timeout=timeout)
    try:
        if payload is None:
            connection.request(method, path)
        else:
            connection.request(
                method, path, body=json.dumps(payload), headers={"Content-Type": "application/json"}
            )
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Local Ollama returned HTTP {response.status} for {path}.")
            body = response.read(256 * 1024 + 1)
        if len(body) > 256 * 1024:
            raise ValueError("Unexpectedly large response")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise RuntimeError(
            f"Could not read the local Ollama response for {path}. Start Ollama locally "
            "with OLLAMA_NO_CLOUD=1 as shown in README.md."
        ) from error
    finally:
        connection.close()


def check_ollama_model(model_name: str = OLLAMA_MODEL) -> None:
    models = request_ollama_json("GET", "/api/tags").get("models")
    if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
        raise RuntimeError("Local Ollama returned an invalid model list.")

    for model in models:
        matches_model = model_name in (model.get("name"), model.get("model"))
        is_remote = model.get("remote_model") or model.get("remote_host")
        if matches_model and not is_remote:
            return
    raise RuntimeError(f"Missing local model {model_name!r}. Install a local Ollama model during setup.")


def check_platform() -> str:
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise RuntimeError("Use an Apple Silicon Mac with a native arm64 Python.")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12. Run `uv run --no-sync python bot.py --check`.")
    return f"Apple Silicon, Python {platform.python_version()}"


def check_dependencies() -> str:
    try:
        return f"Pipecat {version('pipecat-ai')}"
    except PackageNotFoundError:
        raise RuntimeError("Pipecat is missing. Run `uv sync --python 3.12`.") from None


def runtime_checks() -> list[tuple[str, str, bool]]:
    """Report every prerequisite in a single run."""
    checks = (
        ("Platform", check_platform),
        ("Dependencies", check_dependencies),
        ("Speech models", check_model_files),
        ("Local Ollama", check_ollama_model),
    )
    results = []
    for label, check in checks:
        try:
            results.append((label, check() or "Ready", True))
        except (OSError, RuntimeError) as error:
            results.append((label, str(error), False))
    return results

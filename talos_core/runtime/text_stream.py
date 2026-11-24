"""Silent token streaming from the local Ollama model.

This is the text counterpart of the interruptible spoken stream. It yields
model deltas as they arrive and never touches the audio device.
"""

from __future__ import annotations

import http.client
import json
import os

from talos_core.runtime.local_runtime import OLLAMA_HOST, OLLAMA_PORT

# qwen3 emits visible reasoning in streamed content even with think disabled.
# This model streams clean answers directly.
DEFAULT_MODEL = "qwen2.5:1.5b"


def stream_reply(prompt: str, *, model: str = DEFAULT_MODEL,
                 system: str | None = None, timeout: float = 60):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A prompt is required.")
    messages = [
        {"role": "system",
         "content": (system or "Answer in clear, concise plain text. Do not use Markdown.")
         + "\n/no_think"},
        {"role": "user", "content": prompt.strip()[:8000]},
    ]
    payload = {
        "model": model,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "messages": messages,
        "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 256},
    }
    port = int(os.environ.get("TALOS_OLLAMA_PORT", OLLAMA_PORT))
    connection = http.client.HTTPConnection(OLLAMA_HOST, port, timeout=timeout)
    try:
        connection.request(
            "POST", "/api/chat", body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Local model returned HTTP {response.status}.")
            for _ in range(4096):
                line = response.readline(32_769)
                if not line:
                    return
                if len(line) > 32_768:
                    raise RuntimeError("Local model returned an oversized chunk.")
                try:
                    chunk = json.loads(line)
                except ValueError as error:
                    raise RuntimeError("Local model returned invalid JSON.") from error
                if "error" in chunk:
                    raise RuntimeError(str(chunk["error"])[:300])
                content = chunk.get("message", {}).get("content", "")
                if isinstance(content, str) and content:
                    yield content
                if chunk.get("done"):
                    return
            raise RuntimeError("Local model stream did not finish.")
    except OSError as error:
        raise RuntimeError(f"Local model is unavailable: {error}") from error
    finally:
        connection.close()

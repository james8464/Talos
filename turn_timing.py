"""Optional local transcript completeness signal. Never writes or executes a turn."""

import json

from local_runtime import check_ollama_model, request_ollama_json


def classify(text, model):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 800:
        raise ValueError("Use a caption of at most 800 characters.")
    check_ollama_model(model)
    raw = request_ollama_json(
        "POST",
        "/api/chat",
        timeout=1.2,
        payload={
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": -1,
            "messages": [
                {
                    "role": "system",
                    "content": "Classify an unfinished or complete spoken utterance. "
                    "Set hold to true if the speaker stopped mid-clause. Set hold to false for a finished question or command. "
                    "Judge syntax and meaning only, never answer the request.",
                },
                {"role": "user", "content": text},
            ],
            "format": {"type": "object", "properties": {"hold": {"type": "boolean"}}, "required": ["hold"]},
            "options": {"temperature": 0, "num_predict": 24, "num_ctx": 8192},
        },
    )
    try:
        hold = json.loads(raw["message"]["content"])["hold"]
        if type(hold) is not bool:
            raise ValueError
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("Local turn model returned an invalid classification.") from error
    return {"hold": hold}

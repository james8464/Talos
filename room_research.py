"""User-requested public lookups. No room history or device data is sent."""

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit

FREE_MODELS = (
    "zai/glm-4.7-flash",
    "google_gemini_ai/gemma-4-26b-a4b-it",
    "zai/glm-4.5-flash",
    "google_gemini_ai/gemma-4-31b-it",
    "zai/glm-4.6v-flash",
)
FREE_MODEL = FREE_MODELS[0]
_turns = count()
_cooldown = {}


class RouteUnavailable(RuntimeError):
    pass
KEY_FILE = Path(__file__).resolve().parent / "state" / "respan.key"
SEARCH_PACKAGE = "ddgs==9.5.1"


def external_request(text):
    match = re.fullmatch(
        r"(?:(?:hey[, ]+)?talos[, ]+)?(?:please )?"
        r"(search (?:the )?web(?: for)?|look up online|search online(?: for)?|ask respan)"
        r"[\s,:]+(.+)", text.strip(), re.I | re.S,
    )
    if match:
        return ("respan" if match[1].lower() == "ask respan" else "web", match[2].strip())
    return None


def respan_completion(messages, *, max_tokens, timeout=45, key_file=None, on_model=None, validate=None, public=False):
    pool = FREE_MODELS + ("phonic/phonic-1",) if public else FREE_MODELS
    start = next(_turns) % len(pool)
    routes = pool[start:] + pool[:start]
    deadline = time.monotonic() + timeout
    for model in routes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _cooldown.get(model, 0) > time.monotonic():
            continue
        if on_model:
            on_model(model)
        try:
            text, usage = _respan_request(messages, model, max_tokens, remaining, key_file)
            if validate and not validate(text):
                continue
            return text, usage
        except RouteUnavailable:
            _cooldown[model] = time.monotonic() + 300
    raise RuntimeError("Respan free text routes are unavailable or returned unusable output.")


def _respan_request(messages, model, max_tokens, timeout, key_file):
    path = KEY_FILE if key_file is None else key_file
    key = os.environ.get("RESPAN_API_KEY") or (path.read_text().strip() if path.is_file() else "")
    if not key:
        raise RuntimeError("Respan has no local API key.")
    connection = http.client.HTTPSConnection("api.respan.ai", timeout=timeout)
    try:
        connection.request("POST", "/api/chat/completions", body=json.dumps({
            "model": model, "max_tokens": max_tokens, "stream": False, "messages": messages,
        }).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with connection.getresponse() as response:
            if response.status in (400, 402, 403, 404, 422, 429) or response.status >= 500:
                raise RouteUnavailable(f"Respan route returned HTTP {response.status}.")
            if response.status != 200:
                raise RuntimeError(f"Respan returned HTTP {response.status}.")
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Oversized response")
            result = json.loads(data)
        choice = result["choices"][0]
        reply = choice["message"]["content"]
        if not isinstance(reply, str) or not reply.strip() or choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Incomplete response")
        usage = result.get("usage") or {}
        counts = {k: usage[k] for k in ("prompt_tokens", "completion_tokens")
                  if type(usage.get(k)) is int and usage[k] >= 0}
        return reply.strip(), counts
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"Respan connection unavailable ({type(error).__name__}).") from error
    except (ValueError, TypeError, LookupError, AttributeError) as error:
        raise RouteUnavailable("Respan returned an incomplete response.") from error
    finally:
        connection.close()


def public_answer(question):
    answer, _ = respan_completion([
        {"role": "system", "content": "Answer briefly in plain spoken English."},
        {"role": "user", "content": question}], max_tokens=160, timeout=6, public=True)
    return answer[:600]


def search_worker(query):
    from ddgs import DDGS

    return DDGS(timeout=5).text(query, backend="duckduckgo", max_results=4)


def search_web(query):
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("Web search requires uv.")
    # Query travels on stdin. stdout contains only the four search results.
    try:
        result = subprocess.run(
            [uv, "run", "--no-project", "--python", "3.12", "--with", SEARCH_PACKAGE,
             "python", str(Path(__file__).resolve())], input=query[:320], capture_output=True,
            text=True, timeout=30, check=True,
            env={k: v for k, v in os.environ.items() if k != "RESPAN_API_KEY"},
        )
        sources = []
        for row in json.loads(result.stdout)[:4]:
            url = row["href"]
            if urlsplit(url).scheme in ("https", "http") and urlsplit(url).hostname:
                sources.append({"title": row["title"][:180], "url": url[:2048], "excerpt": row["body"][:450]})
        return sources
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, LookupError) as error:
        raise RuntimeError("Web search is unavailable.") from error


if __name__ == "__main__":
    print(json.dumps(search_worker(sys.stdin.read(320))))

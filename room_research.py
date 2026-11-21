"""User-requested public lookups. No room history or device data is sent."""

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

FREE_MODEL = "openrouter/z-ai/glm-5.2:free"
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


def public_answer(question):
    key = os.environ.get("RESPAN_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.is_file() else "")
    if not key:
        raise RuntimeError("Respan has no local API key.")
    connection = http.client.HTTPSConnection("api.respan.ai", timeout=6)
    try:
        connection.request("POST", "/api/chat/completions", body=json.dumps({
            "model": FREE_MODEL, "max_tokens": 160, "stream": False,
            "messages": [{"role": "system", "content": "Answer briefly in plain spoken English."},
                         {"role": "user", "content": question}],
        }).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Respan returned HTTP {response.status}.")
            result = json.loads(response.read(65537))
        choice = result["choices"][0]
        reply = choice["message"]["content"]
        if not isinstance(reply, str) or not reply.strip() or choice.get("finish_reason") == "length":
            raise ValueError
        return reply.strip()[:600]
    except (OSError, http.client.HTTPException, ValueError, TypeError, LookupError) as error:
        raise RuntimeError("The free Respan model did not return a complete answer.") from error
    finally:
        connection.close()


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

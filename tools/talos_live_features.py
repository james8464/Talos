"""Ask the running Talos room to exercise its user-visible features.

This is a live acceptance check, not a unit test. It requires the launchd
service (or another room endpoint) on the configured port and the local owner
token. It also checks that audio synthesis is refused and that no speech
processes are running.

Run:
    python3 -m tools.talos_live_features
    python3 -m tools.talos_live_features --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / "state" / "room.token"
BASE = "http://127.0.0.1:8765"


def post(path: str, body: dict, token: str, timeout: int = 180) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def ask(token: str, text: str) -> dict:
    status, body = post("/v1/turn", {"text": text, "source": "text"}, token)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body.get('error', body)}")
    return body


def check(checks: list[dict], name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})


def run() -> dict:
    token = TOKEN_PATH.read_text().strip()
    stamp = int(time.time())
    checks: list[dict] = []

    capabilities = ask(token, "What can you do?")["reply"]
    check(checks, "capabilities", "reminders" in capabilities and "muted" in capabilities, capabilities)

    now = ask(token, "What time is it?")["reply"]
    check(checks, "time", bool(re.search(r"It is \d\d:\d\d \S+ on \w+ \d\d \w+ \d{4}\.", now)), now)

    date = ask(token, "What is today's date?")["reply"]
    check(checks, "date", bool(re.search(r"on \w+ \d\d \w+ \d{4}\.", date)), date)

    math = ask(token, "What is 144 / 12?")["reply"]
    check(checks, "calculation", "144 divided by 12 is 12." in math, math)

    reminder = ask(token, "Remind me in ten minutes to check the live feature pass.")
    check(checks, "reminder", reminder.get("action") == "applied" and reminder.get("tool") == "set_reminder",
          reminder.get("reply", ""))

    task_text = f"verify live features {stamp}"
    task = ask(token, f"Create a task to {task_text}.")
    check(checks, "task", task.get("action") == "applied" and task.get("tool") == "create_task",
          task.get("reply", ""))

    note = ask(token, f"Save a note that the live feature pass is {stamp}.")
    check(checks, "note", note.get("action") == "applied" and note.get("tool") == "save_note",
          note.get("reply", ""))

    inbox = ask(token, "What is on my plate today?")["reply"]
    check(checks, "inbox", task_text in inbox and "#" in inbox, inbox)

    fact_subject = f"live pass code {stamp}"
    fact_value = f"cobalt-{stamp}"
    saved = ask(token, f"Remember that my {fact_subject} is {fact_value}.")["reply"]
    recalled = ask(token, f"What is my {fact_subject}?")["reply"]
    check(checks, "personal_fact", fact_value in recalled, recalled)
    check(checks, "personal_fact_saved", "remember" in saved.casefold(), saved)

    memory = ask(token, f"Search your memory for {fact_subject}.")["reply"]
    check(checks, "memory_search", fact_value in memory, memory)

    knowledge = ask(token, "Search your knowledge for Room.")["reply"]
    check(checks, "knowledge_search", "Knowledge results for Room" in knowledge, knowledge)

    windows = ask(token, "What windows are open?")["reply"]
    check(checks, "mac_windows", "Windows:" in windows or "frontmost app" in windows, windows)

    permissions = ask(token, "Check Mac permissions.")["reply"]
    check(checks, "mac_permissions", "Accessibility is" in permissions, permissions)

    ask(token, "Open TextEdit.")
    inspected = ask(token, "Inspect TextEdit.")["reply"]
    check(checks, "mac_inspect", "TextEdit:" in inspected, inspected)

    screenshot = ask(token, "Take a screenshot.")["reply"]
    check(checks, "mac_screenshot", "Screenshot saved to" in screenshot, screenshot)

    vision = ask(token, "What can you see right now?")["reply"]
    check(checks, "camera_handshake", "camera frame" in vision or "can't see" in vision, vision)

    status, voice = post("/v1/voice", {"text": "This must never play."}, token, timeout=30)
    check(checks, "voice_disabled", status == 503 and "wav" not in voice,
          f"HTTP {status} {voice.get('error', '')}")

    audio = subprocess.run(["pgrep", "-fl", "afplay|/usr/bin/say|kokoro"],
                           capture_output=True, text=True)
    check(checks, "no_audio_processes", audio.returncode != 0, audio.stdout.strip())

    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for item in result["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"] + " · " + item["detail"])
        print("OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

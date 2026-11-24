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


def cleanup(stamp: int, screenshot: str) -> None:
    """Remove artifacts created by this live pass from the real room state."""
    from talos_core import todo
    from talos_core.memory.agent_memory import STATE_PATH, open_memory

    with open_memory(STATE_PATH) as db, db:
        db.execute(
            "DELETE FROM inbox WHERE text = ? OR text LIKE ? OR text LIKE ?",
            ("check the live feature pass", f"verify live features {stamp}%",
             f"the live feature pass is {stamp}%"),
        )
        db.execute(
            "DELETE FROM personal_facts WHERE subject LIKE ? OR subject LIKE ?",
            (f"live pass code {stamp}%", f"forgotten code {stamp}%"),
        )
    todo.remove_matching(STATE_PATH.parent, "live todo")
    if screenshot:
        Path(screenshot).unlink(missing_ok=True)


def run(keep: bool = False) -> dict:
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

    tasks = ask(token, "List my tasks.")["reply"]
    check(checks, "task_list", task_text in tasks, tasks)

    goals = ask(token, "Show my goals.")["reply"]
    check(checks, "goals", "goal" in goals.casefold(), goals)

    todo_text = f"live todo {stamp}"
    added_todo = ask(token, f"Add a todo to {todo_text}.")["reply"]
    listed_todos = ask(token, "Show my todos.")["reply"]
    check(checks, "todo", todo_text in added_todo and todo_text in listed_todos, listed_todos)

    local_skills = ask(token, "List my skills.")["reply"]
    check(checks, "skills", "skill" in local_skills.casefold(), local_skills)

    peer_nodes = ask(token, "List nodes.")["reply"]
    check(checks, "nodes", "node" in peer_nodes.casefold(), peer_nodes)

    maintenance = ask(token, "Show the maintenance report.")["reply"]
    check(checks, "maintenance", "Maintenance:" in maintenance, maintenance)

    model = ask(token, "What model are you using?")["reply"]
    plugins = ask(token, "List my plugins.")["reply"]
    subagents = ask(token, "List subagents.")["reply"]
    checkpoints = ask(token, "List checkpoints.")["reply"]
    check(checks, "model", "using" in model.casefold(), model)
    check(checks, "plugins", "plugin" in plugins.casefold(), plugins)
    check(checks, "subagents", "subagent" in subagents.casefold(), subagents)
    check(checks, "checkpoints", "checkpoint" in checkpoints.casefold(), checkpoints)

    health = ask(token, "Check your health.")["reply"]
    check(checks, "health", "Audio is muted" in health, health)

    code = ask(token, "Search the code graph for Room.")["reply"]
    check(checks, "code_graph", "Code results for Room" in code, code)

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

    forgotten = f"forgotten code {stamp}"
    ask(token, f"Remember that my {forgotten} is temporary-{stamp}.")
    ask(token, f"Forget my {forgotten}.")
    missing = ask(token, f"What is my {forgotten}?")["reply"]
    check(checks, "missing_fact", "don't have a saved fact" in missing, missing)

    thanks = ask(token, "Thank you.")["reply"]
    present = ask(token, "Are you there?")["reply"]
    check(checks, "small_talk", "welcome" in thanks.casefold() and "here" in present.casefold(),
          f"{thanks} / {present}")

    fact = ask(token, "Tell me a fun fact.")["reply"]
    mountain = ask(token, "What is the tallest mountain?")["reply"]
    tired = ask(token, "I feel tired.")["reply"]
    identity = ask(token, "What is my name?")["reply"]
    check(checks, "public_fact", "Honey" in fact and "whatsapp" not in fact.casefold(), fact)
    check(checks, "general_knowledge", "Everest" in mountain, mountain)
    check(checks, "wellbeing", "short break" in tired.casefold(), tired)
    check(checks, "identity", "You are" in identity or "your name" in identity, identity)

    knowledge = ask(token, "Search your knowledge for Room.")["reply"]
    check(checks, "knowledge_search", "Knowledge results for Room" in knowledge, knowledge)

    windows = ask(token, "What windows are open?")["reply"]
    check(checks, "mac_windows", "Windows:" in windows or "frontmost app" in windows, windows)

    permissions = ask(token, "Check Mac permissions.")["reply"]
    check(checks, "mac_permissions", "Accessibility is" in permissions, permissions)

    ask(token, "Open TextEdit.")
    inspected = ask(token, "Inspect TextEdit.")["reply"]
    check(checks, "mac_inspect", "TextEdit:" in inspected, inspected)

    frontmost = ask(token, "Which app is frontmost?")["reply"]
    check(checks, "mac_frontmost", "frontmost app is" in frontmost, frontmost)

    running_apps = ask(token, "List running apps.")["reply"]
    check(checks, "mac_apps", "Running apps:" in running_apps, running_apps)

    found = ask(token, "Find bold in TextEdit.")["reply"]
    check(checks, "mac_find", "Matches in TextEdit" in found, found)

    ocr = ask(token, "Read text in TextEdit.")["reply"]
    check(checks, "mac_ocr", "Text in TextEdit" in ocr, ocr)

    screenshot = ask(token, "Take a screenshot.")["reply"]
    check(checks, "mac_screenshot", "Screenshot saved to" in screenshot, screenshot)

    calendar = ask(token, "What's on my calendar?")["reply"]
    check(checks, "native_calendar", "Calendar:" in calendar or "No events" in calendar, calendar)

    native_reminders = ask(token, "Show my Apple reminders.")["reply"]
    check(checks, "native_reminders",
          "Apple Reminders" in native_reminders or "No pending Apple reminders" in native_reminders,
          native_reminders)

    shortcuts = ask(token, "List my shortcuts.")["reply"]
    check(checks, "native_shortcuts", "Shortcuts" in shortcuts, shortcuts)

    app_status = ask(token, "Check app status.")["reply"]
    check(checks, "app_status", "Accessibility" in app_status and "EventKit exports" in app_status,
          app_status)

    app_search = ask(token, "Search my notes for zzzznomatch.")["reply"]
    check(checks, "native_app_search", "No matching" in app_search or "disabled" in app_search,
          app_search)

    activity = ask(token, "Show my recent activity.")["reply"]
    check(checks, "recent_activity", "Recent activity" in activity, activity)
    for app in ("mail", "messages", "contacts"):
        reply = ask(token, f"Search my {app} for zzzznomatch.")["reply"]
        check(checks, f"native_{app}_search",
              "No matching" in reply or "disabled" in reply, reply)
    screenshot_path = ""
    if "Screenshot saved to " in screenshot:
        screenshot_path = screenshot.split("Screenshot saved to ", 1)[1].split(" (", 1)[0]

    vision = ask(token, "What can you see right now?")["reply"]
    check(checks, "camera_handshake", "camera frame" in vision or "can't see" in vision, vision)

    status, voice = post("/v1/voice", {"text": "This must never play."}, token, timeout=30)
    check(checks, "voice_disabled", status == 503 and "wav" not in voice,
          f"HTTP {status} {voice.get('error', '')}")

    audio = subprocess.run(["pgrep", "-fl", "afplay|/usr/bin/say|kokoro"],
                           capture_output=True, text=True)
    check(checks, "no_audio_processes", audio.returncode != 0, audio.stdout.strip())

    if not keep:
        cleanup(stamp, screenshot_path)
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Keep the test reminders, notes and screenshot.")
    args = parser.parse_args()
    result = run(keep=args.keep)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for item in result["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"] + " · " + item["detail"])
        print("OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

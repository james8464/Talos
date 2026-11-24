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
import base64
import json
import re
import subprocess
import sys
import tempfile
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


def cleanup_designs(paths: list[str]) -> None:
    """Delete only the design artifacts this pass created."""
    designs = (ROOT / "state" / "designs").resolve()
    for value in paths:
        path = Path(value).resolve()
        if path.parent != designs:
            continue
        for sibling in designs.glob(path.stem + "*"):
            sibling.unlink(missing_ok=True)


def cleanup(stamp: int, screenshot: str, design_paths: list[str] | None = None,
            web_paths: list[str] | None = None) -> None:
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
    cleanup_designs(design_paths or [])
    shots = (ROOT / "state" / "screenshots").resolve()
    for value in web_paths or []:
        path = Path(value).resolve()
        if path.parent == shots:
            path.unlink(missing_ok=True)


def control_checks(token, checks):
    """Intrusive mouse/keyboard checks; runs only with --control."""
    opened = ask(token, "Open Calculator.")["reply"]
    check(checks, "open_app", "Opened Calculator" in opened, opened)
    front = ask(token, "Which app is in front?")["reply"]
    check(checks, "frontmost_app", "frontmost app is" in front.casefold(), front)
    clicked = ask(token, "Click the 7 button in Calculator.")["reply"]
    check(checks, "control_click", "Clicked 7 in Calculator" in clicked, clicked)
    cleared = ask(token, "Click the All Clear button in Calculator.")["reply"]
    check(checks, "control_clear", "Clicked" in cleared and "Calculator" in cleared, cleared)
    hovered = ask(token, "Hover over the 7 button in Calculator.")["reply"]
    check(checks, "control_hover", "Hovering over" in hovered, hovered)
    scrolled = ask(token, "Scroll down by 2 lines in Calculator.")["reply"]
    check(checks, "control_scroll", "Scrolled down 2 lines" in scrolled, scrolled)
    windows = ask(token, "List Calculator windows.")["reply"]
    check(checks, "window_list", "Calculator" in windows and "Windows" in windows, windows)
    pressed = ask(token, "Press escape in Calculator.")["reply"]
    check(checks, "control_press", "Pressed escape" in pressed, pressed)
    focused = ask(token, "Focus Calculator.")["reply"]
    check(checks, "control_focus", "Focused Calculator" in focused, focused)
    original = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    try:
        ask(token, "Copy talos clipboard check to my clipboard.")
        read = ask(token, "What's on my clipboard?")["reply"]
        check(checks, "clipboard_roundtrip", "talos clipboard check" in read, "round-trip ok")
    finally:
        subprocess.run(["pbcopy"], input=original, text=True)

    move = ask(token, "Move the mouse to 700, 450.")["reply"]
    check(checks, "pointer_move", "Moved the pointer" in move, move)

def run(keep: bool = False, control: bool = False) -> dict:
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

    found = ask(token, "Find all clear in Calculator.")["reply"]
    check(checks, "mac_find", "Matches in Calculator" in found, found)

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
    from talos_core import skills
    skill_path = skills.create(ROOT / "state", "Live pass skill", "Used by the live pass",
                               "Say the live pass skill is loaded.")
    try:
        skill_reply = ask(token, "Use the Live pass skill.")["reply"]
        check(checks, "skill_use", "live pass skill is loaded" in skill_reply, skill_reply)
    finally:
        skill_path.unlink(missing_ok=True)
    cli = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "ask", "Show my recent activity."],
        capture_output=True, text=True, timeout=120,
    )
    check(checks, "cli_ask", cli.returncode == 0 and "Recent activity" in cli.stdout,
          (cli.stdout or cli.stderr)[-250:])

    identity = ask(token, "Who am I?")["reply"]
    check(checks, "identity_recal", identity.startswith("You are ")
          and "don't have" not in identity, "identity resolved" if identity.startswith("You are ")
          else identity)

    email = ask(token, "What is my email address?")["reply"]
    check(checks, "identity_email", "don't have" not in email.casefold(),
          "email resolved" if "don't have" not in email.casefold() else email)
    phone = ask(token, "What is my phone number?")["reply"]
    check(checks, "identity_phone", "don't have" not in phone.casefold(),
          "phone resolved" if "don't have" not in phone.casefold() else phone)

    design_paths: list[str] = []
    poster = ask(token, "Design a poster titled Live Feature Poster.")["reply"]
    if "Poster created: " in poster:
        design_paths.append(poster.split(": ", 1)[1].strip())
    check(checks, "design_poster", "Poster created" in poster, poster)

    document = ask(token, "Create a document titled Live Feature Check.")["reply"]
    if "Pages-compatible document created: " in document:
        design_paths.append(document.split(": ", 1)[1].strip())
    check(checks, "pages_document", "Pages-compatible document created" in document, document)

    box = ask(token, "Design a 3D box 40x30x20 with 2mm walls.")["reply"]
    if "Box source created: " in box:
        design_paths.append(box.split(": ", 1)[1].split(" and rendered")[0].strip())
    check(checks, "design_box", "Box source created" in box, box)

    nameplate = ask(token, "Design a 3D nameplate that says Talos.")["reply"]
    if "Nameplate source created: " in nameplate:
        design_paths.append(nameplate.split(": ", 1)[1].split(" and rendered")[0].strip())
    check(checks, "design_nameplate", "Nameplate source created" in nameplate, nameplate)

    render = ask(token, "Render the last design in Blender.")["reply"]
    if "Blender render created: " in render:
        design_paths.append(render.split(": ", 1)[1].strip())
    check(checks, "blender_render", "Blender render created" in render, render)

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class LocalPage(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b"<html><head><title>Talos Local Page</title></head>"
                    b"<body><p>local browser check</p>"
                    b"<form onsubmit=\"event.preventDefault();document.getElementById('out').textContent='searched '+document.getElementById('q').value\">"
                    b"<input id='q' placeholder='search' oninput=\"document.getElementById('out').textContent=this.value\">"
                    b"<button type='submit'>Search</button></form>"
                    b"<button onclick=\"document.getElementById('out').textContent='clicked ok'\">Run check</button>"
                    b"<p id='out'>waiting</p></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalPage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{server.server_port}/"
    web_paths: list[str] = []
    try:
        page = ask(token, f"Read {page_url}")["reply"]
        check(checks, "browser_read", "Talos Local Page" in page and "local browser check" in page, page)
        shot = ask(token, f"Screenshot {page_url}")["reply"]
        if "Page screenshot saved to " in shot:
            web_paths.append(shot.split("Page screenshot saved to ", 1)[1].strip())
        check(checks, "browser_screenshot", "Page screenshot saved to" in shot, shot)
        clicked_page = ask(token, f"Click Run check on {page_url}")["reply"]
        check(checks, "browser_click", "clicked ok" in clicked_page, clicked_page)
        typed_page = ask(token, f"Type talos into the search box on {page_url}")["reply"]
        check(checks, "browser_type", "talos" in typed_page, typed_page)
        searched = ask(token, f"Search talos on {page_url}")["reply"]
        check(checks, "browser_search", "searched talos" in searched, searched)
    finally:
        server.shutdown()

    status, voice_reply = post("/v1/turn", {"text": "Hello Talos, how are you?",
                                             "source": "voice"}, token)
    check(checks, "voice_text_reply",
          status == 200 and bool((voice_reply.get("reply") or "").strip()),
          (voice_reply.get("reply") or "")[:160])

    from PIL import Image
    blank = Path(tempfile.mkdtemp()) / "blank-scene.png"
    Image.new("RGB", (320, 240), (20, 30, 40)).save(blank)
    scene = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "scene", "--file", str(blank)],
        capture_output=True, text=True, timeout=120,
    )
    check(checks, "scene_empty", "empty" in scene.stdout.casefold(),
          (scene.stdout or scene.stderr)[:200])
    blank.unlink(missing_ok=True)
    try:
        import numpy as np
        import soundfile as sf
        ping = Path("/System/Library/Sounds/Ping.aiff")
        if ping.is_file():
            data, rate = sf.read(ping)
            if getattr(data, "ndim", 1) > 1:
                data = data[:, 0]
            gap = np.zeros(int(rate * 0.4))
            repeated = np.concatenate([data, gap] * 8)
            sound_path = Path(tempfile.mkdtemp()) / "ping-long.wav"
            sf.write(sound_path, repeated, rate)
            sound = subprocess.run(
                [sys.executable, "-m", "talos_core.cli", "sound", "--file", str(sound_path)],
                capture_output=True, text=True, timeout=120,
            )
            labels = sound.stdout.casefold()
            check(checks, "sound_classify",
                  sound.returncode == 0 and any(
                      label in labels for label in ("telephone", "beep", "music")),
                  (sound.stdout or sound.stderr)[:200])
            sound_path.unlink(missing_ok=True)
    except ImportError:
        pass

    audio_fd, audio_path = tempfile.mkstemp(suffix=".wav")
    import os as _os
    _os.close(audio_fd)
    synthesis_script = """
import base64, io, sys, wave
from pathlib import Path
from talos_core.room.room_voice import KokoroVoice
voice = KokoroVoice(Path('models/kokoro-room'), 'bm_george')
raw = base64.b64decode(voice.speak('Talos audio synthesis check.'))
with wave.open(io.BytesIO(raw), 'rb') as wav:
    assert wav.getnframes() > 1000 and wav.getframerate() == 24000
Path(sys.argv[1]).write_bytes(raw)
"""
    environment = {**_os.environ, "PYTHONPATH": str(ROOT)}
    environment.pop("TALOS_MUTE", None)
    synthesis = subprocess.run(
        [str(ROOT / "state" / "kokoro-env" / "bin" / "python"), "-c", synthesis_script, audio_path],
        capture_output=True, text=True, timeout=180, env=environment,
    )
    size = Path(audio_path).stat().st_size if Path(audio_path).exists() else 0
    check(checks, "audio_synthesis", synthesis.returncode == 0 and size > 1000,
          (synthesis.stderr or f"{size} bytes")[:200])
    Path(audio_path).unlink(missing_ok=True)

    speech = ask(token, "Say hello out loud.")["reply"]
    check(checks, "speech_blocked", "muted" in speech.casefold(), speech)

    music = ask(token, "Play music.")["reply"]
    check(checks, "music_blocked", "disabled" in music.casefold() or "muted" in music.casefold(),
          music)
    for app in ("mail", "messages", "contacts"):
        reply = ask(token, f"Search my {app} for zzzznomatch.")["reply"]
        check(checks, f"native_{app}_search",
              "No matching" in reply or "disabled" in reply, reply)
    screenshot_path = ""
    if "Screenshot saved to " in screenshot:
        screenshot_path = screenshot.split("Screenshot saved to ", 1)[1].split(" (", 1)[0]

    vision = ask(token, "What can you see right now?")["reply"]
    check(checks, "camera_handshake", "camera frame" in vision or "can't see" in vision, vision)
    from PIL import Image
    empty_frame = Path(tempfile.mkdtemp()) / "empty-room.jpg"
    Image.new("RGB", (320, 240), (20, 30, 40)).save(empty_frame)
    status, presence = post("/v1/turn", {"text": "Is anyone there?",
                                         "source": "text",
                                         "camera_jpeg": base64.b64encode(
                                             empty_frame.read_bytes()).decode()}, token)
    check(checks, "vision_presence",
          status == 200 and "empty" in (presence.get("reply") or "").casefold(),
          (presence.get("reply") or "")[:160])
    empty_frame.unlink(missing_ok=True)

    status, voice = post("/v1/voice", {"text": "This must never play."}, token, timeout=30)
    check(checks, "voice_disabled", status == 503 and "wav" not in voice,
          f"HTTP {status} {voice.get('error', '')}")

    audio = subprocess.run(["pgrep", "-fl", "afplay|/usr/bin/say|kokoro"],
                           capture_output=True, text=True)
    check(checks, "no_audio_processes", audio.returncode != 0, audio.stdout.strip())

    if control:
        control_checks(token, checks)
    if not keep:
        cleanup(stamp, screenshot_path, design_paths, web_paths)
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Keep the test reminders, notes and screenshot.")
    parser.add_argument("--control", action="store_true",
                        help="Run intrusive mouse/keyboard checks (moves/clicks Calculator).")
    args = parser.parse_args()
    result = run(keep=args.keep, control=args.control)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for item in result["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"] + " · " + item["detail"])
        print("OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

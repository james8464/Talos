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
import hashlib
import json
import os
import re
import shutil
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


def get(path: str, token: str, timeout: int = 180) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path, headers={"Authorization": "Bearer " + token}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read() or b"{}")


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
    ask(token, "Open TextEdit.")
    inspected = ask(token, "Inspect TextEdit.")["reply"]
    check(checks, "mac_inspect", "TextEdit:" in inspected, inspected)
    found = ask(token, "Find all clear in Calculator.")["reply"]
    check(checks, "mac_find", "Matches in Calculator" in found, found)
    ocr = ask(token, "Read text in TextEdit.")["reply"]
    check(checks, "mac_ocr", "Text in TextEdit" in ocr, ocr)
    screenshot = ask(token, "Take a screenshot.")["reply"]
    check(checks, "mac_screenshot", "Screenshot saved to" in screenshot, screenshot)

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
    if "Screenshot saved to " in screenshot:
        return screenshot.split("Screenshot saved to ", 1)[1].split(" (", 1)[0]
    return ""

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

    frontmost = ask(token, "Which app is frontmost?")["reply"]
    check(checks, "mac_frontmost", "frontmost app is" in frontmost, frontmost)

    running_apps = ask(token, "List running apps.")["reply"]
    check(checks, "mac_apps", "Running apps:" in running_apps, running_apps)

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
    # The browser checks spawn short-lived headless browsers; close only those
    # known processes so the pass does not leave them behind.
    from talos_core.runtime import processes as process_inventory
    process_inventory.stop_stale(labels={"headless browser"})

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
    status, scene_body = post("/v1/vision/scene", {
        "image": base64.b64encode(blank.read_bytes()).decode()}, token)
    check(checks, "scene_endpoint",
          status == 200 and bool((scene_body.get("scene") or {}).get("empty")),
          (scene_body.get("description") or scene_body.get("error") or "")[:160])
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
            status, sound_body = post("/v1/sound", {
                "audio": base64.b64encode(sound_path.read_bytes()).decode()}, token)
            check(checks, "sound_endpoint",
                  status == 200 and isinstance(sound_body.get("events"), list),
                  (sound_body.get("description") or sound_body.get("error") or "")[:160])
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

    overlay = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "highlight",
         "--x", "500", "--y", "400", "--width", "72", "--height", "44",
         "--label", "safe check", "--hold", "0.2"],
        capture_output=True, text=True, timeout=60,
    )
    check(checks, "highlight_overlay",
          overlay.returncode == 0 and '"ok": true' in overlay.stdout,
          (overlay.stdout or overlay.stderr)[:160])
    proc = subprocess.run([sys.executable, "-m", "talos_core.cli", "processes"],
                          capture_output=True, text=True, timeout=30)
    check(checks, "process_status",
          "room service" in proc.stdout and "audio: 0" in proc.stdout,
          proc.stdout[:200])

    stream = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "stream",
         "Reply with exactly: streaming ready."],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "TALOS_MUTE": "1"},
    )
    stream_text = (stream.stdout or stream.stderr).strip()
    check(checks, "text_stream",
          stream.returncode == 0
          and "streaming" in stream_text.casefold()
          and "ready" in stream_text.casefold()
          and "</think" not in stream_text.casefold(),
          stream_text[:200])

    job_state = Path(tempfile.mkdtemp())
    target = ROOT / "talos_core" / "runtime" / "text_stream.py"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    started = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "codejob", "start",
         "Clarify the first helper comment.", "--file", "talos_core/runtime/text_stream.py",
         "--state", str(job_state), "--root", str(ROOT), "--verify-only", "--budget", "120"],
        capture_output=True, text=True, timeout=30,
    )
    job = json.loads(started.stdout or "{}")
    ran = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "codejob", "run", "--id", job.get("id", "-"),
         "--model", "qwen3:4b", "--state", str(job_state), "--root", str(ROOT)],
        capture_output=True, text=True, timeout=240,
    )
    record = json.loads(ran.stdout or "{}")
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    check(checks, "coding_job",
          started.returncode == 0
          and record.get("status") in {"verified", "declined", "deferred"}
          and after == before
          and (Path(job.get("folder", ".")) / "report.json").is_file(),
          f"{record.get('status')}: {record.get('message', '')}"[:200])
    shutil.rmtree(job_state, ignore_errors=True)

    plan_state = Path(tempfile.mkdtemp())
    plan_targets = [ROOT / "talos_core" / "runtime" / "text_stream.py",
                    ROOT / "talos_core" / "runtime" / "background_work.py"]
    plan_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in plan_targets]
    plan_steps = {"steps": [
        {"id": "first", "target": "talos_core/runtime/text_stream.py",
         "request": "Add one clarifying sentence to the module docstring."},
        {"id": "second", "target": "talos_core/runtime/background_work.py",
         "request": "Add one clarifying sentence to the module docstring.",
         "needs": ["first"]},
    ]}
    plan_start = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "codeplan", "start",
         "--json", json.dumps(plan_steps), "--state", str(plan_state),
         "--root", str(ROOT), "--budget", "120"],
        capture_output=True, text=True, timeout=30,
    )
    plan = json.loads(plan_start.stdout or "{}")
    plan_run = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "codeplan", "run", plan.get("id", "-"),
         "--model", "qwen3:4b", "--state", str(plan_state), "--root", str(ROOT)],
        capture_output=True, text=True, timeout=360,
    )
    record = json.loads(plan_run.stdout or "{}")
    statuses = {step["id"]: step["status"] for step in record.get("steps", [])}
    first_ok = statuses.get("first") in {"verified", "applied"}
    dependency_ok = (statuses.get("second") == "blocked" if not first_ok else
                     statuses.get("second") in {"verified", "applied", "declined",
                                                "deferred", "error"})
    unchanged = [hashlib.sha256(path.read_bytes()).hexdigest() for path in plan_targets] == plan_hashes
    check(checks, "coding_plan",
          plan_start.returncode == 0
          and record.get("status") in {"done", "blocked"}
          and dependency_ok
          and unchanged
          and (Path(plan.get("folder", plan_state)) / "report.json").is_file(),
          f"{record.get('status')}: {statuses}"[:200])
    shutil.rmtree(plan_state, ignore_errors=True)

    from talos_core.self_improvement import coding_jobs

    multi_root = Path(tempfile.mkdtemp())
    (multi_root / "talos_core").mkdir()
    (multi_root / "talos_core" / "__init__.py").write_text("")
    (multi_root / "talos_core" / "alpha.py").write_text("def value():\n    return 1\n")
    (multi_root / "talos_core" / "beta.py").write_text("def label():\n    return 'a'\n")
    (multi_root / "tests").mkdir()
    (multi_root / "tests" / "__init__.py").write_text("")
    multi_state = multi_root / "state"
    multi_plan = coding_jobs.start_plan(multi_state, [{
        "id": "both", "targets": ["talos_core/alpha.py", "talos_core/beta.py"],
        "request": "Update both helpers together."}], root=multi_root)
    regression = """import unittest

from talos_core.alpha import value
from talos_core.beta import label


class CombinedTest(unittest.TestCase):
    def test_both(self):
        self.assertEqual(value(), 2)
        self.assertEqual(label(), "b")
"""
    multi_patches = [
        {"target": "talos_core/alpha.py", "before": "    return 1\n",
         "after": "    return 2\n"},
        {"target": "talos_core/beta.py", "before": "    return 'a'\n",
         "after": "    return 'b'\n"},
    ]
    multi_result = coding_jobs.run_plan(
        multi_state, multi_plan["id"], root=multi_root,
        infer=lambda prompt: "{}",
        infer_many=lambda prompt: json.dumps(
            {"patches": multi_patches, "regression_test": regression}))
    multi_step = multi_result["steps"][0]
    multi_unchanged = ((multi_root / "talos_core" / "alpha.py").read_text()
                       == "def value():\n    return 1\n"
                       and (multi_root / "talos_core" / "beta.py").read_text()
                       == "def label():\n    return 'a'\n")
    check(checks, "coding_plan_multi",
          multi_result.get("status") == "done"
          and multi_step.get("status") == "verified"
          and multi_unchanged
          and set(multi_step.get("targets", [])) == {"talos_core/alpha.py",
                                                     "talos_core/beta.py"},
          f"{multi_result.get('status')}: {multi_step.get('status')} "
          f"{multi_step.get('message', '')}"[:200])
    shutil.rmtree(multi_root, ignore_errors=True)

    retry_root = Path(tempfile.mkdtemp())
    (retry_root / "talos_core").mkdir()
    (retry_root / "talos_core" / "__init__.py").write_text("")
    (retry_root / "talos_core" / "gamma.py").write_text("def total():\n    return 1\n")
    (retry_root / "tests").mkdir()
    (retry_root / "tests" / "__init__.py").write_text("")
    retry_state = retry_root / "state"
    retry_job = coding_jobs.start(retry_state, "Return two.", "talos_core/gamma.py",
                                  root=retry_root, apply=False)
    failing = """import unittest

from talos_core.gamma import total


class TotalTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total(), 3)
"""
    passing = failing.replace("(total(), 3)", "(total(), 2)")
    retry_prompts = []

    def retry_infer(prompt):
        retry_prompts.append(prompt)
        return json.dumps({"before": "    return 1\n", "after": "    return 2\n",
                           "regression_test": failing if len(retry_prompts) == 1 else passing})

    retry_result = coding_jobs.run(retry_state, retry_job["id"], root=retry_root,
                                   infer=retry_infer)
    check(checks, "coding_retry",
          retry_result.get("status") == "verified"
          and retry_result.get("attempts") == 2
          and len(retry_prompts) == 2
          and "failed sandbox unit tests" in retry_prompts[1]
          and (retry_root / "talos_core" / "gamma.py").read_text()
          == "def total():\n    return 1\n",
          f"{retry_result.get('status')} after {retry_result.get('attempts')} attempts")
    shutil.rmtree(retry_root, ignore_errors=True)

    repair_root = Path(tempfile.mkdtemp())
    (repair_root / "talos_core").mkdir()
    (repair_root / "talos_core" / "__init__.py").write_text("")
    (repair_root / "talos_core" / "delta.py").write_text("def flag():\n    return 0\n")
    (repair_root / "talos_core" / "epsilon.py").write_text("def word():\n    return 'x'\n")
    (repair_root / "tests").mkdir()
    (repair_root / "tests" / "__init__.py").write_text("")
    repair_state = repair_root / "state"
    repair_plan = coding_jobs.start_plan(repair_state, [
        {"id": "bad", "target": "talos_core/delta.py", "request": "first"},
        {"id": "after", "target": "talos_core/epsilon.py", "request": "second",
         "needs": ["bad"]},
    ], root=repair_root)
    repair_regression = """import unittest

from talos_core.epsilon import word


class WordTest(unittest.TestCase):
    def test_word(self):
        self.assertEqual(word(), "y")
"""

    def repair_infer(prompt):
        if "TARGET: talos_core/delta.py" in prompt:
            return "{}"
        return json.dumps({"before": "    return 'x'\n", "after": "    return 'y'\n",
                           "regression_test": repair_regression})

    repair_calls = []

    def plan_repair(failed, completed, error):
        repair_calls.append(failed["id"])
        return [{"id": "fixed", "targets": ["talos_core/epsilon.py"],
                 "request": "second"}]

    repair_result = coding_jobs.run_plan(
        repair_state, repair_plan["id"], root=repair_root,
        infer=repair_infer, repair=plan_repair)
    repair_steps = {step["id"]: step for step in repair_result["steps"]}
    check(checks, "coding_plan_repair",
          repair_result.get("status") == "done"
          and repair_result.get("revision") == 1
          and repair_calls == ["bad"]
          and repair_steps.get("bad", {}).get("status") == "superseded"
          and repair_steps.get("fixed", {}).get("status") == "verified"
          and repair_steps.get("after") is None
          and (repair_root / "talos_core" / "delta.py").read_text()
          == "def flag():\n    return 0\n",
          f"{repair_result.get('status')}: revision {repair_result.get('revision')} "
          f"{repair_result.get('repair')}"[:200])
    twice_plan = coding_jobs.start_plan(repair_state, [
        {"id": "bad2", "target": "talos_core/delta.py", "request": "first"}],
        root=repair_root)
    twice_calls = []

    def twice_repair(failed, completed, error):
        twice_calls.append(failed["id"])
        return [{"id": f"retry-{len(twice_calls)}",
                 "targets": ["talos_core/epsilon.py"], "request": "retry"}]

    twice_result = coding_jobs.run_plan(
        repair_state, twice_plan["id"], root=repair_root,
        infer=lambda prompt: "{}", repair=twice_repair)
    twice_statuses = {step["id"]: step["status"] for step in twice_result["steps"]}
    check(checks, "coding_plan_repair_twice",
          twice_result.get("status") == "blocked"
          and twice_result.get("revision") == 2
          and twice_calls == ["bad2", "retry-1"]
          and twice_statuses == {"bad2": "superseded", "retry-1": "superseded",
                                 "retry-2": "declined"}
          and twice_result.get("remaining_budget", -1) >= 0
          and (repair_root / "talos_core" / "delta.py").read_text()
          == "def flag():\n    return 0\n",
          f"revision {twice_result.get('revision')}: {twice_statuses} "
          f"budget {twice_result.get('remaining_budget')}"[:200])

    shutil.rmtree(repair_root, ignore_errors=True)

    from talos_core.self_improvement import plan_generator

    catalog_root = Path(tempfile.mkdtemp())
    (catalog_root / "talos_core").mkdir()
    (catalog_root / "talos_core" / "__init__.py").write_text("")
    (catalog_root / "talos_core" / "safe.py").write_text("VALUE = 1\n")
    (catalog_root / "state").mkdir()
    private = catalog_root / "state" / "private.py"
    private.write_text("SECRET = 1\n")
    outside = json.dumps({"steps": [{"id": "bad", "targets": ["state/private.py"],
                                     "request": "read the private store"}]})
    try:
        plan_generator.generate("touch private state", root=catalog_root,
                                infer=lambda prompt: outside, attempts=1)
        catalog_blocked = False
        catalog_detail = "generator accepted an out-of-catalog target"
    except ValueError as error:
        catalog_blocked = "not in the source catalog" in str(error)
        catalog_detail = str(error)[:200]
    check(checks, "plan_catalog_guard",
          catalog_blocked and private.read_text() == "SECRET = 1\n",
          catalog_detail)
    shutil.rmtree(catalog_root, ignore_errors=True)

    cli_root = Path(tempfile.mkdtemp())
    cli_state = cli_root / "state"
    (cli_root / "a.py").write_text("A = 1\n")
    (cli_root / "b.py").write_text("B = 2\n")
    cli_script = r"""
import io, json, sys
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from talos_core import cli

root, state = Path(sys.argv[1]), Path(sys.argv[2])
steps = {"steps": [
    {"id": "bad", "target": "a.py", "request": "first"},
    {"id": "after", "target": "b.py", "request": "second", "needs": ["bad"]},
]}
start_out, run_out = io.StringIO(), io.StringIO()
calls = {"n": 0}

def revise(*args, **kwargs):
    calls["n"] += 1
    return {"steps": [{"id": f"fixed-{calls['n']}", "targets": ["b.py"],
                       "request": "second"}]}

with patch("talos_core.self_improvement.plan_generator.repair",
           side_effect=revise) as repair, \
        patch("talos_core.self_improvement.initiative_coder.local_infer",
              return_value=lambda prompt: "{}"):
    with redirect_stdout(start_out):
        start = cli.main(["codeplan", "start", "--json", json.dumps(steps),
                          "--request", "goal", "--root", str(root), "--state", str(state)])
    plan = json.loads(start_out.getvalue())
    with redirect_stdout(run_out):
        run = cli.main(["codeplan", "run", plan["id"], "--root", str(root),
                        "--state", str(state), "--model", "test"])
    result = json.loads(run_out.getvalue())
print(json.dumps({"start": start, "run": run, "result": result,
                  "repair_calls": repair.call_count}))
"""
    cli_repair = subprocess.run(
        [sys.executable, "-c", cli_script, str(cli_root), str(cli_state)],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT), "TALOS_MUTE": "1"},
    )
    try:
        outcome = json.loads(cli_repair.stdout or "{}")
    except ValueError:
        outcome = {}
    result = outcome.get("result", {})
    statuses = {step["id"]: step["status"] for step in result.get("steps", [])}
    check(checks, "coding_plan_cli_repair",
          outcome.get("start") == 0
          and result.get("revision") == 2
          and result.get("repairs", [{}])[0].get("failed") == "bad"
          and result.get("repair", {}).get("failed") == "fixed-1"
          and statuses.get("bad") == "superseded"
          and statuses.get("fixed-1") == "superseded"
          and statuses.get("fixed-2") == "declined"
          and outcome.get("repair_calls") == 2
          and (cli_root / "a.py").read_text() == "A = 1\n"
          and (cli_root / "b.py").read_text() == "B = 2\n",
          f"revision {result.get('revision')}: {statuses} "
          f"calls {outcome.get('repair_calls')}"[:200])
    shutil.rmtree(cli_root, ignore_errors=True)

    av = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "av-readiness", "--json"],
        capture_output=True, text=True, timeout=180,
    )
    try:
        av_local = json.loads(av.stdout or "{}")
    except ValueError:
        av_local = {}
    av_status, av_body = get("/v1/av/readiness", token)
    av_remote = av_body.get("readiness", {})
    permissions = {"authorized", "denied", "restricted", "not_determined", "unknown"}
    check(checks, "av_readiness",
          av.returncode == 0
          and av_local.get("camera_permission") in permissions
          and av_local.get("microphone_permission") in permissions
          and av_local.get("capture_started") is False
          and av_local.get("recording_started") is False
          and av_status == 200
          and av_remote.get("capture_started") is False
          and av_remote.get("recording_started") is False,
          f"cli {av_local.get('camera_permission')}/{av_local.get('microphone_permission')} "
          f"devices {av_local.get('camera_device')}/{av_local.get('microphone_device')} "
          f"endpoint {av_status}")

    guard = subprocess.run(
        [sys.executable, "-m", "talos_core.cli", "capture-check", "--state", str(ROOT / "state")],
        capture_output=True, text=True, timeout=60,
    )
    check(checks, "capture_consent_guard",
          guard.returncode == 2
          and "consent" in guard.stderr.casefold()
          and not list((ROOT / "state").glob(".capture-*")),
          (guard.stderr or guard.stdout).strip()[:200])

    speech = ask(token, "Say hello out loud.")["reply"]
    check(checks, "speech_blocked", "muted" in speech.casefold(), speech)

    introduced = ask(token, "My name is Ada.")["reply"]
    identity_claim = ask(token, "Do you recognize me?")["reply"]
    check(checks, "identity_self_report",
          "nice to meet you, ada" in introduced.casefold()
          and "introduced yourself as ada" in identity_claim.casefold()
          and "self-reported" in identity_claim.casefold()
          and "can't verify" in identity_claim.casefold(),
          identity_claim[:200])

    forgotten = ask(token, "Forget my name.")["reply"]
    after_forget = ask(token, "Do you recognize me?")["reply"]
    check(checks, "identity_forget",
          "forgot" in forgotten.casefold()
          and "haven't confirmed" in after_forget.casefold(),
          after_forget[:200])

    capture_photo = ask(token, "Can you take a photo?")["reply"]
    capture_mic = ask(token, "Can you record my voice?")["reply"]
    check(checks, "capture_guidance",
          "consent" in capture_photo.casefold()
          and "capture-check" in capture_photo
          and "consent" in capture_mic.casefold()
          and "capture-check" in capture_mic,
          capture_photo[:200])

    capability = ask(token, "In one short sentence, what can you run in the background?")["reply"]
    check(checks, "capability_reply",
          "muted" in capability.casefold() and "can't play" in capability.casefold(),
          capability)

    room_coding = ask(token, "coding job talos_core/runtime/text_stream.py: "
                             "Add one clarifying sentence to the module docstring.")["reply"]
    check(checks, "room_coding_job",
          ("verified" in room_coding.casefold() or "declined" in room_coding.casefold())
          and "target unchanged: yes" in room_coding.casefold(),
          room_coding)

    room_plan = ask(token, "coding plan: add a clarifying sentence to the module "
                           "docstring in talos_core/runtime/text_stream.py")["reply"]
    check(checks, "room_coding_plan",
          "coding plan" in room_plan.casefold()
          and ("done" in room_plan.casefold() or "blocked" in room_plan.casefold())
          and "targets unchanged: yes" in room_plan.casefold(),
          room_plan[:240])

    music = ask(token, "Play music.")["reply"]
    check(checks, "music_blocked", "disabled" in music.casefold() or "muted" in music.casefold(),
          music)
    for app in ("mail", "messages", "contacts"):
        reply = ask(token, f"Search my {app} for zzzznomatch.")["reply"]
        check(checks, f"native_{app}_search",
              "No matching" in reply or "disabled" in reply, reply)
    screenshot_path = ""

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
        screenshot_path = control_checks(token, checks) or ""
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

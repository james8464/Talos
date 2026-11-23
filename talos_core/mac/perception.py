"""Screen perception and element targeting for unfamiliar apps.

This is a thin adapter over the MIT-licensed ``macos-computer-use-kit``:
Accessibility refs, set-of-mark screenshots, on-device OCR, background clicks
and verified AX actions. The kit discovers buttons, fields and menus for apps
Talos has never seen; this module turns its JSON into compact, model-readable
candidates and safe actions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class KitUnavailable(RuntimeError):
    pass


class KitError(RuntimeError):
    pass


def command() -> list[str]:
    """Locate the macos-cu CLI, preferring the project environment."""
    explicit = os.environ.get("TALOS_MACOS_CU")
    if explicit:
        return [explicit]
    local = ROOT / ".venv" / "bin" / "macos-cu"
    if local.is_file():
        return [str(local)]
    found = shutil.which("macos-cu")
    if found:
        return [found]
    raise KitUnavailable(
        "Install the perception extra: pip install 'talos-assistant[control]'"
    )


def run(*args: str, timeout: int = 30,
        runner: Callable = subprocess.run) -> dict:
    argv = [*command(), *args]
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise KitError(f"macos-cu failed: {error}") from error
    text = (completed.stdout or "").strip()
    if not text:
        raise KitError((completed.stderr or "empty macos-cu response").strip()[:500])
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise KitError(text[:500]) from error
    if completed.returncode not in (0, 3):
        hint = payload.get("hint") if isinstance(payload, dict) else None
        raise KitError(f"macos-cu exit {completed.returncode}: {hint or text[:300]}")
    return payload if isinstance(payload, dict) else {"result": payload}


def available() -> bool:
    try:
        command()
        return True
    except KitUnavailable:
        return False


def snapshot(app: str, *, interactive: bool = True, budget: int = 4000,
             runner: Callable = subprocess.run) -> dict:
    args = ["ax", "snapshot", "--app", app, "--json", "--budget", str(budget)]
    if interactive:
        args.append("--interactive")
    return run(*args, runner=runner)


def annotate(app: str, out: Path | None = None, *,
             runner: Callable = subprocess.run) -> dict:
    destination = Path(out) if out else Path(tempfile.gettempdir()) / f"talos-marks-{os.getpid()}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    return run("shot", "annotate", "--app", app, "--out", str(destination), runner=runner)


def ocr(app: str, query: str | None = None, *,
        runner: Callable = subprocess.run, timeout: int = 120) -> dict:
    args = ["ocr", "--app", app]
    if query:
        args += ["--text", query]
    return run(*args, runner=runner, timeout=timeout)


def elements(snapshot_payload: dict) -> list[dict]:
    return list(snapshot_payload.get("elements") or ())


def describe(payload: dict, *, limit: int = 40) -> str:
    """Compact, model-readable list of actionable elements."""
    lines = []
    for element in elements(payload)[:limit]:
        label = (element.get("title") or element.get("desc") or element.get("value") or "")
        label = re.sub(r"\s+", " ", str(label))[:80]
        center = element.get("center_screen") or [0, 0]
        flags = []
        if element.get("subrole"):
            flags.append(str(element["subrole"]))
        if element.get("identifier"):
            flags.append(str(element["identifier"]))
        suffix = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"#{element.get('ref', '?')} {element.get('role', '?')} \"{label}\" "
            f"@screen[{center[0]},{center[1]}]{suffix}"
        )
    return "\n".join(lines)


def find(app: str, query: str, *, limit: int = 8,
         runner: Callable = subprocess.run) -> list[dict]:
    """Rank visible controls by how well their label/role matches ``query``."""
    payload = snapshot(app, runner=runner)
    wanted = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for element in elements(payload):
        label = " ".join(str(element.get(key) or "") for key in
                         ("title", "desc", "value", "role", "identifier"))
        words = set(re.findall(r"[a-z0-9]+", label.lower()))
        overlap = len(wanted & words)
        if not overlap:
            continue
        role = str(element.get("role", ""))
        clickable = 1 if any(word in role.lower() for word in ("button", "menu", "link", "tab")) else 0
        center = element.get("center_screen") or [0, 0]
        scored.append({
            "score": overlap * 3 + clickable,
            "ref": element.get("ref"),
            "role": role,
            "label": (element.get("title") or element.get("desc") or element.get("value") or "")[:100],
            "center": center,
            "subrole": element.get("subrole"),
        })
    scored.sort(key=lambda item: (-item["score"], str(item["label"])))
    return scored[:limit]


def click_ref(app: str, ref: str, *, runner: Callable = subprocess.run) -> dict:
    """Click an element by ref. AXPress first; coordinate click as fallback.

    A press that sends but does not change observable state is retried as a
    process-targeted coordinate click, which is what custom-drawn buttons need.
    """
    result = None
    try:
        result = run("ax", "press", "--app", app, "--ref", ref, runner=runner)
        if result.get("verified") is not False and result.get("state_changed") is not False:
            return result
    except KitError:
        result = None
    payload = snapshot(app, runner=runner)
    match = next((element for element in elements(payload)
                  if str(element.get("ref")) == str(ref)), None)
    if match is None:
        raise KitError(f"Element ref {ref!r} is not visible in {app}.")
    center = match.get("center_screen") or [0, 0]
    clicked = run("input", "click", "--app", app, "--x", str(int(center[0])),
                  "--y", str(int(center[1])), "--show", runner=runner)
    if result is not None:
        clicked.setdefault("ax_press", result)
    return clicked


def type_into_ref(app: str, ref: str, text: str, *,
                  runner: Callable = subprocess.run) -> dict:
    """Set a field's value directly, falling back to focus + background typing."""
    try:
        return run("ax", "setvalue", "--app", app, "--ref", ref, "--text", text, runner=runner)
    except KitError:
        run("ax", "focus", "--app", app, "--ref", ref, runner=runner)
        return run("input", "type", "--app", app, "--text", text, runner=runner)


def _vision_ref(goal: str, marks: list[dict], screenshot: str | None,
                elements: list[dict], model: str) -> str | None:
    """Ask the local vision model which numbered mark matches the goal."""
    if not screenshot or not marks:
        return None
    from io import BytesIO

    from PIL import Image

    from talos_core.room.room_vision import describe_image

    image = Image.open(screenshot).convert("RGB")
    image.thumbnail((1800, 1800))
    output = BytesIO()
    image.save(output, "JPEG", quality=85)
    options = "\n".join(
        f"{item['mark']}. {item.get('role', '?')} {item.get('label', '')}".strip()
        for item in marks
    )
    prompt = (f"Goal: {goal}\nVisible controls:\n{options}\n"
              "Reply with only the number of the control that best accomplishes the goal.")
    seen = describe_image(prompt, __import__("base64").b64encode(output.getvalue()).decode())
    match = re.search(r"\b(\d{1,2})\b", seen or "")
    if not match:
        return None
    number = int(match.group(1))
    selected = next((item for item in marks if int(item.get("mark", 0)) == number), None)
    return str(selected.get("ref")) if selected else None


def choose(app: str, goal: str, *, decider: Callable | None = None,
           model: str | None = None, runner: Callable = subprocess.run) -> dict:
    """Pick a clickable target for ``goal``, optionally using a vision model.

    ``decider(goal, marks, elements, screenshot)`` returns either a ref or a
    dict ``{"ref": ...}``. Without a decider, labels are matched lexically.
    """
    payload = snapshot(app, runner=runner)
    candidates = elements(payload)
    screenshot = None
    try:
        marks = annotate(app, runner=runner)
        screenshot = marks.get("path")
    except KitError:
        marks = {}
    if decider is None and model:
        ref = _vision_ref(goal, marks.get("marks", []), screenshot, candidates, model)
        if ref:
            def decider(*_):
                return ref
    if decider is not None:
        choice = decider(goal, marks.get("marks", ()), candidates, screenshot)
        ref = choice.get("ref") if isinstance(choice, dict) else choice
        if ref:
            selected = next((element for element in candidates
                             if str(element.get("ref")) == str(ref)), None)
            if selected:
                center = selected.get("center_screen") or [0, 0]
                return {"ref": ref, "role": selected.get("role"),
                        "label": selected.get("title") or selected.get("desc") or "",
                        "center": center, "screenshot": screenshot,
                        "reason": choice.get("reason", "") if isinstance(choice, dict) else "",
                        "source": "model"}
    matches = find(app, goal, runner=runner)
    if not matches:
        raise KitError(f"No visible control in {app} matches {goal!r}.")
    best = matches[0]
    return {**best, "screenshot": screenshot, "source": "labels"}


def stats() -> dict:
    return {"available": available(), "command": command() if available() else None}

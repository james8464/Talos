"""Unified GUI actions and vision-assisted element targeting.

The action vocabulary follows OpenCowork's ``gui_interact`` /
``gui_interact_vision`` tools (MIT, Copyright (c) 2026 OpenCoworkAI): click,
double-click, right-click, move, type, key, drag, screenshot, and wait, plus
natural-language element targeting.  The implementation delegates to Talos's
existing accessibility layer and :class:`ComputerUse`, so it inherits receipts,
PID-targeted input, and permission checks instead of shelling out to cliclick.
"""

from __future__ import annotations

import re
import time

from talos_core.mac.computer_use import ComputerUse, ComputerUseError

ACTIONS = ("click", "double_click", "right_click", "move", "type", "key", "drag",
           "screenshot", "wait")
MAX_WAIT = 10.0


def _required(value, name):
    if value is None or value == "":
        raise ValueError(f"{name} is required for this action.")
    return value


def act(computer: ComputerUse, action: str, *, x=None, y=None, value=None,
        app=None, confirm=False, timeout=MAX_WAIT) -> dict:
    """Run one GUI action and return a JSON-friendly result."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown GUI action {action!r}; use one of {', '.join(ACTIONS)}.")
    if action in ("click", "double_click", "right_click"):
        x, y = int(_required(x, "x")), int(_required(y, "y"))
        if action == "click":
            return computer.click(x, y, app=app).to_dict()
        if action == "double_click":
            return computer.click(x, y, count=2, app=app).to_dict()
        return computer.right_click(x, y, app=app, confirm=confirm).to_dict()
    if action == "move":
        x, y = int(_required(x, "x")), int(_required(y, "y"))
        return computer.move(x, y, app=app).to_dict()
    if action == "type":
        return computer.type_text(str(_required(value, "value")), app=app).to_dict()
    if action == "key":
        return computer.press(str(_required(value, "value")), confirm=confirm,
                              app=app).to_dict()
    if action == "drag":
        parts = str(_required(value, "value")).replace(" ", "").split(",")
        if len(parts) != 4:
            raise ValueError("Drag value must be 'x1,y1,x2,y2'.")
        x1, y1, x2, y2 = (int(part) for part in parts)
        return computer.drag(x1, y1, x2, y2, app=app).to_dict()
    if action == "screenshot":
        return computer.screenshot().to_dict()
    seconds = min(max(float(value if value is not None else 1.0), 0.0), MAX_WAIT)
    time.sleep(seconds)
    return {"action": "wait", "ok": True, "seconds": seconds}


def target(computer: ComputerUse, app: str, action: str, description: str,
           *, value=None, confirm=False) -> dict:
    """Locate an element by intent, then act on it.

    Uses the accessibility tree and Talos's optional local vision model.  A
    click lands on the element's screen centre; typing focuses the element
    first.  Nothing is executed when no control matches.
    """
    if action not in ("click", "double_click", "right_click", "hover", "type"):
        raise ValueError("Vision targeting supports click, double-click, right-click, "
                         "hover, and type.")
    try:
        choice = computer.choose(app, description)
    except (OSError, RuntimeError, ValueError):
        choice = {}
    source = "accessibility"
    if not choice or not choice.get("ref"):
        matches = _ocr_matches(computer, app, description)
        if not matches:
            return {"ok": False, "app": app, "description": description,
                    "error": "No matching control was found."}
        match = matches[0]
        choice = {"ref": None, "center": match.get("center_screen") or [0, 0],
                  "label": match.get("text", ""), "source": "ocr"}
        source = "ocr"
    center = choice.get("center") or [0, 0]
    x, y = int(center[0]), int(center[1])
    if action == "type":
        if source == "ocr":
            _activate(computer, app)
            computer.click(x, y)
            typed = computer.type_text(str(_required(value, "value")))
            result = {"click": None, "type": typed}
        else:
            clicked = computer.click_ref(app, choice["ref"])
            typed = computer.type_ref(app, choice["ref"], str(_required(value, "value")))
            result = {"click": clicked, "type": typed}
    elif action == "double_click":
        result = computer.click(x, y, count=2, app=app).to_dict()
    elif action == "right_click":
        result = computer.right_click(x, y, app=app, confirm=confirm).to_dict()
    elif action == "hover":
        result = computer.hover(x, y, app=app).to_dict()
    elif source == "ocr":
        _activate(computer, app)
        if action == "double_click":
            result = computer.click(x, y, count=2).to_dict()
        elif action == "right_click":
            result = computer.right_click(x, y, confirm=confirm).to_dict()
        elif action == "hover":
            result = computer.hover(x, y).to_dict()
        else:
            result = computer.click(x, y).to_dict()
    else:
        result = computer.click_ref(app, choice["ref"])
    return {"ok": True, "app": app, "description": description,
            "ref": choice.get("ref"), "label": choice.get("label", ""),
            "center": [x, y], "action": action, "source": source, "result": result}


def _activate(computer, app: str) -> None:
    activate = getattr(computer, "activate", None)
    if callable(activate):
        try:
            activate(app)
        except (OSError, RuntimeError, ValueError):
            pass


def _ocr_matches(computer, app: str, description: str) -> list[dict]:
    """Look for visible text when the accessibility tree has no control."""
    candidates = [description]
    stripped = re.sub(r"^(?:the|a|an)\s+", "", description.strip(), flags=re.I)
    stripped = re.sub(r"\s+(?:button|field|menu|item|tab|checkbox|link)$", "",
                      stripped, flags=re.I)
    if stripped and stripped.casefold() != description.casefold():
        candidates.append(stripped)
    for query in candidates:
        try:
            found = computer.ocr(app, query)
        except (OSError, RuntimeError, ValueError):
            continue
        matches = (found or {}).get("matches") or []
        if matches:
            return matches
    return []


def verify(computer: ComputerUse, app: str, question: str) -> str:
    """Ask the local vision model a question about the app's window."""
    from talos_core.room.room_vision import describe_image

    image = computer.window_jpeg(app)
    if not image:
        raise ComputerUseError(f"No window image is available for {app}.")
    return describe_image(question, image)


def image_point_to_screen(x, y, image_width, image_height, bounds=None):
    """Map screenshot pixels to logical screen points (Retina-aware)."""
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Image dimensions must be positive.")
    if bounds is None:
        try:
            import Quartz
        except ImportError as error:
            raise ComputerUseError("Coordinate mapping needs pyobjc-framework-Quartz.") from error
        display = Quartz.CGMainDisplayID()
        rect = Quartz.CGDisplayBounds(display)
        bounds = (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
    left, top, width, height = bounds
    scale_x = image_width / width
    scale_y = image_height / height
    return round(left + x / scale_x), round(top + y / scale_y)

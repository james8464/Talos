"""Harmless real-world computer-use check.

Opens TextEdit, creates a new document, types a marker, asks the local vision
model to read the screen, and closes the document without saving. Every step
passes through :class:`ComputerUseLoop`, so this exercises the same
observe → decide → act path an autonomous agent uses.

Run:
    python3 -m tools.computer_use_demo
    python3 -m tools.computer_use_demo --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from talos_core.mac.computer_use import ComputerUse, ComputerUseLoop
from talos_core.room.room_vision import describe_image


def make_decider(computer: ComputerUse, marker: str, log: list[dict], original: str):
    state = {"typed": False, "vision": "", "attempts": 0}

    def decide(goal: str, observation: dict, history: list[dict]) -> dict:
        if not history:
            return {"action": "activate", "app": "TextEdit"}
        if len(history) == 1:
            return {"action": "menu", "app": "TextEdit", "items": ["File", "New"]}
        if len(history) == 2:
            bounds = computer.window_bounds("TextEdit")
            if bounds:
                x, y, width, height = bounds
                return {"action": "click", "x": x + width // 2, "y": y + height // 2,
                        "app": "TextEdit"}
        if len(history) == 3:
            # Deliberately give focus back to the user's app. Typing must still
            # land in TextEdit because events are addressed to its process id.
            return {"action": "activate", "app": original}
        if not state["typed"]:
            state["typed"] = True
            state["attempts"] += 1
            return {"action": "type", "text": marker, "app": "TextEdit"}
        # Vision reads TextEdit's own window, even while it is backgrounded.
        seen = describe_image(
            "Read the text in the document window. Reply with only the visible text, "
            "no commentary.",
            computer.window_jpeg("TextEdit"),
        )
        state["vision"] = seen
        log.append({"vision": seen, "attempt": state["attempts"],
                    "frontmost": computer.frontmost().get("app", "")})
        if marker.casefold() in seen.casefold() or "TALOS" in seen.upper():
            return {"action": "done"}
        if state["attempts"] >= 2:
            return {"action": "screenshot"}
        state["typed"] = False
        return {"action": "key", "combo": "cmd+a", "app": "TextEdit"}

    return decide


def close_untitled_document() -> None:
    script = 'tell application "TextEdit" to close front document saving no'
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", default=f"TALOS ULTRON CHECK {os.getpid()}")
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    computer = ComputerUse(args.state, cursor=True)
    original = computer.frontmost().get("app", "Finder")
    log: list[dict] = []
    started = time.time()
    result = {"ok": False}
    try:
        loop = ComputerUseLoop(computer, max_steps=10)
        result = loop.run("Type a marker into a new TextEdit document and verify it visually",
                          make_decider(computer, args.marker, log, original))
        result["marker"] = args.marker
        result["vision"] = log[-1]["vision"] if log else ""
        result["frontmost_at_vision"] = log[-1].get("frontmost") if log else ""
        result["elapsed_s"] = round(time.time() - started, 1)
    finally:
        close_untitled_document()
        try:
            computer.activate(original)
        except (OSError, ValueError, RuntimeError):
            pass
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"ok={result.get('ok')} steps={result.get('steps')} elapsed={result.get('elapsed_s')}s")
        for item in result.get("history", ()):
            print(f"  {item['step']}. {item['action']} ok={item['ok']}")
        print("vision:", (result.get("vision") or "").replace("\n", " ")[:300])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

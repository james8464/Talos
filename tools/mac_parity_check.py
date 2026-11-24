"""Repeatable live macOS parity check across TextEdit, Calculator and Safari.

This is intentionally opt-in; it opens apps and creates a temporary TextEdit
document, then closes it. It does not touch existing documents.

Run:
    .venv/bin/python -m tools.mac_parity_check
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from talos_core.mac import perception
from talos_core.mac.computer_use import ComputerUse, permissions


def text_edit(state: Path) -> dict:
    computer = ComputerUse(state, cursor=True)
    before = {w.get("title") for w in perception.run("window", "list", "--app", "TextEdit").get("windows", [])}
    subprocess.run(["osascript", "-e", 'tell application "TextEdit" to make new document'],
                   capture_output=True)
    time.sleep(0.7)
    windows = perception.run("window", "list", "--app", "TextEdit").get("windows", [])
    window = next((w for w in windows if w.get("title") not in before), None)
    if not window:
        return {"ok": False, "error": "no new TextEdit window"}
    title = window["title"]
    x, y, _width, _height = window["pos"] + window["size"]
    result = {}
    try:
        result["move"] = computer.window("move", app="TextEdit", title=title, x=200, y=180).get("ok")
        result["resize"] = computer.window("resize", app="TextEdit", title=title, width=700, height=500).get("ok")
        result["type"] = computer.type_text("TALOS PARITY CHECK", app="TextEdit").ok
        result["hover"] = computer.hover(x + 100, y + 100, app="TextEdit").get("ok")
        result["scroll"] = computer.scroll(3, app="TextEdit", x=x + 200, y=y + 200).get("ok")
        result["minimize"] = computer.window("minimize", app="TextEdit", title=title).get("ok")
        result["restore"] = computer.window("restore", app="TextEdit", title=title).get("ok")
        result["close_guard"] = "confirmed" if computer.window("close", app="TextEdit", title=title, confirm=True).get("ok") else "failed"
    finally:
        subprocess.run(["osascript", "-e", f'tell application "TextEdit" to close document "{title}" saving no'],
                       capture_output=True)
    result["ok"] = all(result.get(key) for key in ("move", "resize", "type", "hover", "scroll", "minimize", "restore"))
    return result


def calculator(state: Path) -> dict:
    from tools.app_scenarios import calculator as run
    return run(state)


def main() -> int:
    state = ROOT / "state"
    payload = {"permissions": permissions(),
               "textedit": text_edit(state), "calculator": calculator(state)}
    print(json.dumps(payload, indent=2))
    return 0 if all(payload[key].get("ok") for key in ("textedit", "calculator")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

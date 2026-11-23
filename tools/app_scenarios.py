"""Repeatable live checks against real macOS apps.

Run:
    python3 -m tools.app_scenarios calculator
    python3 -m tools.app_scenarios safari

Uses the same ``ComputerUse`` surface the planner uses. All actions are
background-posted; only the app under test receives input.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from talos_core.mac.computer_use import ComputerUse


def running(name: str) -> bool:
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def calculator(state: Path) -> dict:
    launched = not running("Calculator")
    if launched:
        subprocess.run(["open", "-a", "Calculator"], check=True)
    time.sleep(1.0)
    computer = ComputerUse(state, cursor=True)
    steps = []
    try:
        for goal in ("All Clear", "7", "Add", "8", "Equals"):
            choice = computer.choose("Calculator", goal)
            result = computer.click_ref("Calculator", choice["ref"])
            steps.append({"goal": goal, "ref": choice["ref"], "verified": result.get("verified")})
            time.sleep(0.2)
        time.sleep(0.6)
        view = computer.see("Calculator", interactive=False)
        elements = [item for item in view["elements"] if item.get("value") is not None]
        display = next(
            (str(item["value"]) for item in elements if item.get("desc") == "Edit field"),
            next((str(item["value"]) for item in elements
                  if item.get("role") == "AXStaticText"
                  and item.get("desc") != "Last Expression"), ""),
        )
        return {"ok": display == "15", "display": display, "steps": steps}
    finally:
        if launched:
            subprocess.run(["osascript", "-e", 'tell application "Calculator" to quit'],
                           capture_output=True)


def safari(state: Path, url: str = "https://example.com") -> dict:
    subprocess.run(["open", "-a", "Safari", url], check=True)
    time.sleep(3.0)
    computer = ComputerUse(state, cursor=True)
    view = computer.see("Safari")
    matches = computer.find_controls("Safari", "reload")
    machine = None
    if matches:
        machine = computer.click_ref("Safari", matches[0]["ref"])
    return {
        "ok": bool(matches and machine and machine.get("verified") is not False),
        "controls": len(view["elements"]),
        "first_controls": view["text"].splitlines()[:5],
        "reload": matches[:1],
        "click": machine,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("calculator", "safari"))
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = calculator(args.state) if args.scenario == "calculator" else safari(args.state)
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

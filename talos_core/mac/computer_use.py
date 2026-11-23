"""Native macOS control primitives for Talos.

The layer wraps what Codex-style computer use needs: see the screen, inspect
the frontmost app and its windows, focus apps, click, type, press shortcuts,
navigate menus, and move data through the clipboard. All actions go through
argv-only subprocess calls (never a shell) and return receipts suitable for an
autonomous loop.

Safety and audit:

* destructive shortcuts require an explicit ``confirm=True``;
* typed text is never written to the audit log, only its length and digest;
* every action appends a JSONL receipt under ``state/computer-use.jsonl``;
* permission probes report Accessibility and Screen Recording status so the
  caller can ask the operator for the missing grant instead of failing opaquely.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCREENCAPTURE = "/usr/sbin/screencapture"
OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
PBPUT = "/usr/bin/pbcopy"
PBGET = "/usr/bin/pbpaste"

MAX_OUTPUT = 8_000
DESTRUCTIVE_KEYS = {
    frozenset({"cmd", "q"}),
    frozenset({"cmd", "delete"}),
    frozenset({"cmd", "shift", "delete"}),
    frozenset({"cmd", "option", "escape"}),
    frozenset({"cmd", "control", "q"}),
}
KEY_CODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "forwarddelete": 117,
}
MODIFIERS = {"cmd": "command down", "command": "command down",
             "ctrl": "control down", "control": "control down",
             "alt": "option down", "option": "option down",
             "shift": "shift down"}


class ComputerUseError(RuntimeError):
    """An action could not be completed."""


class PolicyError(ComputerUseError):
    """The action was refused by Talos policy."""


class PermissionError_(ComputerUseError):
    """A required macOS privacy permission is missing."""


@dataclass
class Receipt:
    action: str
    ok: bool
    timestamp: str
    detail: str = ""
    output: str = ""
    path: str | None = None
    confirmed: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _truncate(value: str) -> str:
    return value if len(value) <= MAX_OUTPUT else value[:MAX_OUTPUT] + "…"


def _apple_string(value: str) -> str:
    """Quote arbitrary text as an AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _validate_app(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 120 or "\n" in name or "\r" in name:
        raise ValueError("A process name must be 1–120 printable characters.")
    return name


def _validate_point(x: int, y: int) -> tuple[int, int]:
    if not (0 <= int(x) < 20_000 and 0 <= int(y) < 20_000):
        raise ValueError("Screen coordinates are outside a sane range.")
    return int(x), int(y)


def permissions() -> dict:
    """Read macOS privacy grants without prompting."""
    result = {"accessibility": None, "screen_recording": None, "platform": "darwin"}
    if not _is_darwin():
        result["platform"] = "unsupported"
        return result
    try:
        services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        result["accessibility"] = bool(services.AXIsProcessTrusted())
    except (OSError, AttributeError):
        pass
    try:
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        if hasattr(core, "CGPreflightScreenCaptureAccess"):
            core.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
            result["screen_recording"] = bool(core.CGPreflightScreenCaptureAccess())
    except (OSError, AttributeError):
        pass
    return result


def _is_darwin() -> bool:
    import sys

    return sys.platform == "darwin"


def _normalize_key(combo: str) -> tuple[tuple[str, ...], str]:
    parts = [part.strip().lower() for part in re.split(r"[+\-]", combo) if part.strip()]
    if not parts:
        raise ValueError("Describe a key or shortcut such as 'cmd+shift+4'.")
    key = parts[-1]
    modifiers = tuple(dict.fromkeys(part for part in parts[:-1]))
    unknown = [part for part in modifiers if part not in MODIFIERS]
    if unknown:
        raise ValueError(f"Unknown modifier(s): {', '.join(unknown)}")
    if key not in KEY_CODES and len(key) != 1:
        raise ValueError(f"Unsupported key: {key}")
    return modifiers, key


class ComputerUse:
    """A thin, auditable driver for macOS screen and UI automation."""

    def __init__(self, state: Path, *, runner: Callable = subprocess.run,
                 clock: Callable[[], float] = time.time,
                 screen_dir: Path | None = None) -> None:
        self.state = Path(state)
        self.reader = runner
        self.clock = clock
        self.screen_dir = Path(screen_dir) if screen_dir else self.state / "screens"
        self.log_path = self.state / "computer-use.jsonl"

    # -- observation ------------------------------------------------------
    def frontmost(self) -> dict:
        script = ('tell application "System Events" to get name of first application process '
                  'whose frontmost is true')
        receipt = self._osascript("frontmost_app", script)
        return {"app": receipt.output.strip(), "receipt": receipt.to_dict()}

    def apps(self) -> list[dict]:
        script = ('tell application "System Events" to get name of every process '
                  'whose background only is false')
        receipt = self._osascript("list_apps", script)
        names = [name.strip() for name in receipt.output.split(",") if name.strip()]
        return [{"name": name, "frontmost": False} for name in names]

    def windows(self, app: str | None = None) -> list[dict]:
        target = _apple_string(_validate_app(app)) if app else None
        script = (
            f'tell application "System Events" to tell process {target} to get {{name, position, size}} '
            "of every window" if target else
            'tell application "System Events" to get {name, position, size} of every window '
            'of first application process whose frontmost is true'
        )
        receipt = self._osascript("list_windows", script)
        return [{"raw": line.strip()} for line in receipt.output.splitlines() if line.strip()]

    def screenshot(self, *, path: Path | None = None) -> Receipt:
        self.screen_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = Path(path) if path else self.screen_dir / f"screen-{int(self.clock() * 1000)}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [SCREENCAPTURE, "-x", "-t", "png", str(destination)]
        receipt = self._run("screenshot", command)
        receipt.path = str(destination)
        if receipt.ok:
            try:
                os.chmod(destination, 0o600)
                receipt.metadata["bytes"] = destination.stat().st_size
            except OSError as error:
                receipt.ok = False
                receipt.detail = f"screenshot saved but unreadable: {error}"
        self.audit(receipt)
        return receipt

    def observe(self) -> dict:
        """One bounded observation step for a computer-use loop."""
        shot = self.screenshot()
        return {
            "frontmost": self.frontmost().get("app", ""),
            "windows": self.windows(),
            "screenshot": shot.path if shot.ok else None,
            "permissions": permissions(),
        }

    # -- input ------------------------------------------------------------
    def activate(self, app: str) -> Receipt:
        app = _validate_app(app)
        receipt = self._run("activate_app", [OPEN, "-a", app])
        self.audit(receipt, app=app)
        return receipt

    def open_path(self, path: str) -> Receipt:
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(target)
        receipt = self._run("open_path", [OPEN, str(target)])
        self.audit(receipt, path=str(target))
        return receipt

    def click(self, x: int, y: int, *, button: str = "left", count: int = 1,
              confirm: bool = False) -> Receipt:
        x, y = _validate_point(x, y)
        if button not in ("left", "right"):
            raise ValueError("button must be 'left' or 'right'")
        if count < 1 or count > 3:
            raise ValueError("count must be 1–3")
        if button == "right":
            raise PolicyError("Right-click menus vary by app; use menu() or click a visible control.")
        if count != 1:
            # Double-click is expressed by repeating the click action in one script.
            script = (f'tell application "System Events"\nrepeat {count} times\n'
                      f'click at {{{x}, {y}}}\ndelay 0.08\nend repeat\nend tell')
        else:
            script = f'tell application "System Events" to click at {{{x}, {y}}}'
        receipt = self._osascript("click", script, confirmed=confirm)
        self.audit(receipt, point=[x, y], button=button)
        return receipt

    def type_text(self, text: str, *, app: str | None = None) -> Receipt:
        if not isinstance(text, str) or not text:
            raise ValueError("Text is required.")
        if len(text) > 8_000:
            raise ValueError("Refusing to type more than 8,000 characters in one action.")
        target = _validate_app(app) if app else None
        if target:
            self.activate(target)
        lines = text.split("\n")
        statements = []
        for index, line in enumerate(lines):
            statements.append(f'keystroke {_apple_string(line)}')
            if index != len(lines) - 1:
                statements.append("key code 36")
        script = 'tell application "System Events" to\n' + "\n".join(statements) + "\nend tell"
        receipt = self._osascript("type_text", script)
        receipt.metadata = {"characters": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        self.audit(receipt)
        return receipt

    def press(self, combo: str, *, confirm: bool = False) -> Receipt:
        modifiers, key = _normalize_key(combo)
        if frozenset(modifiers) | {key} in DESTRUCTIVE_KEYS and not confirm:
            raise PolicyError(f"{combo} closes or deletes data; pass confirm=True explicitly.")
        using = f" using {{{', '.join(MODIFIERS[m] for m in modifiers)}}}" if modifiers else ""
        if key in KEY_CODES:
            script = f'tell application "System Events" to key code {KEY_CODES[key]}{using}'
        else:
            script = f'tell application "System Events" to keystroke {_apple_string(key)}{using}'
        receipt = self._osascript("press_keys", script, confirmed=confirm)
        self.audit(receipt, combo=combo)
        return receipt

    def menu(self, app: str, *items: str) -> Receipt:
        """Click a menu path, e.g. ``menu("Safari", "File", "New Window")``."""
        app = _validate_app(app)
        if len(items) < 2:
            raise ValueError("Supply an app menu path, for example: File New.")
        if any(not str(item).strip() or len(str(item)) > 120 for item in items):
            raise ValueError("Menu items must be short non-empty strings.")
        chain = f'menu {_apple_string(items[0])} of menu bar 1'
        for item in items[1:-1]:
            chain = f'menu {_apple_string(item)} of menu item {_apple_string(item)} of {chain}'
        # Nested submenus are rare; the common two-level path is exact.
        if len(items) == 2:
            script = (f'tell application "System Events" to tell process {_apple_string(app)} '
                      f'to click menu item {_apple_string(items[1])} of menu '
                      f'{_apple_string(items[0])} of menu bar 1')
        else:
            script = (f'tell application "System Events" to tell process {_apple_string(app)} '
                      f'to click menu item {_apple_string(items[-1])} of '
                      + " of ".join(f'menu {_apple_string(name)}' for name in reversed(items[:-1]))
                      + ' of menu bar 1')
        receipt = self._osascript("menu", script)
        self.audit(receipt, app=app, menu=list(items))
        return receipt

    def clipboard(self) -> str:
        receipt = self._run("read_clipboard", [PBGET])
        return receipt.output

    def set_clipboard(self, text: str) -> Receipt:
        if len(text) > 500_000:
            raise ValueError("Clipboard text is too large.")
        receipt = self._run("write_clipboard", [PBPUT], input=text)
        self.audit(receipt, characters=len(text))
        return receipt

    def screenshot_bytes(self) -> bytes:
        """Capture the screen and return PNG bytes without leaving a file behind."""
        with tempfile.TemporaryDirectory(prefix="talos-shot-") as folder:
            path = Path(folder) / "shot.png"
            receipt = self.screenshot(path=path)
            if not receipt.ok:
                raise ComputerUseError(receipt.detail or "Screen capture failed.")
            return path.read_bytes()

    def screenshot_jpeg(self, quality: int = 80) -> bytes:
        """Return a bounded JPEG for local vision models."""
        from io import BytesIO

        from PIL import Image

        data = self.screenshot_bytes()
        image = Image.open(BytesIO(data)).convert("RGB")
        image.thumbnail((2048, 2048))
        output = BytesIO()
        image.save(output, "JPEG", quality=quality)
        return output.getvalue()

    # -- internals --------------------------------------------------------
    def _osascript(self, action: str, script: str, *, confirmed: bool = False) -> Receipt:
        if not _is_darwin():
            return Receipt(action=action, ok=False, timestamp=_now(), detail="macOS only")
        return self._run(action, [OSASCRIPT, "-e", script], confirmed=confirmed)

    def _run(self, action: str, command: Sequence[str], *, input: str | None = None,
             confirmed: bool = False) -> Receipt:
        started = time.monotonic()
        try:
            completed = self.reader(
                list(command), capture_output=True, text=True, input=input, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError) as error:
            return Receipt(action=action, ok=False, timestamp=_now(),
                           detail=f"{type(error).__name__}: {error}", confirmed=confirmed)
        stdout = _truncate((completed.stdout or "").strip())
        stderr = _truncate((completed.stderr or "").strip())
        ok = completed.returncode == 0
        detail = stderr or ("completed" if ok else f"exit {completed.returncode}")
        if not ok and "not allowed assistive access" in stderr.lower():
            detail = "Accessibility permission is required (System Settings → Privacy & Security → Accessibility)."
        return Receipt(action=action, ok=ok, timestamp=_now(), detail=detail, output=stdout,
                       confirmed=confirmed,
                       metadata={"duration_ms": round((time.monotonic() - started) * 1000)})

    def audit(self, receipt: Receipt, **fields) -> None:
        """Append a private JSONL receipt; never stores typed content."""
        if fields:
            receipt.metadata.update(fields)
        try:
            self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
            line = json.dumps(receipt.to_dict(), ensure_ascii=False, sort_keys=True)
            with open(os.open(self.log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600),
                      "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


ALLOWED_ACTIONS = {"activate", "open", "type", "key", "click", "menu", "screenshot", "done"}


class ComputerUseLoop:
    """Bounded observe → decide → act loop in the style of a computer-use agent.

    ``decide(goal, observation, history)`` returns one action dict, for example
    ``{"action": "click", "x": 420, "y": 260}`` or ``{"action": "done"}``.
    The loop never interprets model text directly; unknown actions are refused.
    """

    def __init__(self, computer: ComputerUse, *, max_steps: int = 8,
                 confirm_sensitive: bool = False) -> None:
        if not 1 <= max_steps <= 32:
            raise ValueError("max_steps must be 1–32")
        self.computer = computer
        self.max_steps = max_steps
        self.confirm_sensitive = confirm_sensitive

    def run(self, goal: str, decide: Callable, *, observe_first: bool = True) -> dict:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("A goal is required.")
        history: list[dict] = []
        observation = self.computer.observe() if observe_first else {}
        for step in range(self.max_steps):
            action = decide(goal, observation, history)
            if not isinstance(action, dict) or action.get("action") not in ALLOWED_ACTIONS:
                raise ValueError(f"Unsupported computer action: {action!r}")
            name = action["action"]
            if name == "done":
                return {"ok": True, "steps": step + 1, "history": history,
                        "screenshot": observation.get("screenshot")}
            receipt = self._execute(name, action)
            history.append({"step": step + 1, "action": name, "ok": receipt.ok,
                            "detail": receipt.detail})
            if not receipt.ok:
                return {"ok": False, "steps": step + 1, "history": history,
                        "error": receipt.detail}
            observation = self.computer.observe()
        return {"ok": False, "steps": self.max_steps, "history": history,
                "error": "step limit reached"}

    def _execute(self, name: str, action: dict) -> Receipt:
        if name == "activate":
            return self.computer.activate(str(action.get("app", "")))
        if name == "open":
            return self.computer.open_path(str(action.get("path", "")))
        if name == "type":
            return self.computer.type_text(str(action.get("text", "")), app=action.get("app"))
        if name == "key":
            return self.computer.press(str(action.get("combo", "")),
                                       confirm=bool(action.get("confirm")) or self.confirm_sensitive)
        if name == "click":
            return self.computer.click(int(action.get("x", -1)), int(action.get("y", -1)),
                                       count=int(action.get("count", 1)))
        if name == "menu":
            items = action.get("items") or []
            return self.computer.menu(str(action.get("app", "")), *[str(item) for item in items])
        return self.computer.screenshot()


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Control this Mac from Talos.")
    parser.add_argument("--state", type=Path, default=Path("state"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("permissions")
    commands.add_parser("apps")
    commands.add_parser("frontmost")
    window = commands.add_parser("windows")
    window.add_argument("--app")
    shot = commands.add_parser("screenshot")
    shot.add_argument("--path", type=Path)
    activate = commands.add_parser("activate")
    activate.add_argument("app")
    typing = commands.add_parser("type")
    typing.add_argument("text")
    typing.add_argument("--app")
    key = commands.add_parser("key")
    key.add_argument("combo")
    key.add_argument("--confirm", action="store_true")
    click = commands.add_parser("click")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    menu = commands.add_parser("menu")
    menu.add_argument("app")
    menu.add_argument("items", nargs="+")
    args = parser.parse_args()

    computer = ComputerUse(args.state)
    if args.command == "permissions":
        payload = permissions()
    elif args.command == "apps":
        payload = computer.apps()
    elif args.command == "frontmost":
        payload = computer.frontmost()
    elif args.command == "windows":
        payload = computer.windows(args.app)
    elif args.command == "screenshot":
        payload = computer.screenshot(path=args.path).to_dict()
    elif args.command == "activate":
        payload = computer.activate(args.app).to_dict()
    elif args.command == "type":
        payload = computer.type_text(args.text, app=args.app).to_dict()
    elif args.command == "key":
        payload = computer.press(args.combo, confirm=args.confirm).to_dict()
    elif args.command == "click":
        payload = computer.click(args.x, args.y).to_dict()
    else:
        payload = computer.menu(args.app, *args.items).to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0 if (payload.ok if isinstance(payload, dict) and "ok" in payload else True) else 1


if __name__ == "__main__":
    raise SystemExit(_cli())

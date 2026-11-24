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

import base64
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

from talos_core.security.policy import Policy

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
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "return": 36, "l": 37, "j": 38, "'": 39, "k": 40,
    ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "tab": 48, "space": 49, "`": 50, "delete": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "forwarddelete": 117,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
FLAGS = {"cmd": 1 << 20, "command": 1 << 20, "shift": 1 << 17,
         "ctrl": 1 << 18, "control": 1 << 18, "alt": 1 << 19, "option": 1 << 19}
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
    if not (-20_000 < int(x) < 20_000 and -20_000 < int(y) < 20_000):
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


def _core_graphics():
    """Load CoreGraphics/CoreFoundation for the ctypes fallback."""
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventKeyboardSetUnicodeString.restype = None
    cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                   ctypes.c_void_p]
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_ulonglong]
    cg.CGEventPostToPid.restype = None
    cg.CGEventPostToPid.argtypes = [ctypes.c_int, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return cg, cf


class ComputerUse:
    """A thin, auditable driver for macOS screen and UI automation."""

    def __init__(self, state: Path, *, runner: Callable = subprocess.run,
                 clock: Callable[[], float] = time.time,
                 screen_dir: Path | None = None, cursor: bool | None = None,
                 policy: Policy | None = None) -> None:
        self.state = Path(state)
        self.reader = runner
        self.clock = clock
        self.screen_dir = Path(screen_dir) if screen_dir else self.state / "screens"
        self.log_path = self.state / "computer-use.jsonl"
        self._policy = policy
        if cursor is None:
            cursor = _is_darwin() and os.environ.get("TALOS_CURSOR", "1") != "0"
        self.cursor = None
        if cursor:
            from talos_core.mac.cursor import CursorOverlay
            self.cursor = CursorOverlay(self.state)

    def policy(self) -> Policy:
        """The active approval policy, loaded from private state on first use."""
        if self._policy is None:
            self._policy = Policy.load(self.state)
        return self._policy

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

    def visible_windows(self) -> list[dict]:
        """List on-screen windows through Quartz, without needing Accessibility per app."""
        try:
            import Quartz
        except ImportError:
            return self.windows()
        options = (Quartz.kCGWindowListOptionOnScreenOnly
                   | Quartz.kCGWindowListExcludeDesktopElements)
        listing = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        windows = []
        for item in listing:
            if item.get(Quartz.kCGWindowLayer, 0) != 0:
                continue
            owner = item.get(Quartz.kCGWindowOwnerName) or ""
            title = item.get(Quartz.kCGWindowName) or ""
            if not owner:
                continue
            bounds = item.get(Quartz.kCGWindowBounds) or {}
            windows.append({
                "app": owner, "title": title,
                "raw": f"{owner}: {title}".rstrip(": "),
                "bounds": dict(bounds),
            })
        return windows

    def window_bounds(self, app: str | None = None) -> tuple[int, int, int, int] | None:
        """Return ``(x, y, width, height)`` for the front window, or None."""
        target = _apple_string(_validate_app(app)) if app else None
        if target:
            script = (f'tell application "System Events" to tell process {target} to get '
                      "{position, size} of front window")
        else:
            script = ('tell application "System Events" to get {position, size} of front window '
                      'of first application process whose frontmost is true')
        receipt = self._osascript("window_bounds", script)
        if not receipt.ok:
            return None
        numbers = re.findall(r"-?\d+", receipt.output)
        if len(numbers) < 4:
            return None
        x, y, width, height = map(int, numbers[:4])
        if width <= 0 or height <= 0:
            return None
        return x, y, width, height

    def screenshot(self, *, path: Path | None = None,
                   region: tuple[int, int, int, int] | None = None) -> Receipt:
        self.screen_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = Path(path) if path else self.screen_dir / f"screen-{int(self.clock() * 1000)}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [SCREENCAPTURE, "-x", "-t", "png"]
        if region is not None:
            x, y, width, height = region
            if width <= 0 or height <= 0:
                raise ValueError("Screenshot region must have a positive size.")
            command += ["-R", f"{x},{y},{width},{height}"]
        command.append(str(destination))
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

    @staticmethod
    def perception():
        from talos_core.mac import perception
        return perception

    def see(self, app: str, *, annotate: bool = False,
            interactive: bool = True) -> dict:
        """Snapshots the app's controls and optionally a set-of-mark screenshot."""
        perception = self.perception()
        state = perception.snapshot(app, interactive=interactive)
        marked = None
        if annotate:
            marked = perception.annotate(app)
        return {
            "app": app,
            "elements": perception.elements(state),
            "text": perception.describe(state),
            "snapshot": state.get("snapshot"),
            "marks": marked.get("marks") if marked else None,
            "screenshot": marked.get("path") if marked else None,
        }

    def find_controls(self, app: str, query: str, *, limit: int = 8) -> list[dict]:
        return self.perception().find(app, query, limit=limit)

    def ocr(self, app: str, query: str | None = None) -> dict:
        return self.perception().ocr(app, query)

    def click_ref(self, app: str, ref: str) -> dict:
        result = self.perception().click_ref(app, ref)
        self.audit(Receipt("click_ref", True, _now(), str(result)[:300],
                           metadata={"app": app, "ref": ref}))
        return result

    def type_ref(self, app: str, ref: str, text: str) -> dict:
        result = self.perception().type_into_ref(app, ref, text)
        bounds = self.window_bounds(app)
        if bounds:
            self._show_cursor(bounds[0] + 60, bounds[1] + 24, f"field · {app}")
        self.audit(Receipt("type_ref", True, _now(), f"{len(text)} characters",
                           metadata={"app": app, "ref": ref,
                                     "sha256": hashlib.sha256(text.encode()).hexdigest()}))
        return result

    def scroll(self, amount: int, *, dx: int = 0, app: str | None = None,
               x: int | None = None, y: int | None = None, unit: str = "line") -> dict:
        """Scroll a target app without moving the physical pointer."""
        perception = self.perception()
        args = ["input", "scroll", "--amount", str(int(amount)), "--dx", str(int(dx)),
                "--unit", unit]
        if app:
            target = _validate_app(app)
            args += ["--app", target]
            if x is None or y is None:
                bounds = self.window_bounds(target)
                if bounds is None:
                    raise ComputerUseError(f"No window for {target!r} to scroll.")
                x = bounds[0] + bounds[2] // 2 if x is None else x
                y = bounds[1] + bounds[3] // 2 if y is None else y
        if x is not None and y is not None:
            _validate_point(x, y)
            args += ["--x", str(int(x)), "--y", str(int(y))]
        args.append("--show")
        result = perception.run(*args)
        self._show_cursor(int(x if x is not None else 200), int(y if y is not None else 200),
                          f"scroll · {app or 'front'}")
        self.audit(Receipt("scroll", True, _now(), str(result)[:200],
                           metadata={"app": app, "amount": amount, "dx": dx}))
        return result

    def hover(self, x: int, y: int, *, app: str | None = None) -> dict:
        x, y = _validate_point(x, y)
        perception = self.perception()
        args = ["input", "hover", "--x", str(x), "--y", str(y), "--show"]
        if app:
            args += ["--app", _validate_app(app)]
        result = perception.run(*args)
        self._show_cursor(x, y, f"hover · {app or 'front'}")
        self.audit(Receipt("hover", True, _now(), str(result)[:200],
                           metadata={"app": app, "x": x, "y": y}))
        return result

    def drag(self, from_x: int, from_y: int, to_x: int, to_y: int, *,
             app: str | None = None, steps: int = 12, button: str = "left",
             mode: str = "pid") -> dict:
        for x, y in ((from_x, from_y), (to_x, to_y)):
            _validate_point(x, y)
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be left, right or middle")
        if mode not in ("pid", "hid"):
            raise ValueError("mode must be pid (background) or hid (real pointer)")
        perception = self.perception()
        args = ["input", "drag", "--x", str(int(from_x)), "--y", str(int(from_y)),
                "--to-x", str(int(to_x)), "--to-y", str(int(to_y)),
                "--steps", str(max(1, min(int(steps), 60))), "--button", button,
                "--mode", mode, "--show"]
        if app:
            args += ["--app", _validate_app(app)]
        result = perception.run(*args)
        self._show_cursor(int(to_x), int(to_y), f"drag · {app or 'front'}")
        self.audit(Receipt("drag", True, _now(), str(result)[:200],
                           metadata={"app": app, "from": [from_x, from_y],
                                     "to": [to_x, to_y]}))
        return result

    def window(self, action: str, *, app: str | None = None,
               confirm: bool = False, **options) -> dict:
        """List, move, resize, minimize, restore, raise, close or fullscreen a window."""
        allowed = {"list", "move", "resize", "minimize", "restore", "raise",
                   "focus", "close", "fullscreen"}
        if action not in allowed:
            raise ValueError(f"Unknown window action: {action}")
        if action == "close":
            decision = self.policy().approve("window", app=app, target=action,
                                             destructive=True, confirm=confirm)
            if decision.needs_confirmation:
                raise PolicyError("Closing a window needs confirm=True.")
            if not decision.allowed:
                raise PolicyError(decision.reason)
        args = ["window", action]
        if app:
            args += ["--app", _validate_app(app)]
        for key in ("title", "index", "x", "y", "width", "height"):
            if options.get(key) is not None:
                args += [f"--{key}", str(options[key])]
        result = self.perception().run(*args)
        self.audit(Receipt(f"window_{action}", True, _now(), str(result)[:240],
                           metadata={"app": app, **{k: v for k, v in options.items()
                                                    if v is not None}}))
        return result

    def choose(self, app: str, goal: str, decider=None, model: str | None = None) -> dict:
        choice = self.perception().choose(app, goal, decider=decider, model=model)
        center = choice.get("center") or [0, 0]
        self._show_cursor(int(center[0]), int(center[1]), f"target · {app}")
        return choice

    def click_goal(self, app: str, goal: str, decider=None, model: str | None = None) -> dict:
        """Find a visible control by intent, then click it and report the choice."""
        choice = self.choose(app, goal, decider=decider, model=model)
        result = self.click_ref(app, choice["ref"])
        return {**choice, "click": result}

    def _harness(self):
        try:
            from computer_harness import engine
            return engine
        except ImportError:
            return None

    def app_state(self, app: str, *, screenshot: bool = True) -> dict:
        """Reader-first background snapshot via computer-harness (optional)."""
        engine = self._harness()
        if engine is None:
            raise ComputerUseError(
                "Install the control extra for background accessibility: "
                "pip install 'talos-assistant[control]'")
        return engine.get_app_state(_validate_app(app), capture=screenshot)

    def click_element(self, app: str, element_index: int, *, method: str = "ax") -> dict:
        engine = self._harness()
        if engine is None:
            raise ComputerUseError("Background element clicks need the control extra.")
        state = engine.get_app_state(_validate_app(app), capture=False)
        result = engine.click_element(state["pid"], state["elements"], element_index,
                                      method=method)
        bounds = state.get("window_bounds") or {}
        if bounds:
            self._show_cursor(bounds.get("x", 0) + 40, bounds.get("y", 0) + 40,
                              f"element · {app}")
        self.audit(Receipt("click_element", True, _now(), str(result),
                           metadata={"app": app, "index": element_index}))
        return result

    def set_element_value(self, app: str, element_index: int, value: str) -> dict:
        engine = self._harness()
        if engine is None:
            raise ComputerUseError("AX text entry needs the control extra.")
        state = engine.get_app_state(_validate_app(app), capture=False)
        result = engine.set_element_value(state["pid"], state["elements"],
                                          element_index, value)
        self.audit(Receipt("set_element_value", True, _now(),
                           f"{len(value)} characters via AX",
                           metadata={"app": app, "index": element_index,
                                     "sha256": hashlib.sha256(value.encode()).hexdigest()}))
        return result

    def window_jpeg(self, app: str, *, quality: int = 82) -> str:
        """Base64 JPEG of a window, readable even when the app is backgrounded."""
        engine = self._harness()
        data = None
        if engine is not None:
            try:
                state = engine.get_app_state(_validate_app(app), capture=True)
                path = (state.get("screenshot") or {}).get("path")
                if path:
                    data = Path(path).read_bytes()
            except (OSError, ValueError, RuntimeError):
                data = None
        if data is None:
            bounds = self.window_bounds(app)
            if bounds is None:
                raise ComputerUseError(f"No window for {app!r}.")
            with tempfile.TemporaryDirectory(prefix="talos-window-") as folder:
                target = Path(folder) / "window.png"
                receipt = self.screenshot(path=target, region=bounds)
                if not receipt.ok:
                    raise ComputerUseError(receipt.detail or "Window capture failed.")
                data = target.read_bytes()
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(data)).convert("RGB")
        image.thumbnail((1800, 1800))
        output = BytesIO()
        image.save(output, "JPEG", quality=quality)
        return base64.b64encode(output.getvalue()).decode()

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
    def app_pid(self, app: str) -> int:
        """Resolve a running application's process id."""
        app = _validate_app(app)
        receipt = self._osascript(
            "app_pid",
            f'tell application "System Events" to get unix id of process {_apple_string(app)}',
        )
        if receipt.ok and receipt.output.strip().isdigit():
            return int(receipt.output.strip())
        try:
            from AppKit import NSWorkspace

            for candidate in NSWorkspace.sharedWorkspace().runningApplications():
                if str(candidate.localizedName() or "").lower() == app.lower():
                    return int(candidate.processIdentifier())
        except (ImportError, AttributeError):
            pass
        raise ValueError(f"No running application named {app!r}.")

    def _post_unicode_to_pid(self, pid: int, text: str) -> None:
        """Deliver Unicode text straight to one process, without changing focus."""
        try:
            import Quartz

            for char in text:
                for down in (True, False):
                    event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(event, len(char), char)
                    Quartz.CGEventPostToPid(int(pid), event)
                time.sleep(0.004)
            return
        except (ImportError, AttributeError):
            pass
        cg, cf = _core_graphics()
        units = text.encode("utf-16-le")
        for index in range(0, len(units), 2):
            pair = units[index:index + 2]
            if pair in (b"\r\x00", b"\n\x00"):
                self._post_key_to_pid(pid, 36, 0)
                continue
            buffer = ctypes.create_string_buffer(pair, len(pair))
            for down in (True, False):
                event = cg.CGEventCreateKeyboardEvent(None, 0, down)
                cg.CGEventKeyboardSetUnicodeString(event, 1, buffer)
                cg.CGEventPostToPid(int(pid), event)
                cf.CFRelease(event)
                time.sleep(0.004)

    def _post_key_to_pid(self, pid: int, keycode: int, flags: int) -> None:
        try:
            import Quartz

            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, int(keycode), down)
                Quartz.CGEventSetFlags(event, int(flags))
                Quartz.CGEventPostToPid(int(pid), event)
                time.sleep(0.01)
            return
        except (ImportError, AttributeError):
            pass
        cg, cf = _core_graphics()
        for down in (True, False):
            event = cg.CGEventCreateKeyboardEvent(None, int(keycode), down)
            cg.CGEventSetFlags(event, int(flags))
            cg.CGEventPostToPid(int(pid), event)
            cf.CFRelease(event)
            time.sleep(0.01)

    def _post_click_to_pid(self, pid: int, x: int, y: int, count: int,
                           button: str = "left") -> None:
        try:
            import Quartz
        except ImportError as error:
            raise ComputerUseError("Direct clicks need pyobjc-framework-Quartz.") from error
        if button == "right":
            kinds = (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp)
            mouse_button = Quartz.kCGMouseButtonRight
        else:
            kinds = (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp)
            mouse_button = Quartz.kCGMouseButtonLeft
        for number in range(1, count + 1):
            for kind in kinds:
                event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), mouse_button)
                Quartz.CGEventSetIntegerValueField(
                    event, Quartz.kCGMouseEventClickState, number)
                Quartz.CGEventPostToPid(int(pid), event)
                time.sleep(0.01)

    def _show_cursor(self, x: int, y: int, label: str = "TALOS") -> None:
        if self.cursor is not None:
            try:
                self.cursor.show(int(x), int(y), label[:40])
            except (OSError, ValueError):
                pass

    def _show_click(self, x: int, y: int, label: str = "click") -> None:
        if self.cursor is not None:
            try:
                self.cursor.ripple(int(x), int(y), label[:40])
            except (OSError, ValueError):
                pass

    def activate(self, app: str) -> Receipt:
        app = _validate_app(app)
        try:
            pid = self.app_pid(app)
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

            running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if running is not None and running.activateWithOptions_(
                    NSApplicationActivateIgnoringOtherApps):
                receipt = Receipt("activate_pid", True, _now(), f"activated {app}",
                                  metadata={"pid": pid})
                self.audit(receipt, app=app)
                time.sleep(0.15)
                return receipt
        except (ImportError, OSError, RuntimeError, ValueError):
            pass
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
              confirm: bool = False, app: str | None = None) -> Receipt:
        x, y = _validate_point(x, y)
        if button not in ("left", "right"):
            raise ValueError("button must be 'left' or 'right'")
        if count < 1 or count > 3:
            raise ValueError("count must be 1–3")
        self._show_cursor(x, y, f"click {app}" if app else "click")
        self._show_click(x, y, f"click {app}" if app else "click")
        if app:
            pid = self.app_pid(app)
            self._post_click_to_pid(pid, x, y, count)
            receipt = Receipt("click_to_pid", True, _now(), f"delivered to {app}",
                              confirmed=confirm, metadata={"pid": pid, "count": count})
            self.audit(receipt, point=[x, y], app=app)
            return receipt
        if button == "right":
            if app:
                pid = self.app_pid(app)
                self._post_click_to_pid(pid, x, y, count, button="right")
                receipt = Receipt("right_click_to_pid", True, _now(),
                                  f"delivered to {app}", confirmed=confirm,
                                  metadata={"pid": pid, "count": count})
                self.audit(receipt, point=[x, y], app=app)
                return receipt
            return self.right_click(x, y, confirm=confirm)
        if count != 1:
            # Double-click is expressed by repeating the click action in one script.
            script = (f'tell application "System Events"\nrepeat {count} times\n'
                      f'click at {{{x}, {y}}}\ndelay 0.08\nend repeat\nend tell')
        else:
            script = f'tell application "System Events" to click at {{{x}, {y}}}'
        receipt = self._osascript("click", script, confirmed=confirm)
        self.audit(receipt, point=[x, y], button=button)
        return receipt

    def right_click(self, x: int, y: int, *, confirm: bool = False,
                    app: str | None = None) -> Receipt:
        """Right-click a screen point, directly to an app when given."""
        x, y = _validate_point(x, y)
        self._show_cursor(x, y, f"right-click {app}" if app else "right-click")
        self._show_click(x, y, f"right-click {app}" if app else "right-click")
        if app:
            pid = self.app_pid(app)
            self._post_click_to_pid(pid, x, y, 1, button="right")
            receipt = Receipt("right_click_to_pid", True, _now(),
                              f"delivered to {app}", confirmed=confirm,
                              metadata={"pid": pid})
            self.audit(receipt, point=[x, y], app=app)
            return receipt
        try:
            import Quartz
        except ImportError as error:
            raise ComputerUseError(
                "Global right-clicks need pyobjc-framework-Quartz.") from error
        for kind in (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp):
            event = Quartz.CGEventCreateMouseEvent(
                None, kind, (x, y), Quartz.kCGMouseButtonRight)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.02)
        receipt = Receipt("right_click", True, _now(), f"posted at {x},{y}",
                          confirmed=confirm)
        self.audit(receipt, point=[x, y])
        return receipt

    def move(self, x: int, y: int, *, app: str | None = None) -> Receipt:
        """Move the pointer without clicking; PID-targeted when ``app`` is set."""
        x, y = _validate_point(x, y)
        self._show_cursor(x, y, f"move · {app}" if app else "move")
        try:
            import Quartz
        except ImportError as error:
            raise ComputerUseError("Pointer moves need pyobjc-framework-Quartz.") from error
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (x, y), 0)
        if app:
            pid = self.app_pid(app)
            Quartz.CGEventPostToPid(pid, event)
            receipt = Receipt("move_to_pid", True, _now(), f"delivered to {app}",
                              metadata={"pid": pid})
        else:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            receipt = Receipt("move", True, _now(), f"posted at {x},{y}")
        self.audit(receipt, point=[x, y], app=app)
        return receipt

    def type_text(self, text: str, *, app: str | None = None) -> Receipt:
        """Type text. With ``app`` the events go straight to that PID, so the
        user's foreground app keeps focus and never receives stray keystrokes."""
        if not isinstance(text, str) or not text:
            raise ValueError("Text is required.")
        if len(text) > 8_000:
            raise ValueError("Refusing to type more than 8,000 characters in one action.")
        if app:
            target = _validate_app(app)
            pid = self.app_pid(target)
            bounds = self.window_bounds(target)
            if bounds:
                x, y, width, _height = bounds
                self._show_cursor(x + max(24, width // 2), y + 24, f"typing · {target}")
            else:
                self._show_cursor(80, 80, f"typing · {target}")
            try:
                self._post_unicode_to_pid(pid, text)
            except (OSError, RuntimeError) as error:
                raise ComputerUseError(f"Could not type into {target}: {error}") from error
            receipt = Receipt("type_text_to_pid", True, _now(), f"delivered to {target}",
                              metadata={"characters": len(text), "pid": pid,
                                        "sha256": hashlib.sha256(text.encode()).hexdigest()})
            self.audit(receipt, app=target)
            return receipt
        lines = text.split("\n")
        statements = []
        for index, line in enumerate(lines):
            statements.append(f'keystroke {_apple_string(line)}')
            if index != len(lines) - 1:
                statements.append("key code 36")
        script = 'tell application "System Events"\n' + "\n".join(statements) + "\nend tell"
        receipt = self._osascript("type_text", script)
        receipt.metadata = {"characters": len(text),
                            "sha256": hashlib.sha256(text.encode()).hexdigest()}
        self.audit(receipt)
        return receipt

    def press(self, combo: str, *, confirm: bool = False, app: str | None = None) -> Receipt:
        modifiers, key = _normalize_key(combo)
        destructive = frozenset(modifiers) | {key} in DESTRUCTIVE_KEYS
        decision = self.policy().approve("key", app=app, payload=combo,
                                         destructive=destructive, confirm=confirm)
        if not decision.allowed:
            if decision.needs_confirmation and destructive:
                raise PolicyError(f"{combo} closes or deletes data; pass confirm=True explicitly.")
            raise PolicyError(decision.reason)
        if app:
            target = _validate_app(app)
            pid = self.app_pid(target)
            keycode = KEY_CODES.get(key)
            if keycode is None:
                raise ValueError(f"Direct shortcuts need a named key, not {key!r}.")
            flags = 0
            for modifier in modifiers:
                flags |= FLAGS[modifier]
            bounds = self.window_bounds(target)
            if bounds:
                self._show_cursor(bounds[0] + 60, bounds[1] + 24, f"key · {target}")
            self._post_key_to_pid(pid, keycode, flags)
            receipt = Receipt("press_keys_to_pid", True, _now(), f"delivered to {target}",
                              confirmed=confirm, metadata={"pid": pid})
            self.audit(receipt, combo=combo, app=target)
            return receipt
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
        bounds = self.window_bounds(app)
        if bounds:
            self._show_cursor(bounds[0] + 80, bounds[1] + 24, f"menu · {app}")
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
        overlay = getattr(self.computer, "cursor", None)
        started = False
        if overlay is not None:
            try:
                started = overlay.begin_session("computer use")
            except (OSError, ValueError):
                started = False
        try:
            return self._run_steps(goal, decide, observe_first=observe_first)
        finally:
            if started:
                try:
                    overlay.end_session()
                except (OSError, ValueError):
                    pass

    def _run_steps(self, goal: str, decide: Callable, *,
                   observe_first: bool = True) -> dict:
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
                                       confirm=bool(action.get("confirm")) or self.confirm_sensitive,
                                       app=action.get("app"))
        if name == "click":
            return self.computer.click(int(action.get("x", -1)), int(action.get("y", -1)),
                                       count=int(action.get("count", 1)),
                                       app=action.get("app"))
        if name == "menu":
            items = action.get("items") or []
            return self.computer.menu(str(action.get("app", "")), *[str(item) for item in items])
        return self.computer.screenshot()


def _gui_command(args, computer) -> dict:
    from talos_core.mac import gui_actions, gui_apps

    manager = gui_apps.GuiAppManager(args.state)
    if args.command == "gui-launch":
        return manager.start(args.start_command, name=args.name, wait=args.wait,
                             confirm=args.confirm)
    if args.command == "gui-status":
        return manager.status()
    if args.command == "gui-stop":
        return manager.stop(force=args.force)
    if args.command == "gui-act":
        return gui_actions.act(computer, args.action, x=args.x, y=args.y,
                               value=args.value, app=args.app, confirm=args.confirm)
    if args.command == "gui-goal":
        return gui_actions.target(computer, args.app, args.action, args.goal,
                                  value=args.value, confirm=args.confirm)
    return {"ok": True, "answer": gui_actions.verify(computer, args.app, args.question)}


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
    see = commands.add_parser("see", help="Read an app's visible controls and clickable refs.")
    see.add_argument("app")
    see.add_argument("--annotate", action="store_true")
    see.add_argument("--all", action="store_true", help="include non-interactive text")
    choose = commands.add_parser("choose", help="Find a control by intent, optionally with a vision model.")
    choose.add_argument("app")
    choose.add_argument("goal")
    choose.add_argument("--model")
    choose.add_argument("--click", action="store_true")
    find = commands.add_parser("find", help="Rank visible controls by label.")
    find.add_argument("app")
    find.add_argument("query")
    ocr = commands.add_parser("ocr", help="On-device OCR over an app window.")
    ocr.add_argument("app")
    ocr.add_argument("query", nargs="?")
    click_ref = commands.add_parser("click-ref", help="Click an accessibility ref.")
    click_ref.add_argument("app")
    click_ref.add_argument("ref")
    scroll = commands.add_parser("scroll", help="Background scroll a target app.")
    scroll.add_argument("amount", type=int)
    scroll.add_argument("--app")
    scroll.add_argument("--dx", type=int, default=0)
    scroll.add_argument("--x", type=int)
    scroll.add_argument("--y", type=int)
    hover = commands.add_parser("hover", help="Hover a screen point in a target app.")
    hover.add_argument("x", type=int)
    hover.add_argument("y", type=int)
    hover.add_argument("--app")
    drag = commands.add_parser("drag", help="Background drag between screen points.")
    drag.add_argument("x", type=int)
    drag.add_argument("y", type=int)
    drag.add_argument("to_x", type=int)
    drag.add_argument("to_y", type=int)
    drag.add_argument("--app")
    drag.add_argument("--mode", choices=["pid", "hid"], default="pid")
    window = commands.add_parser("window", help="List or manage app windows.")
    window.add_argument("action", choices=["list", "move", "resize", "minimize", "restore",
                                            "raise", "focus", "close", "fullscreen"])
    window.add_argument("--app")
    window.add_argument("--title")
    window.add_argument("--index", type=int)
    window.add_argument("--x", type=int)
    window.add_argument("--y", type=int)
    window.add_argument("--width", type=int)
    window.add_argument("--height", type=int)
    window.add_argument("--confirm", action="store_true")
    type_ref = commands.add_parser("type-ref", help="Set/type into an accessibility field.")
    type_ref.add_argument("app")
    type_ref.add_argument("ref")
    type_ref.add_argument("text")
    gui_launch = commands.add_parser("gui-launch", help="Start a managed GUI app.")
    gui_launch.add_argument("--command", dest="start_command", required=True)
    gui_launch.add_argument("--name")
    gui_launch.add_argument("--wait", type=float, default=1.0)
    gui_launch.add_argument("--confirm", action="store_true")
    commands.add_parser("gui-status", help="Show the managed GUI app.")
    gui_stop = commands.add_parser("gui-stop", help="Stop the managed GUI app.")
    gui_stop.add_argument("--force", action="store_true")
    gui_act = commands.add_parser("gui-act", help="Run one coordinate GUI action.")
    gui_act.add_argument("action", choices=("click", "double_click", "right_click", "move",
                                             "type", "key", "drag", "screenshot", "wait"))
    gui_act.add_argument("--x", type=int)
    gui_act.add_argument("--y", type=int)
    gui_act.add_argument("--value")
    gui_act.add_argument("--app")
    gui_act.add_argument("--confirm", action="store_true")
    gui_goal = commands.add_parser("gui-goal", help="Act on an element by description.")
    gui_goal.add_argument("app")
    gui_goal.add_argument("goal")
    gui_goal.add_argument("--action", choices=("click", "double_click", "right_click",
                                                "hover", "type"), default="click")
    gui_goal.add_argument("--value")
    gui_goal.add_argument("--confirm", action="store_true")
    gui_verify = commands.add_parser("gui-verify", help="Ask the local vision model about a window.")
    gui_verify.add_argument("app")
    gui_verify.add_argument("question")

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
    elif args.command == "see":
        payload = computer.see(args.app, annotate=args.annotate, interactive=not args.all)
    elif args.command == "choose":
        payload = (computer.click_goal(args.app, args.goal, model=args.model)
                   if args.click else computer.choose(args.app, args.goal, model=args.model))
    elif args.command == "find":
        payload = computer.find_controls(args.app, args.query)
    elif args.command == "ocr":
        payload = computer.ocr(args.app, args.query)
    elif args.command == "click-ref":
        payload = computer.click_ref(args.app, args.ref)
    elif args.command == "type-ref":
        payload = computer.type_ref(args.app, args.ref, args.text)
    elif args.command == "scroll":
        payload = computer.scroll(args.amount, dx=args.dx, app=args.app,
                                  x=args.x, y=args.y)
    elif args.command == "hover":
        payload = computer.hover(args.x, args.y, app=args.app)
    elif args.command == "drag":
        payload = computer.drag(args.x, args.y, args.to_x, args.to_y, app=args.app, mode=args.mode)
    elif args.command == "window":
        payload = computer.window(args.action, app=args.app, title=args.title,
                                  index=args.index, x=args.x, y=args.y,
                                  width=args.width, height=args.height,
                                  confirm=args.confirm)
    elif args.command.startswith("gui-"):
        try:
            payload = _gui_command(args, computer)
        except (ValueError, OSError, RuntimeError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 1
    else:
        payload = computer.menu(args.app, *args.items).to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    if isinstance(payload, dict) and "ok" in payload:
        return 0 if payload["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

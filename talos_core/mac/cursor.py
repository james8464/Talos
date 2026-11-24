"""Visible Talos cursor overlay.

The Swift helper is a click-through, always-on-top accessory window that never
takes focus or moves the real pointer. This wrapper compiles it on first use
(from ``tools/native/cursor_overlay.swift``), feeds newline-delimited JSON on
stdin, and hides the marker after a short delay.

The overlay is cosmetic: a failed compile or dead helper never blocks an action.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "cursor_overlay.swift"
if not SOURCE.is_file():
    SOURCE = Path(__file__).resolve().parent / "native" / "cursor_overlay.swift"
HIDE_AFTER = 2.5


class CursorOverlay:
    def __init__(self, state: Path, *, compiler: str | None = None,
                 popen=subprocess.Popen, enabled: bool = True) -> None:
        self.state = Path(state)
        self.binary = self.state / "native" / "talos-cursor-overlay"
        self.compiler = compiler or shutil.which("swiftc")
        self.popen = popen
        self.enabled = enabled and bool(self.compiler) and SOURCE.is_file()
        self.process = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        atexit.register(self.close)

    def _ensure_binary(self) -> bool:
        if not self.enabled:
            return False
        try:
            self.binary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (not self.binary.is_file()
                    or self.binary.stat().st_mtime < SOURCE.stat().st_mtime):
                completed = subprocess.run(
                    [self.compiler, "-O", str(SOURCE), "-o", str(self.binary)],
                    capture_output=True, text=True, timeout=120, check=False,
                )
                if completed.returncode != 0:
                    self.enabled = False
                    return False
                os.chmod(self.binary, 0o700)
            return True
        except (OSError, subprocess.SubprocessError):
            self.enabled = False
            return False

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return self.process
        if not self._ensure_binary():
            return None
        try:
            self.process = self.popen(
                [str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True,
            )
            time.sleep(0.15)
            return self.process
        except (OSError, subprocess.SubprocessError):
            self.process = None
            self.enabled = False
            return None

    def show(self, x: int, y: int, label: str = "TALOS", *, hide_after: float | None = HIDE_AFTER) -> bool:
        with self._lock:
            process = self._start()
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(
                    {"x": int(x), "y": int(y), "label": label[:60], "visible": True,
                     "control": True}
                ) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return False
            if self._timer is not None:
                self._timer.cancel()
            if hide_after:
                self._timer = threading.Timer(hide_after, self.hide)
                self._timer.daemon = True
                self._timer.start()
            return True

    def click(self, x: int, y: int, label: str = "click",
              *, hide_after: float | None = HIDE_AFTER) -> bool:
        """Show the cursor and a click ripple at a screen point."""
        with self._lock:
            process = self._start()
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(
                    {"x": int(x), "y": int(y), "label": label[:60], "visible": True,
                     "control": True, "click": True}
                ) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return False
            if self._timer is not None:
                self._timer.cancel()
            if hide_after:
                self._timer = threading.Timer(hide_after, self.hide)
                self._timer.daemon = True
                self._timer.start()
            return True

    def begin_session(self, label: str = "computer use") -> bool:
        """Keep the control indicator visible until :meth:`end_session`."""
        with self._lock:
            process = self._start()
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(
                    {"session": True, "label": label[:60], "visible": True, "control": True}
                ) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            return True

    def end_session(self) -> None:
        with self._lock:
            process = self.process
            if process is None or process.stdin is None:
                return
            try:
                process.stdin.write('{"session": false}\n')
                process.stdin.flush()
            except (OSError, ValueError):
                pass

    def highlight(self, x: int, y: int, width: int = 56, height: int = 56,
                  label: str = "target", *, hide_after: float | None = 8.0) -> bool:
        """Ring a screen rectangle and label it without clicking."""
        with self._lock:
            process = self._start()
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(
                    {"highlight": True, "x": int(x), "y": int(y),
                     "width": int(width), "height": int(height),
                     "label": label[:40], "visible": True, "control": True}
                ) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return False
            if self._timer is not None:
                self._timer.cancel()
            if hide_after:
                self._timer = threading.Timer(hide_after, self.hide)
                self._timer.daemon = True
                self._timer.start()
            return True

    def ripple(self, x: int, y: int, label: str = "click") -> bool:
        """Draw a click ripple without resetting the current label."""
        with self._lock:
            process = self.process
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(
                    {"x": int(x), "y": int(y), "label": label[:60], "visible": True,
                     "control": True, "click": True}
                ) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return False
            return True

    def hide(self) -> None:
        with self._lock:
            process = self.process
            if process is None or process.stdin is None:
                return
            try:
                process.stdin.write('{"visible": false, "control": false}\n')
                process.stdin.flush()
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            process, self.process = self.process, None
            if process is None:
                return
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except (OSError, subprocess.SubprocessError):
                    pass


overlay: CursorOverlay | None = None


def shared(state: Path) -> CursorOverlay:
    global overlay
    if overlay is None:
        overlay = CursorOverlay(state)
    return overlay

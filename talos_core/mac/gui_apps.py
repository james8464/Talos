"""Managed GUI application lifecycle.

Behavior is ported from OpenCowork's GUI test harness
(``src/main/mcp/software-dev-server-example.ts``, MIT, Copyright (c) 2026
OpenCoworkAI): start an application, wait for it to become ready, keep a log,
and stop it later.  This module keeps Talos's own safety rules instead of
copying the Electron code: argv only, never a shell, an explicit confirm, a
private log file, and a process-group shutdown so helpers do not survive.

Native apps that are already installed are better handled with
:meth:`talos_core.mac.computer_use.ComputerUse.activate`; this manager is for
apps that Talos itself launches (test apps, local Electron/Python tools).
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_COMMAND = 4_000


class GuiAppError(RuntimeError):
    pass


class GuiAppRefused(GuiAppError):
    pass


@dataclass
class GuiApp:
    name: str
    pid: int
    command: list[str]
    started_at: str
    log: str

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(command) -> list[str]:
    if isinstance(command, str):
        if not command.strip():
            raise ValueError("A start command is required.")
        if len(command) > MAX_COMMAND:
            raise ValueError("The start command is too long.")
        argv = shlex.split(command)
    elif isinstance(command, (list, tuple)):
        argv = [str(part) for part in command]
    else:
        raise ValueError("The start command must be a string or an argv list.")
    if not argv or not argv[0]:
        raise ValueError("A start command is required.")
    return argv


class GuiAppManager:
    def __init__(self, state, *, popen=subprocess.Popen, clock=time.monotonic):
        self.state = Path(state)
        self.popen = popen
        self.clock = clock

    @property
    def record(self) -> Path:
        return self.state / "gui-apps.json"

    @property
    def log_dir(self) -> Path:
        return self.state / "gui-logs"

    def _write(self, app: GuiApp) -> None:
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".gui-apps-", dir=self.state)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(app.to_dict(), stream, indent=2)
                stream.write("\n")
            os.replace(temporary, self.record)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def status(self) -> dict:
        try:
            data = json.loads(self.record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"running": False, "app": None}
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
            return {"running": False, "app": None}
        alive = _alive(data["pid"])
        data["running"] = alive
        return data

    def start(self, command, *, name=None, cwd=None, wait=1.0, confirm=False,
              env=None) -> dict:
        if confirm is not True:
            raise GuiAppRefused("Starting an application needs explicit confirm=True.")
        argv = _parse(command)
        if not 0 <= float(wait) <= 60:
            raise ValueError("wait must be between 0 and 60 seconds.")
        existing = self.status()
        if existing.get("running"):
            raise GuiAppError(
                f"{existing.get('name', 'an application')} is already managed; stop it first."
            )
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        label = " ".join(str(name or Path(argv[0]).stem).split())[:60] or "app"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        log = self.log_dir / f"{stamp}-{label.replace(' ', '-')}.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        environment = None
        if env is not None:
            environment = {**os.environ, **{str(k): str(v) for k, v in env.items()}}
        try:
            with os.fdopen(fd, "wb") as stream:
                process = self.popen(
                    argv, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                    env=environment,
                )
        except OSError as error:
            raise GuiAppError(f"Could not start {label}: {error}") from error
        app = GuiApp(label, int(process.pid), argv,
                     datetime.now(UTC).isoformat(timespec="seconds"), str(log))
        self._write(app)
        if wait:
            time.sleep(float(wait))
        code = process.poll()
        if code not in (None, 0):
            tail = _tail(log)
            try:
                self.record.unlink(missing_ok=True)
            except OSError:
                pass
            raise GuiAppError(
                f"{label} exited immediately with code {code}." + (f" {tail}" if tail else "")
            )
        return {**app.to_dict(), "running": code is None}

    def stop(self, *, force=False) -> dict:
        current = self.status()
        if not current.get("app") and not current.get("pid"):
            return {"stopped": False, "reason": "no managed application"}
        pid = int(current["pid"])
        if not _alive(pid):
            self.record.unlink(missing_ok=True)
            return {"stopped": False, "reason": "already exited", "pid": pid}
        try:
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise GuiAppError(f"Could not stop pid {pid}: {error}") from error
        deadline = self.clock() + (1.0 if force else 3.0)
        while self.clock() < deadline:
            if not _alive(pid):
                break
            time.sleep(0.05)
        still = _alive(pid)
        if still and not force:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            still = _alive(pid)
        if not still:
            self.record.unlink(missing_ok=True)
        return {"stopped": not still, "pid": pid, "forced": bool(force), "alive": still}


def _alive(pid: int) -> bool:
    pid = int(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A terminated child stays addressable until it is reaped. Treat a zombie
    # as stopped so ``stop()`` reports the truth instead of a false failure.
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    state = (completed.stdout or "").strip()
    return bool(state) and not state.startswith("Z")


def _tail(path: Path, limit: int = 400) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return " ".join(stream.read().decode("utf-8", "replace").split())
    except OSError:
        return ""

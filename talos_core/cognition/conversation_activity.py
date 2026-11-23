"""A process-shared, content-free signal for foreground conversation."""

import fcntl
import json
import os
import select
import signal
import subprocess
import time
from contextlib import contextmanager


@contextmanager
def foreground(state):
    marker = state / "conversation.activity"
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        fd = os.open(marker, flags, 0o600)
    except FileNotFoundError:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(marker, flags, 0o600)
    with os.fdopen(fd, "rb+") as file:
        fcntl.flock(file, fcntl.LOCK_SH)
        try:
            yield
        finally:
            os.utime(file.fileno())


def conversation_busy(state, grace=10):
    try:
        file = (state / "conversation.activity").open("rb")
    except FileNotFoundError:
        return False
    with file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return time.time() - os.fstat(file.fileno()).st_mtime < grace


class QuietWorker:
    """Reuse one sleeping child; terminate its process group if a foreground turn begins."""

    def __init__(self, state, args):
        self.state, self.args = state, args
        self.child = None

    def close(self):
        child, self.child = self.child, None
        if child is None:
            return
        try:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            child.wait()
            child.stdin.close()
            child.stdout.close()

    def request(self, kind, **options):
        if conversation_busy(self.state):
            return None
        if self.child is None:
            self.child = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          bufsize=0, start_new_session=True)
        child = self.child
        response = bytearray()
        try:
            child.stdin.write(json.dumps({"kind": kind, **options}).encode() + b"\n")
            while not conversation_busy(self.state):
                if not select.select([child.stdout], [], [], .25)[0]:
                    continue
                chunk = os.read(child.stdout.fileno(), 8192)
                if not chunk:
                    raise RuntimeError("Maintenance child exited before replying.")
                response.extend(chunk)
                if len(response) > 65536:
                    raise ValueError("Maintenance response too large.")
                if response.endswith(b"\n"):
                    message = json.loads(response)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid maintenance response.")
                    if set(message) == {"result"}:
                        return message["result"]
                    if set(message) == {"error"} and isinstance(message["error"], str):
                        raise RuntimeError(f"Maintenance child failed ({message['error']}).")
                    raise ValueError("Invalid maintenance response.")
        except BaseException:
            self.close()
            raise
        self.close()
        return None

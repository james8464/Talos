"""A process-shared, content-free signal for foreground conversation."""

import fcntl
import os
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


def run_while_quiet(state, args):
    """Run a replayable maintenance job. A foreground turn cancels its process group."""
    if conversation_busy(state):
        return None
    with subprocess.Popen(args, stdout=subprocess.PIPE, text=True, start_new_session=True) as job:
        try:
            while not conversation_busy(state):
                try:
                    output, _ = job.communicate(timeout=0.25)
                    if job.returncode:
                        raise subprocess.CalledProcessError(job.returncode, args)
                    return output
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if job.poll() is None:
                try:
                    os.killpg(job.pid, signal.SIGTERM)
                    job.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(job.pid, signal.SIGKILL)
                    job.wait()
                except ProcessLookupError:
                    pass
    return None

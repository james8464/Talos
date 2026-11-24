"""Recover exited Talos workers without competing with a healthy owner."""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from talos_core.recovery import reconnect_delay
from talos_core.runtime.talos_activity import lock_state, record


@dataclass
class Recovery:
    argv: tuple
    lock: str
    attempts: int = 0
    due: float | None = None
    started: float | None = None


def worker_lock(argv):
    """Return the lock name for a worker launched as ``-m module`` or by path."""
    if "-m" in argv:
        module = argv[argv.index("-m") + 1]
    else:
        module = Path(argv[1]).stem
    script = module.rsplit(".", 1)[-1]
    if script == "section_workers":
        return "section-" + argv[argv.index("--engine") + 1] + "-watch"
    return {"overnight_goal": "heartbeat-watch", "vault_watch": "vault-watch",
            "maintenance": "maintenance", "respan_worker": "respan-watch",
            "self_repair": "self-repair-watch",
            "source_observer": "source-observer-watch"}[script]


class WorkerSupervisor:
    def __init__(self, state, children):
        self.state = state
        self.children = children
        self.workers = [Recovery(tuple(p.args), worker_lock(p.args)) for p in children]

    def tick(self, now):
        for index, worker in enumerate(self.workers):
            child = self.children[index]
            code = child.poll()
            if code is None and worker.started is None:
                continue
            if worker.due is not None and now < worker.due:
                continue
            owner = lock_state(self.state / (worker.lock + ".lock"))
            if code is None:
                if now - worker.started >= 5 and owner == "running":
                    record(self.state, "Supervisor", f"{worker.lock} recovered · process stayed up and worker lock is held")
                    worker.attempts, worker.started = 0, None
                continue
            worker.started = None
            if owner != "stopped":
                # A second launcher may legitimately own this worker. Do not adopt or stop it.
                worker.due = now + 30
                continue
            if worker.due is None:
                delay = reconnect_delay(worker.attempts, base=2, cap=300, jitter=False)
                worker.due = now + delay
                record(self.state, "Supervisor", f"{worker.lock} exited ({code}) · will retry in {delay:g}s")
                continue
            try:
                self.children[index] = subprocess.Popen(worker.argv)
            except OSError as error:
                record(self.state, "Supervisor", f"{worker.lock} restart failed ({type(error).__name__})")
                worker.due = now + reconnect_delay(worker.attempts + 1, base=2, cap=300, jitter=False)
            else:
                record(self.state, "Supervisor", f"{worker.lock} restarted · awaiting worker lock")
                worker.due, worker.started = None, now
            worker.attempts += 1

    def watch(self, stop):
        while not stop.wait(2):
            self.tick(time.monotonic())

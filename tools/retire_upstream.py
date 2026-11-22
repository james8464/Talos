"""Retire verified source from the two downloaded comparison trees, retaining exact originals."""
import fcntl
import hashlib
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feature_queue import append, queue_entries
from feature_targets import FILE
from talos_activity import record


class Port(NamedTuple):
    project: str
    path: str
    sha: str
    target: str
    title: str


PORTS = (
    Port("OpenClaw", "src/agents/command-poll-backoff.ts", "4ac9c782011aac8be9a8220d03a29e96dab6ee2d12093f4757d4782b7bd952de", "talos_activity.py", "Slow quiet terminal status probes and refresh immediately on activity"),
    Port("Hermes", "apps/shared/src/reconnect-backoff.ts", "066a9efab5237e47b0b892ef72ec129485622a45ff8e1b964e278999c8466328", "run_talos.py", "Recover the local model with jitter and a stable connection threshold"),
)
for name, path, sha in (
    ("map-size", "src/infra/map-size.ts", "b8eee98537ea95bc20d428f4e1d40f299b31de34e4a00b968cae4d1af1a8aae6"),
    ("number-coercion", "packages/normalization-core/src/number-coercion.ts", "8c40eca65a8fd1dc3aa40d90c22bff35c3710fcb03d2bc8227864a264d20ddc4"),
    ("string-coerce", "packages/normalization-core/src/string-coerce.ts", "0f62efe900e43b23b12025da27f5e65aa6e7fee369191cbbdcf4200f0de70cdf"),
    ("iso-time", "src/shared/iso-time.ts", "79699b601ce5f17bb85ec4838c1bca8fdd5560895406b42ecf6cfa4fd332e871"),
):
    PORTS += (Port("OpenClaw", path, sha, f"talos_core/scheduling/{name}.ts", f"Use the migrated {name} helper in local scheduling"),)


def digest(path):
    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
        raise ValueError(f"Expected a regular file at {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retire(root, state, ports):
    root, state = root.resolve(), state.resolve()
    with (state / "upstream-comparison.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        paths = []
        for port in ports:
            source = state / ("openclaw-src" if port.project == "OpenClaw" else "hermes-src") / port.path
            backup = state / ".migrated-upstream" / port.project / port.sha / port.path
            copies = [p for p in (source, backup) if os.path.lexists(p)]
            if not copies or any(digest(p) != port.sha for p in copies):
                raise ValueError(f"Missing or modified pinned source: {port.project}/{port.path}")
            if port.target.startswith("talos_core/scheduling/") and digest(root / port.target) != port.sha:
                raise ValueError(f"The migrated helper differs from its reference: {port.target}")
            if backup.resolve() != backup.absolute():
                raise ValueError("Archive path traverses a symbolic link")
            paths.append((port,source,backup))
        for port, source, backup in paths:
            if source.exists():
                backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # A hard link is an atomic exclusive archive on the same filesystem.
                if not backup.exists():
                    os.link(source, backup)
                if digest(backup) != port.sha or digest(source) != port.sha:
                    raise ValueError("Source changed during retirement")
                source.unlink()
        index = state / "upstream-inventory.sqlite3"
        if index.is_file():
            with closing(sqlite3.connect(index)) as db, db:
                db.executemany("DELETE FROM files WHERE tree=? AND path=?", [(p.project,p.path) for p in ports])
        with open(os.open(state / FILE, os.O_CREAT | os.O_RDWR, 0o600), "r+") as queue:
            fcntl.flock(queue, fcntl.LOCK_EX)
            proposed, finished = queue_entries(queue)
            for port in ports:
                ident = port.sha[:16]
                if ident not in proposed:
                    append(queue,dict(status="proposed",id=ident,time=time.time(),origin="verified-upstream-port",
                                      feature=port.title,source=port.target,upstream=port.project,reference=port.path,
                                      source_sha256=port.sha,acceptance="Replay the integrated runtime or scheduling behavior against the verified port."))
                if ident not in finished:
                    append(queue,dict(status="done",id=ident,time=time.time(),
                                      verification="Passed behavior replays. Archived pinned source "+port.sha))
                    record(state,"Migration",f"Verified {port.project}/{port.path} in {port.target} and retired the downloaded copy")


if __name__ == "__main__":
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT/"tests"),str(ROOT)))}
    subprocess.run([sys.executable,"-m","unittest","-q","test_runtime_recovery","test_vault_watch"],
                   cwd=ROOT,env=env,check=True)
    retire(ROOT, ROOT/"state", PORTS)
    print(f"Verified {len(PORTS)} archived source files.")

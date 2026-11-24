"""Offline, hash-guarded repairs of two pure algorithms using pinned regression cases."""

import argparse
import ctypes
import difflib
import fcntl
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tokenize
from contextlib import closing
from pathlib import Path

from repair_contracts import (
    CONTRACTS,
    alternatives,
    canonical_source,
    digest,
    read_regular,
    replay,
)
from talos_activity import lock_state, record

ROOT = Path(__file__).resolve().parent
INTERVAL = 600


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def save(path, value):
    write_atomic(path, (json.dumps(value, indent=2) + "\n").encode())


def load(path):
    return json.loads(read_regular(path, 256_000)) if path.is_file() else {}


def exchange(a, b):
    """Swap same-filesystem paths atomically, preserving the displaced inode for verification."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        swap, flag = libc.renameatx_np, 2
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        swap, flag = libc.renameat2, 2
    else:
        raise OSError("Atomic exchange unsupported on this platform")
    # Both paths are absolute, so their directory descriptors are ignored.
    if swap(-1, os.fsencode(a), -1, os.fsencode(b), flag) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def replace_exact(target, expected, data, evidence):
    if digest(read_regular(target)) != expected:
        return False
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".talos-repair-", delete=False
    ) as stream:
        staged = Path(stream.name)
        disposable = True
        try:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(target.stat().st_mode))
            os.fsync(stream.fileno())
            stream.close()
            exchange(target, staged)
            disposable = False
            try:
                displaced = read_regular(staged)
            except (OSError, ValueError):
                # A concurrent rename may have put a directory or symlink here.
                # Return its inode intact, or leave it at staged if another edit
                # has since won. Never read through it or discard uninspected data.
                if read_regular(target) == data:
                    exchange(target, staged)
                    disposable = read_regular(staged) == data
                return False
            if digest(displaced) == expected:
                disposable = True
                return True
            write_atomic(
                evidence / ("conflict-" + digest(displaced) + ".bak"), displaced
            )
            if read_regular(target) == data:
                exchange(target, staged)
                latest = read_regular(staged)
                if latest != data:
                    write_atomic(
                        evidence / ("conflict-" + digest(latest) + ".bak"), latest
                    )
                    if read_regular(target) == displaced:
                        exchange(target, staged)
                        # In a second concurrent race, keep the displaced inode.
                        disposable = read_regular(staged) == displaced
                    return False
            disposable = True
            return False
        finally:
            if disposable:
                staged.unlink(missing_ok=True)


def section_options(state, path, original):
    ledger = state / "section-reviews.sqlite3"
    if not ledger.is_file():
        return
    with closing(
        sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.3)
    ) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='section_candidates'"
        ).fetchone():
            return
        keys = db.execute(
            "SELECT id FROM section_candidates WHERE status='ready' ORDER BY rowid DESC LIMIT 10"
        ).fetchall()
    for (key,) in keys:
        if not re.fullmatch(r"[0-9a-f]{16}", key):
            continue
        folder = state / "section-patches" / key
        proof = load(folder / "receipt.json")
        if proof.get("source") != path or proof.get("target_sha256") != digest(
            original
        ):
            continue
        patch = read_regular(folder / "change.diff", 64_000)
        if digest(patch) != proof.get("diff_sha256"):
            continue
        with tempfile.TemporaryDirectory(prefix="talos-repair-diff-") as scratch:
            target = Path(scratch).resolve() / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
            result = subprocess.run(
                ["git", "apply", "--no-index", "--include=" + path, "-"],
                input=patch,
                cwd=scratch,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                env={
                    "PATH": os.environ.get("PATH", os.defpath),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                },
            )
            if result.returncode == 0 and target.is_file():
                yield "section-" + key, read_regular(target)


def artifact(store, path, original, proposed, origin):
    before, after = digest(original), digest(proposed)
    key = digest(f"{path}|{before}|{after}|{origin}".encode())[:24]
    folder = store / key
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.resolve() != folder.absolute():
        raise ValueError("Unexpected receipt location")
    write_atomic(folder / "before.py.bak", original)
    write_atomic(folder / "after.py.bak", proposed)
    diff = difflib.unified_diff(
        original.decode().splitlines(True),
        proposed.decode().splitlines(True),
        fromfile="a/" + path,
        tofile="b/" + path,
    )
    write_atomic(folder / "change.diff", "".join(diff).encode())
    receipt = dict(
        id=key,
        target=path,
        original_sha256=before,
        candidate_sha256=after,
        rollback_sha256=before,
        origin=origin,
        baseline="red",
        tests=list(CONTRACTS[path].cases),
        created=time.time(),
    )
    return folder, receipt


def settle(root, store, folder, receipt):
    """Recheck the installed revision; recover the same transaction after interruption."""
    path = receipt["target"]
    if path not in CONTRACTS:
        raise ValueError("Unknown repair target")
    target = root / path
    current = read_regular(target)
    before = read_regular(folder / "before.py.bak")
    after = read_regular(folder / "after.py.bak")
    canonical_source(path, before)
    canonical_source(path, after)
    if (
        digest(before) != receipt["original_sha256"]
        or digest(after) != receipt["candidate_sha256"]
    ):
        raise ValueError("Repair evidence changed")
    if current == after:
        try:
            receipt["post_install"] = replay(root, path, current)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            receipt["post_install"] = "unavailable"
        if receipt["post_install"] == "green" and digest(
            read_regular(target)
        ) == digest(after):
            outcome = "verified"
            write_atomic(store / (Path(path).stem + ".green"), after)
        else:
            outcome = (
                "rolled back"
                if replace_exact(target, digest(after), before, folder)
                else "concurrent edit preserved"
            )
    elif current == before:
        outcome = "interrupted before install"
    else:
        outcome = "concurrent edit preserved"
    receipt["outcome"] = outcome
    save(folder / "receipt.json", receipt)
    (store / "pending.json").unlink(missing_ok=True)
    return outcome


def repair(root, state, store, path, original, canonical):
    last_green = store / (Path(path).stem + ".green")
    try:
        options = list(section_options(state, path, original))
    except (
        OSError,
        ValueError,
        KeyError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        # A damaged or unavailable cloud draft must never block deterministic repair.
        record(
            state, "Self-repair", f"Section drafts unavailable ({type(error).__name__})"
        )
        options = []
    if last_green.is_file():
        try:
            options.extend(
                ("last verified local source", version)
                for version in alternatives(path, original, read_regular(last_green))
            )
        except (ValueError, OSError, SyntaxError, tokenize.TokenError):
            record(state, "Self-repair", f"Prior local snapshot unavailable for {path}")
    options.extend(
        ("pinned policy", version)
        for version in alternatives(path, original, canonical)
    )
    tried = {digest(original)}
    last = None
    for origin, proposed in options:
        sha = digest(proposed)
        if sha in tried:
            continue
        tried.add(sha)
        try:
            proposed.decode("utf-8")
        except UnicodeError:
            record(
                state, "Self-repair", f"Rejected binary candidate for {path} · {sha}"
            )
            continue
        folder, proof = artifact(store, path, original, proposed, origin)
        try:
            canonical_source(path, proposed)
            proof["candidate"] = replay(root, path, proposed)
        except (
            ValueError,
            SyntaxError,
            tokenize.TokenError,
            RuntimeError,
            OSError,
            subprocess.SubprocessError,
        ):
            proof["candidate"] = "outside contract or replay unavailable"
        if proof["candidate"] != "green":
            proof["outcome"] = "rejected"
            save(folder / "receipt.json", proof)
            last = proof["id"]
            continue
        proof["outcome"] = "prepared"
        save(folder / "receipt.json", proof)
        save(store / "pending.json", {"id": proof["id"]})
        if not replace_exact(root / path, digest(original), proposed, folder):
            proof["outcome"] = "concurrent edit preserved"
            save(folder / "receipt.json", proof)
            (store / "pending.json").unlink(missing_ok=True)
            return proof["outcome"], proof["id"]
        return settle(root, store, folder, proof), proof["id"]
    return "no verified repair", last


def run_once(root, state, *, clock=time.time):
    root, state = root.resolve(), state.resolve()
    store = state / "self-repair"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    if store.resolve() != store:
        raise ValueError("Unexpected repair state path")
    with (store / "turn.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        status = load(store / "status.json")
        pending = load(store / "pending.json")
        observed_sha = None
        if pending:
            key = pending.get("id", "")
            if not re.fullmatch(r"[0-9a-f]{24}", key):
                raise ValueError("Invalid interrupted repair")
            folder = store / key
            proof = load(folder / "receipt.json")
            if proof.get("id") != key:
                raise ValueError("Interrupted receipt mismatch")
            result = settle(root, store, folder, proof)
            path = proof["target"]
            observed_sha = proof["original_sha256"]
        else:
            for path in sorted(
                CONTRACTS, key=lambda p: status.get(p, {}).get("checked", 0)
            ):
                previous = status.get(path, {})
                try:
                    source = read_regular(root / path)
                except (ValueError, OSError):
                    source = None
                sha = digest(source) if source is not None else None
                if (
                    previous.get("sha") == sha
                    and clock() - previous.get("checked", 0) < INTERVAL
                ):
                    continue
                key = None
                observed_sha = sha
                try:
                    if source is None:
                        raise ValueError("Missing target")
                    canonical = canonical_source(path, source)
                    baseline = replay(root, path, source)
                    if digest(read_regular(root / path)) != sha:
                        result = "concurrent edit preserved"
                    elif baseline == "green":
                        write_atomic(store / (Path(path).stem + ".green"), source)
                        result = "healthy"
                    else:
                        result, key = repair(
                            root, state, store, path, source, canonical
                        )
                except (
                    ValueError,
                    RuntimeError,
                    SyntaxError,
                    tokenize.TokenError,
                    OSError,
                    subprocess.SubprocessError,
                ) as error:
                    result = f"deferred ({type(error).__name__})"
                break
            else:
                return None
        if result == "verified" and key:
            observed_sha = load(store / key / "receipt.json")["candidate_sha256"]
        previous = status.get(path, {})
        status[path] = {
            "outcome": result,
            "checked": clock(),
            "sha": observed_sha,
            "receipt": key,
            "last_verified": key
            if result == "verified"
            else previous.get("last_verified"),
            "last_rollback": key
            if result == "rolled back"
            else previous.get("last_rollback"),
        }
        save(store / "status.json", status)
        message = f"{path} · {result}" + (f" · {key}" if key else "")
        record(state, "Self-repair", message)
        return message


def summary(state):
    try:
        values = load(state / "self-repair/status.json")
        if not values and lock_state(state / "self-repair-watch.lock") != "running":
            return []
        return [
            f"Offline self-repair · {lock_state(state / 'self-repair-watch.lock')}",
            *(
                f"{p} · {values[p]['outcome']}"
                + (
                    f" · last verified {values[p]['last_verified']}"
                    if values[p].get("last_verified")
                    else ""
                )
                for p in CONTRACTS
                if p in values
            ),
        ]
    except (OSError, ValueError, KeyError):
        return ["Offline self-repair · status unavailable"]


def diagnose(root, state):
    from self_diagnosis import tick

    return tick(root, state)


def investigate(root, state):
    from incident_investigation import tick

    return tick(state)


def watch(root, state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "self-repair-watch.lock").open("a") as leader:
        try:
            fcntl.flock(leader, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            for work in (diagnose, run_once, investigate):
                try:
                    outcome = work(root, state)
                    if outcome:
                        print(outcome, flush=True)
                except Exception as error:
                    record(
                        state,
                        "Self-repair",
                        f"{work.__name__} deferred ({type(error).__name__})",
                    )
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        if args.watch:
            watch(ROOT, ROOT / "state")
        else:
            print(run_once(ROOT, ROOT / "state") or "No repair due")
    except KeyboardInterrupt:
        pass

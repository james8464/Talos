"""Keep the Obsidian vault synchronized while Talos runs."""
import argparse
import fcntl
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from talos_core.mac import mac_context
from talos_core.memory import knowledge_map
from talos_core.memory.vault_graph import (
    MARKER,
    ROOT,
    code_name,
    link,
    publish_code,
    source_files,
    write_note,
)
from talos_core.runtime.talos_activity import (
    JOURNAL,
    checked_time,
    record,
    repair_evidence,
    runtime_evidence,
    runtime_locks,
    service_snapshot,
)
from talos_core.self_improvement.feature_targets import FILE
from talos_core.self_improvement.maintenance_schedule import stamp


class VaultPublisher:
    def __init__(self, state, root=ROOT):
        self.state = state
        self.root = root
        self.vault = state / "Talos Memory"
        self.folder = self.vault / "Talos maps"
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.inputs = tuple(state / name for name in (
            mac_context.INDEX_NAME, mac_context.INDEX_NAME + "-wal", "mac-context.json", FILE))
        self.previous = None
        self.source_stamps = None
        self.cache = {}
        self.live_stamps = None
        self.next_live_pulse = 0.

    def publish_live(self):
        journal = self.state / JOURNAL
        watched = [self.folder, self.state / "Talos migrations.md", self.state / "heartbeat.json",
                   self.state / "self-repair/status.json"]
        for name in (JOURNAL, "section-reviews.sqlite3", "respan-review.sqlite3", "self-diagnosis.sqlite3", mac_context.INDEX_NAME):
            watched.extend((self.state / name, self.state / (name + "-wal")))
        locks = runtime_locks(self.state)
        stamps = (tuple(stamp(p) for p in watched), locks)
        now = time.monotonic()
        if stamps == self.live_stamps and now < self.next_live_pulse:
            return
        lines = [MARKER, "# Talos live", "", f"Status sampled {checked_time(time.time())}", "", "## Latest results", ""]
        receipts = runtime_evidence(self.state)
        lines.extend(f"    {line}" for line in (receipts or ["Waiting for the first runtime receipt."]))
        lines.extend(("", "## Self-repair", ""))
        lines.extend(f"    {line}" for line in repair_evidence(self.state))
        if (self.state / "Talos repair replay.md").is_file():
            lines.extend(("", "[[Talos repair replay|Isolated repair test results]]"))
        lines.extend(("", "## Services", ""))
        lines.extend(f"    {line}" for line in service_snapshot(self.state, locks))
        lines.extend(("", "## Knowledge graph", ""))
        for name in ("Talos memory map", "Project connections", "Code index", "Development history", "Recent source edits"):
            if (self.folder / (name + ".md")).is_file():
                lines.append(f"- [[Talos Memory/Talos maps/{name}|{name}]]")
        if (self.state / "Talos migrations.md").is_file():
            lines.append("- [[Talos migrations|Verified upstream migrations]]")
        if journal.is_file():
            try:
                with closing(sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                    rows = db.execute("""SELECT stamp,component,message FROM events
                        WHERE component IN ('Respan','Model','OpenCode','OpenClaude','Index maintenance','Supervisor','Diagnosis','Repair','Self-repair')
                        ORDER BY id DESC LIMIT 8""").fetchall()
            except sqlite3.Error:
                rows = []
            if rows:
                lines.extend(("", "## Recent worker activity", ""))
            for timestamp, component, message in rows:
                lines.append(f"    {checked_time(timestamp)}  {component}  {' '.join(message.splitlines())}")
        write_note(self.state / "Talos live.md", "\n".join(lines) + "\n", managed=True)
        self.live_stamps = stamps
        self.next_live_pulse = now + 60

    def poll(self):
        stamps = {p: stamp(p) for p in source_files(self.root)}
        inputs = tuple(stamp(p) for p in (*self.inputs, self.vault))
        changes = []
        missing = []
        if self.previous is not None:
            missing = [name for name in ("Code index.md", "Talos memory map.md", "Project connections.md")
                       if not (self.folder / name).exists() and not (self.folder / name).is_symlink()]
            if missing:
                self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        code_dirty = stamps != self.source_stamps or "Code index.md" in missing
        if code_dirty:
            earlier = {path: value[1] for path, value in self.cache.items()}
            count = publish_code(self.root, self.folder, stamps, self.cache)
            if count:
                changes.append(f"{count} code map notes updated")
            if self.source_stamps is not None:
                edits = []
                for path in sorted(earlier.keys() | self.cache.keys()):
                    digest = self.cache[path][1] if path in self.cache else None
                    if earlier.get(path) == digest:
                        continue
                    action = "Added" if path not in earlier else "Removed" if digest is None else "Edited"
                    relative = path.relative_to(self.root).as_posix()
                    reference = link("Code", code_name(relative)) if digest else relative
                    edits.append(f"- {datetime.now().astimezone().isoformat(timespec='seconds')} · {action} {reference}")
                    record(self.state, "Source", f"{action} {relative}")
                if edits:
                    history = self.folder / "Recent source edits.md"
                    previous = history.read_text(encoding="utf-8").splitlines()[2:] if history.is_file() else []
                    write_note(history, "# Recent source edits\n\n" + "\n".join((edits + previous)[:50]) + "\n")
        if inputs != self.previous or any(name != "Code index.md" for name in missing):
            count = knowledge_map.publish(self.state)
            if count:
                changes.append(f"{count} project, evidence or finding notes updated")
            if self.previous is not None and inputs[3] != self.previous[3]:
                changes.append("development proposals and verification refreshed")
        elif code_dirty:
            knowledge_map.publish_work(self.state, self.folder)
        restored = [name for name in missing if (self.folder / name).is_file()]
        if restored:
            record(self.state, "Repair", "Rebuilt missing generated graph indexes · " + ", ".join(restored))
        self.publish_live()
        self.previous, self.source_stamps = inputs, stamps
        if changes:
            result = " · ".join(changes)
            record(self.state, "Obsidian", result)
            return result
        return None


def watch(state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "vault-watch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("The Obsidian publisher is already running.", flush=True)
            return
        publisher = VaultPublisher(state)
        print("Watching Talos memory and source for Obsidian updates.", flush=True)
        while True:
            try:
                message = publisher.poll()
                if message:
                    print(message, flush=True)
            except (OSError, ValueError, sqlite3.Error) as error:
                print(f"Vault update deferred ({type(error).__name__})", file=sys.stderr, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    args = parser.parse_args()
    try:
        watch(args.state)
    except KeyboardInterrupt:
        pass

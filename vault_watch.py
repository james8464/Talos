"""Keep the Obsidian vault synchronized while Talos runs."""
import argparse
import fcntl
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime

import knowledge_map
import mac_context
from feature_targets import FILE
from maintenance_schedule import stamp
from talos_activity import JOURNAL, record
from vault_graph import MARKER, ROOT, code_name, link, publish_code, source_files, write_note


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

    def publish_live(self):
        journal = self.state / JOURNAL
        stamps = tuple(stamp(p) for p in (journal, self.state / (JOURNAL + "-wal"), self.folder, self.state / "Talos migrations.md"))
        if stamps == self.live_stamps:
            return
        lines = [MARKER, "# Talos live", ""]
        for name in ("Talos memory map", "Project connections", "Code index", "Development history", "Recent source edits"):
            if (self.folder / (name + ".md")).is_file():
                lines.append(f"- [[Talos Memory/Talos maps/{name}|{name}]]")
        if (self.state / "Talos migrations.md").is_file():
            lines.append("- [[Talos migrations|Verified upstream migrations]]")
        if journal.is_file():
            from contextlib import closing
            with closing(sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
                    rows = db.execute("""SELECT stamp,component,message FROM events
                        WHERE component IN ('Respan','Model','Migration') ORDER BY id DESC LIMIT 8""").fetchall()
                    if rows:
                        lines.extend(("", "## Latest reviewer and model activity", ""))
                    for timestamp, component, message in rows:
                        clock = datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                        lines.append(f"    {clock}  {component}  {' '.join(message.splitlines())}")
        write_note(self.state / "Talos live.md", "\n".join(lines) + "\n", managed=True)
        self.live_stamps = stamps

    def poll(self):
        stamps = {p: stamp(p) for p in source_files(self.root)}
        inputs = tuple(stamp(p) for p in (*self.inputs, self.vault))
        changes = []
        code_dirty = stamps != self.source_stamps
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
        if inputs != self.previous:
            count = knowledge_map.publish(self.state)
            if count:
                changes.append(f"{count} project, evidence or finding notes updated")
            if self.previous is not None and inputs[3] != self.previous[3]:
                changes.append("development proposals and verification refreshed")
        elif code_dirty:
            knowledge_map.publish_work(self.state, self.folder)
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

"""Poll local source metadata; schedule replayable sync and source review jobs."""

import sqlite3
import subprocess
import time

from conversation_activity import conversation_busy


def stamp(path):
    try:
        info = path.stat()
        return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    except FileNotFoundError:
        return None


def source_versions(consent, databases, imports, kinds):
    """Fingerprint configuration, enabled databases, SQLite WALs and local export files."""
    files = {consent}
    for path in databases:
        files.update((path, path.with_name(path.name + "-wal")))
    local = tuple((path, stamp(path)) for path in sorted(files))
    exports = {}
    for kind in kinds:
        folder = imports / kind
        if folder.is_dir() and not folder.is_symlink():
            exports[kind] = tuple((path, stamp(path)) for path in sorted(folder.rglob("*"))
                                  if not path.is_symlink() and path.is_file())
    return local, exports


class MaintenanceSchedule:
    def __init__(self):
        self.sources = self.code = None
        self.next_files = self.next_review = 0
        self.counts = {}
        self.outcome = "Awaiting first review"

    def poll(self, state, *, sources, code, run):
        """Return report lines after a check, or None when foreground activity defers it."""
        if conversation_busy(state):
            return None
        now = time.monotonic()
        lines = []
        try:
            current = sources()
            files_due = now >= self.next_files
            if current != self.sources or files_due:
                previous = self.sources[1] if self.sources else {}
                exports = current[1]
                changed = sorted(k for k in previous.keys() | exports.keys()
                                 if previous.get(k) != exports.get(k))
                counts = run("sync", changed=changed, files=files_due)
                if counts is None:
                    return None
                self.counts, self.sources = counts, current
                if files_due:
                    self.next_files = now + 900
            lines.append(f"{sum(self.counts.values()):,} indexed records · " +
                         ", ".join(f"{key} {value:,}" for key, value in self.counts.items()))
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
            lines.append(f"Memory refresh failed ({type(error).__name__})")
        if conversation_busy(state):
            return None
        try:
            current_code = code()
            if current_code != self.code or now >= self.next_review:
                result = run("review")
                if result is None:
                    return None
                self.outcome, self.code = result, current_code
                self.next_review = now + 1800
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.outcome = f"Review failed ({type(error).__name__})"
        lines.append("Latest source review · " + self.outcome)
        return lines

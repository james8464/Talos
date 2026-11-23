"""Poll local source metadata; schedule replayable sync and source review jobs."""

import sqlite3
import subprocess
import time

from talos_core.cognition.conversation_activity import conversation_busy


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
        self.next_memory_review = 0
        self.next_health = 0
        self.next_reflection = 0
        self.next_initiative = 0
        self.next_capabilities = 0
        self.next_coder = 0
        self.counts = {}
        self.outcome = "Awaiting first review"
        self.memory_outcome = "Awaiting an idle review"
        self.health_outcome = "Awaiting self-check"
        self.reflection_outcome = "Awaiting reflection"
        self.initiative_outcome = "Awaiting initiative"
        self.capabilities_outcome = "Awaiting capability audit"
        self.coder_outcome = "Awaiting planned initiative"

    def poll_memory(self, state, *, run):
        now = time.monotonic()
        if now >= self.next_memory_review and not conversation_busy(state):
            try:
                result = run("memory_review")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.memory_outcome = f"Review unavailable ({type(error).__name__})"
                self.next_memory_review = now + 300
            else:
                if result is not None:
                    self.memory_outcome = result
                    self.next_memory_review = now + 120
        return self.memory_outcome

    def poll_reflection(self, state, *, run):
        """Recompute contextual relations every ten minutes."""
        now = time.monotonic()
        if now >= self.next_reflection and not conversation_busy(state):
            try:
                result = run("reflection")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.reflection_outcome = f"Reflection unavailable ({type(error).__name__})"
                self.next_reflection = now + 300
            else:
                if result is not None:
                    self.reflection_outcome = str(result)
                    self.next_reflection = now + 600
        return self.reflection_outcome

    def poll_coder(self, state, *, run):
        """Draft and verify one queued initiative leaf every thirty minutes."""
        now = time.monotonic()
        if now >= self.next_coder and not conversation_busy(state):
            try:
                result = run("initiative_code")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.coder_outcome = f"Coder unavailable ({type(error).__name__})"
                self.next_coder = now + 1800
            else:
                if result is not None:
                    self.coder_outcome = str(result)
                    self.next_coder = now + 1800
        return self.coder_outcome

    def poll_capabilities(self, state, *, run):
        """Compare the tool surface with pinned upstream projects every 12 hours."""
        now = time.monotonic()
        if now >= self.next_capabilities and not conversation_busy(state):
            try:
                result = run("capabilities")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.capabilities_outcome = f"Capability audit unavailable ({type(error).__name__})"
                self.next_capabilities = now + 1800
            else:
                if result is not None:
                    self.capabilities_outcome = str(result)
                    self.next_capabilities = now + 12 * 3600
        return self.capabilities_outcome

    def poll_initiative(self, state, *, run):
        """Think of and queue new capabilities four times a day."""
        now = time.monotonic()
        if now >= self.next_initiative and not conversation_busy(state):
            try:
                result = run("initiative")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.initiative_outcome = f"Initiative unavailable ({type(error).__name__})"
                self.next_initiative = now + 1800
            else:
                if result is not None:
                    self.initiative_outcome = str(result)
                    self.next_initiative = now + 4 * 3600
        return self.initiative_outcome

    def poll_health(self, state, *, run):
        """Run a full self-check every six hours, deferred during conversation."""
        now = time.monotonic()
        if now >= self.next_health and not conversation_busy(state):
            try:
                result = run("health")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.health_outcome = f"Self-check unavailable ({type(error).__name__})"
                self.next_health = now + 600
            else:
                if result is not None:
                    self.health_outcome = str(result)
                    self.next_health = now + 6 * 3600
        return self.health_outcome

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
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
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

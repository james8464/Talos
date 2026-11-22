"""Keep Talos memory current and publish a timestamped local activity report."""

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from contextlib import closing
from conversation_activity import QuietWorker
from maintenance_schedule import MaintenanceSchedule, source_versions, stamp
import mac_context
from feature_targets import FILE, TARGETS, change_events
from talos_activity import active_phase, lock_state, record

STATE = Path(__file__).resolve().parent / "state"
REPORT = Path("Talos Memory") / "Talos activity.md"


def expose_report(state):
    note = state / REPORT
    note.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        note.symlink_to("../Maintenance.txt")
    except FileExistsError:
        pass


def publish(state, lines):
    temporary = state / "maintenance-status.tmp"
    temporary.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(state / "Maintenance.txt")


def queue_summary(path):
    pending = set()
    completed = 0
    latest = None
    with path.open() as events:
        for event in change_events(events):
            if event["status"] == "proposed":
                pending.add(event["id"])
            elif event["status"] == "done" and event["id"] in pending:
                pending.remove(event["id"])
                completed += 1
                latest = event["verification"]
    lines = [f"{len(pending)} requests pending · {completed} completed"]
    if latest:
        lines.append("Last verified change · " + latest)
    return lines


IMPORT_KINDS = ("Calendar", "Reminders", "OCR", "Notes", "Projects")


def enabled_versions(state, home):
    enabled = mac_context.enabled(state)
    databases = (source for app, paths in mac_context.sources(home / "Library").items()
                 if app in enabled for source in paths)
    return source_versions(state / "mac-context.json", databases, state / "knowledge-imports", IMPORT_KINDS)


def job_loop(state, model, home):
    # Only the child imports the extractors and feature reviewer.
    import knowledge_sources
    from personal_index import sync
    for line in sys.stdin:
        try:
            request = json.loads(line)
            kind = request["kind"]
            if kind == "sync":
                imports = [(name, dict(knowledge_sources.collect(state, home, name)).items())
                           for name in request.get("changed", ())]
                result = sync(state, home / "Library", knowledge=imports,
                              refresh_existing_files=request.get("files", False))
                import memory_connections
                memory_connections.refresh(state)
            elif kind == "review":
                import feature_queue
                from upstream_comparison import compare
                root = Path(__file__).resolve().parent
                try:
                    comparison = ("Respan reviewer active; see reviewer events"
                                  if lock_state(state / "respan-watch.lock") == "running" else compare(root, state, model))
                except (OSError, ValueError, RuntimeError) as error:
                    comparison = f"upstream comparison deferred ({type(error).__name__})"
                result = f"{feature_queue.review(state, model)} · {comparison}"
            elif kind == "index_maintenance":
                from index_maintenance import merge_step
                result = merge_step(state)
            elif kind == "memory_review":
                import memory_review
                result = memory_review.review(state, model)
            else:
                raise ValueError("Unknown maintenance job.")
            message = {"result": result}
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
            message = {"error": type(error).__name__}
        print(json.dumps(message), flush=True)


def watch(state, model):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    expose_report(state)
    worker = QuietWorker(state, [sys.executable, __file__, "--state", str(state), "--model", model, "--jobs"])
    with closing(worker), (state / "maintenance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        schedule = MaintenanceSchedule()
        cycle = 0
        next_index_check = 0.0
        index_outcome = "Awaiting an idle index check"
        home = Path.home()

        def observed_job(kind, **options):
            labels = {"sync": "Refresh enabled memory sources", "review": "Compare code and review proposals",
                      "memory_review": "Check project evidence for AI memory review"}
            label = labels[kind]
            record(state, "Maintenance", label + " · started")
            started = time.monotonic()
            try:
                with active_phase(state, label):
                    result = worker.request(kind, **options)
            except Exception as error:
                record(state, "Maintenance", f"{label} · failed ({type(error).__name__})")
                raise
            if result is None:
                outcome = "deferred for foreground activity"
            elif isinstance(result, dict):
                outcome = f"{sum(result.values()):,} indexed records"
            else:
                outcome = str(result)
            record(state, "Maintenance", f"{label} · {outcome} · {time.monotonic()-started:.1f}s")
            return result

        while True:
            lines = schedule.poll(state,
                sources=lambda: enabled_versions(state, home),
                code=lambda: tuple(stamp(Path(__file__).resolve().parent / name) for name in TARGETS),
                run=observed_job)
            if lines is None:
                worker.close()
                time.sleep(5)
                continue
            if (state / mac_context.INDEX_NAME).is_file():
                if time.monotonic() >= next_index_check:
                    try:
                        result = worker.request("index_maintenance")
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        index_outcome = f"Index check unavailable ({type(error).__name__})"
                        next_index_check = time.monotonic() + 60
                    else:
                        if result is not None:
                            index_outcome = result["message"]
                            next_index_check = time.monotonic() + result["retry_in"]
                lines.append("Search index maintenance · " + index_outcome)
                lines.append("Latest AI memory review · " + schedule.poll_memory(state, run=observed_job))
            cycle += 1
            label = datetime.now().astimezone().isoformat(timespec="seconds")
            lines.insert(0, f"Memory check {cycle} · {label}")
            try:
                lines.extend(queue_summary(state / FILE))
            except (OSError, ValueError, KeyError):
                lines.append("Queue summary unavailable")
            publish(state, lines)
            time.sleep(60)


def live(state, model):
    """Show actual worker reports and newly appended queue events in Terminal."""
    child = subprocess.Popen([sys.executable, __file__, "--state", str(state), "--model", model], start_new_session=True)
    previous = None
    offset = 0
    print("Talos maintenance · Ctrl-C closes this view", flush=True)
    try:
        while True:
            try:
                report = (state / "Maintenance.txt").read_text().strip()
                if report != previous:
                    print(report, flush=True)
                    previous = report
                with (state / FILE).open() as queue:
                    queue.seek(offset)
                    for line in queue:
                        if not line.endswith("\n"):
                            break
                        event = json.loads(line)
                        offset += len(line.encode())
                        print(event.get("origin", event["status"]), "·",
                              event.get("feature") or event.get("verification"), flush=True)
            except FileNotFoundError:
                pass
            if child.poll() not in (None, 0):
                raise RuntimeError("Maintenance worker could not start")
            time.sleep(2)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--model", default="qwen3:4b")
    cli.add_argument("--live", action="store_true")
    cli.add_argument("--state", type=Path, default=STATE)
    cli.add_argument("--jobs", action="store_true", help=argparse.SUPPRESS)
    args = cli.parse_args()
    os.environ.setdefault("TALOS_OLLAMA_PORT", "11435")
    if args.jobs:
        job_loop(args.state, args.model, Path.home())
    else:
        try:
            (live if args.live else watch)(args.state, args.model)
        except KeyboardInterrupt:
            pass

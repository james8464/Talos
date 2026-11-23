"""Keep Talos memory current and publish a timestamped local activity report."""

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from talos_core.cognition import cognitive_engine
from talos_core.cognition.conversation_activity import QuietWorker
from talos_core.mac import mac_context
from talos_core.runtime.talos_activity import active_phase, lock_state, record
from talos_core.self_improvement.feature_targets import FILE, TARGETS, proposal_ledger
from talos_core.self_improvement.maintenance_schedule import MaintenanceSchedule, source_versions, stamp

STATE = Path(__file__).resolve().parents[2] / "state"
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
    with path.open() as events:
        proposed, outcomes = proposal_ledger(events)
    completed = [item for key, item in outcomes.items() if key in proposed and item["status"] == "done"]
    declined = sum(key in proposed and item["status"] == "declined" for key, item in outcomes.items())
    summary = f"{len(proposed.keys() - outcomes.keys())} requests pending · {len(completed)} completed"
    if declined:
        summary += f" · {declined} declined"
    lines = [summary]
    if completed:
        lines.append("Last verified change · " + completed[-1]["verification"])
    return lines


IMPORT_KINDS = ("Calendar", "Reminders", "OCR", "Notes", "Projects")


def enabled_versions(state, home):
    enabled = mac_context.enabled(state)
    databases = (source for app, paths in mac_context.sources(home / "Library").items()
                 if app in enabled for source in paths)
    return source_versions(state / "mac-context.json", databases, state / "knowledge-imports", IMPORT_KINDS)


def job_loop(state, model, home):
    # Only the child imports the extractors and feature reviewer.
    report_root = Path(__file__).resolve().parents[2]
    from talos_core.memory import knowledge_sources
    from talos_core.memory.personal_index import sync
    for line in sys.stdin:
        try:
            request = json.loads(line)
            kind = request["kind"]
            if kind == "sync":
                imports = [(name, dict(knowledge_sources.collect(state, home, name)).items())
                           for name in request.get("changed", ())]
                result = sync(state, home / "Library", knowledge=imports,
                              refresh_existing_files=request.get("files", False))
                from talos_core.memory import memory_connections
                memory_connections.refresh(state)
            elif kind == "review":
                from talos_core.self_improvement import feature_queue
                from talos_core.self_improvement.upstream_comparison import compare
                root = Path(__file__).resolve().parents[2]
                try:
                    comparison = ("Respan reviewer active; see reviewer events"
                                  if lock_state(state / "respan-watch.lock") == "running" else compare(root, state, model))
                except (OSError, ValueError, RuntimeError) as error:
                    comparison = f"upstream comparison deferred ({type(error).__name__})"
                result = f"{feature_queue.review(state, model)} · {comparison}"
            elif kind == "index_maintenance":
                from talos_core.self_improvement.index_maintenance import merge_step
                result = merge_step(state)
            elif kind == "memory_review":
                from talos_core.memory import memory_review
                result = memory_review.review(state, model)
            elif kind == "capabilities":
                from talos_core.self_improvement import capability_audit
                queued = capability_audit.queue_gaps(report_root, state, limit=2)
                result = f"{len(capability_audit.report(report_root, state)['gaps'])} gap(s); {len(queued)} queued"
            elif kind == "initiative_code":
                from talos_core.self_improvement import initiative_coder
                try:
                    inference = initiative_coder.local_infer(model)
                    report = initiative_coder.run_once(state, report_root, infer=inference, apply=True, limit=3)
                    if report.get("ok"):
                        result = "applied " + ", ".join(
                            item.get("target", "") for item in report.get("results", ())
                            if item.get("applied"))
                    else:
                        result = report.get("reason") or "no verified patch"
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                    result = f"initiative coding deferred ({type(error).__name__})"
            elif kind == "initiative":
                from talos_core.self_improvement import initiative
                report = initiative.cycle(state, report_root, limit=2)
                result = f"{report['count']} new initiative(s); {len(report['accepted'])} queued"
            elif kind == "reflection":
                from talos_core.knowledge_reflection import reflect
                from talos_core.memory.agent_memory import open_memory
                db_path = state / "agent.sqlite3"
                if db_path.is_file():
                    with closing(open_memory(db_path)) as db:
                        result = f"{len(reflect(db, state))} related-context edge(s)"
                else:
                    result = "agent database unavailable"
            elif kind == "health":
                from talos_core.self_improvement.health import failures, health, report_failures
                report = health(report_root, state, quick=False, lint=True, tests=True)
                if report["ok"]:
                    result = "healthy"
                else:
                    issues = list(failures(report))
                    report_failures(report, state)
                    try:
                        from talos_core.self_improvement.improve_talos import run_improvement_cycle
                        cycle = run_improvement_cycle(state, root=report_root, apply_patches=True)
                        applied = [item for item in cycle["candidates_verified"] if item.get("applied")]
                        result = (f"attention needed · {issues[0] if issues else 'check failed'}"
                                  f" · {len(applied)} patch(es) applied")
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                        result = (f"attention needed · {issues[0] if issues else 'check failed'}"
                                  f" · repair deferred ({type(error).__name__})")
            elif kind == "cognitive":
                from talos_core.memory.agent_memory import open_memory
                db_path = state / "agent.sqlite3"
                if db_path.is_file():
                    with closing(open_memory(db_path)) as db:
                        result = cognitive_engine.think_once(db, state)["headline"]
                else:
                    result = "agent database unavailable"
            else:
                raise ValueError("Unknown maintenance job.")
            message = {"result": result}
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
            message = {"error": type(error).__name__}
        print(json.dumps(message), flush=True)


def watch(state, model):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    expose_report(state)
    worker = QuietWorker(state, [sys.executable, "-m", "talos_core.runtime.maintenance", "--state", str(state), "--model", model, "--jobs"])
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
                      "memory_review": "Check project evidence for AI memory review",
                      "health": "Self-check Talos source and tests",
                      "initiative": "Propose and decompose new capabilities",
                      "reflection": "Reflect on related knowledge and conversations",
                      "capabilities": "Audit tools against OpenClaw and Hermes",
                      "initiative_code": "Code and verify a planned initiative"}
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
                code=lambda: tuple(stamp(Path(__file__).resolve().parents[2] / name) for name in TARGETS),
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
            lines.append("Self-check · " + schedule.poll_health(state, run=observed_job))
            lines.append("Knowledge reflection · " + schedule.poll_reflection(state, run=observed_job))
            lines.append("Initiative · " + schedule.poll_initiative(state, run=observed_job))
            lines.append("Capability audit · " + schedule.poll_capabilities(state, run=observed_job))
            lines.append("Initiative coder · " + schedule.poll_coder(state, run=observed_job))
            cycle += 1
            label = datetime.now().astimezone().isoformat(timespec="seconds")
            lines.insert(0, f"Memory check {cycle} · {label}")
            try:
                lines.extend(queue_summary(state / FILE))
            except (OSError, ValueError, KeyError):
                lines.append("Queue summary unavailable")
            if (state / "agent.sqlite3").is_file():
                try:
                    from talos_core.memory.agent_memory import open_memory
                    with closing(open_memory(state / "agent.sqlite3")) as db:
                        thought = cognitive_engine.think_once(db, state)
                    lines.append("Cognitive brief · " + thought["headline"])
                except (OSError, ValueError, sqlite3.Error):
                    lines.append("Cognitive brief unavailable")
            publish(state, lines)
            time.sleep(60)


def live(state, model):
    """Show actual worker reports and newly appended queue events in Terminal."""
    child = subprocess.Popen([sys.executable, "-m", "talos_core.runtime.maintenance", "--state", str(state), "--model", model], start_new_session=True)
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

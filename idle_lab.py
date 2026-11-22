"""Low-priority, local coding trials based on repeated room timing failures."""

import difflib
import ast
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from agent_memory import open_memory
import talos_activity
from improve_talos import ROOT, SOURCE_PATTERNS
from local_runtime import check_ollama_model, request_ollama_json


def pending_stages(db, now):
    return db.execute("""SELECT s.stage,s.occurrences,s.worst_ms FROM room_slow_stages s
        WHERE s.occurrences>=2 AND NOT EXISTS(SELECT 1 FROM coding_trials t WHERE
        t.stage=s.stage AND (t.occurrences>=s.occurrences OR t.started>?))
        ORDER BY s.occurrences DESC,s.worst_ms DESC,s.stage""", (now - 86400,))


def power_and_idle():
    try:
        power = subprocess.check_output(
            ["/usr/bin/pmset", "-g", "batt"], text=True, timeout=2
        )
        if "'AC Power'" not in power:
            return False
        hid = subprocess.check_output(
            ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-r", "-d", "1"],
            text=True,
            timeout=3,
        )
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', hid)
        return bool(match and int(match[1]) >= 300_000_000_000)
    except (OSError, subprocess.SubprocessError):
        return False


class IdleReviewDeferred(Exception):
    """Leave this transcript queued until local inference can run."""


class IdleLab:
    def __init__(self, db, model):
        self.db, self.model = db, model
        self.lock = threading.Lock()
        self.job = None
        self.trial_id = None
        self.review_job = None
        self.quiet_until = time.monotonic() + 300
        self.next_feature_review = 0
        self.next_check = time.monotonic() + 300

    def pause(self):
        with self.lock:
            self.next_check = self.quiet_until = time.monotonic() + 300
            if self.review_job and self.review_job.poll() is None:
                self.review_job.terminate()
            if self.job and self.job.poll() is None:
                try:
                    os.killpg(self.job.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                with closing(open_memory(self.db)) as db, db:
                    db.execute(
                        "UPDATE coding_trials SET status='paused for activity' WHERE id=? AND status='started'",
                        (self.trial_id,),
                    )

    def infer(self, messages, **options):
        if time.monotonic() < self.quiet_until or not power_and_idle():
            raise IdleReviewDeferred()
        with self.lock:
            if time.monotonic() < self.quiet_until or (
                self.job and self.job.poll() is None
            ):
                raise IdleReviewDeferred()
            child = self.review_job = subprocess.Popen(
                [sys.executable, str(ROOT / "idle_lab.py"), "--review", self.model],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        try:
            with talos_activity.operation(self.db.parent, "Idle review", "Run idle model review"):
                output, _ = child.communicate(json.dumps([messages, options]), timeout=65)
                if child.returncode:
                    raise IdleReviewDeferred()
            return output
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise IdleReviewDeferred()
        finally:
            with self.lock:
                self.review_job = None

    def tick(self):
        now = time.monotonic()
        with self.lock:
            running = self.job and self.job.poll() is None
            if now < self.next_check:
                return
            self.next_check = now + 30
        eligible = power_and_idle()
        if running:
            if not eligible:
                self.pause()
            return
        if not eligible:
            return
        with self.lock:
            if self.next_check > now + 31:
                return
            with closing(open_memory(self.db)) as db, db:
                row = pending_stages(db, time.time()).fetchone()
                if not row:
                    if now >= self.next_feature_review:
                        self.next_feature_review = now + 3600
                        self.trial_id = None
                        self.job = subprocess.Popen(
                            [sys.executable, str(ROOT / "feature_queue.py"), "--state",
                             str(self.db.parent), "--model", self.model],
                            start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                cursor = db.execute(
                    "INSERT INTO coding_trials(stage,occurrences,started,status) VALUES(?,?,?,'started')",
                    (row["stage"], row["occurrences"], time.time()),
                )
                self.trial_id = cursor.lastrowid
            self.job = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "idle_lab.py"),
                    str(self.db),
                    str(cursor.lastrowid),
                    self.model,
                ],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def local_edit(stage, original, model):
    check_ollama_model(model)
    prompt = (
        "Repeated slow "
        + stage
        + ". Improve latency while preserving behavior. Return JSON with before and after strings. "
        "Before must be one exact contiguous excerpt of this Python file. After replaces only that excerpt. "
        "If you cannot improve it, return empty strings. Source:\n"
        + original
    )
    response = request_ollama_json(
        "POST",
        "/api/generate",
        timeout=120,
        payload={
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 1200},
        },
    )
    return json.loads(response.get("response", ""))


def experiment(db_path, trial, model, *, reviewer=None):
    with closing(open_memory(db_path)) as db:
        row = db.execute("SELECT * FROM coding_trials WHERE id=?", (trial,)).fetchone()
        if not row:
            return
        stage = row["stage"]
        target = {
            "speech synthesis": "room_voice.py",
            "memory retrieval": "personal_facts.py",
        }.get(stage, "room_dialogue.py")
        status = "no candidate"
        outdir = db_path.parent / "proposals"
        try:
            with tempfile.TemporaryDirectory(prefix="talos-lab-") as tmp:
                work = Path(tmp)
                for pattern in SOURCE_PATTERNS:
                    for src in ROOT.glob(pattern):
                        if src.is_symlink() or not src.is_file():
                            continue
                        dest = work / src.relative_to(ROOT)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dest)
                original = (work / target).read_text()
                if reviewer is not None:
                    # Only code from a fixed target and a non-personal stage go to the reviewer.
                    proposal = reviewer(stage, original)
                else:
                    proposal = local_edit(stage, original, model)
                before, after = proposal.get("before"), proposal.get("after")
                if before == after == "":
                    return
                if (
                    not isinstance(before, str)
                    or not before.strip()
                    or not isinstance(after, str)
                    or before == after
                    or original.count(before) != 1
                ):
                    raise ValueError("No unambiguous edit.")
                modified = original.replace(before, after)
                diff = "".join(
                    difflib.unified_diff(
                        original.splitlines(True),
                        modified.splitlines(True),
                        "a/" + target,
                        "b/" + target,
                    )
                )
                paths = subprocess.run(
                    ["git", "apply", "--numstat", "-"],
                    input=diff,
                    cwd=work,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                if not re.fullmatch(r"\d+\t\d+\t" + re.escape(target) + r"\n", paths):
                    raise ValueError("unexpected scope")
                subprocess.run(
                    ["git", "apply", "--whitespace=error", "-"],
                    input=diff,
                    cwd=work,
                    text=True,
                    check=True,
                )
                ast.parse((work / target).read_text())
                outdir.mkdir(mode=0o700, exist_ok=True)
                filename = outdir / f"{db_path.stem}-trial-{trial}.patch" if reviewer else outdir / f"trial-{trial}.patch"
                filename.write_text(diff)
                status = "applies and parses; replay pending"
                # Candidate code can read only this copy and the installed runtime. No network access.
                runtime = Path(sys.base_prefix).resolve()
                writes = work / "scratch"
                writes.mkdir()
                profile = "(version 1)(deny default)(allow process-exec process-fork process-info* sysctl-read file-read-metadata)"
                for base in (
                    work.resolve(),
                    runtime,
                    Path("/System"),
                    Path("/usr/lib"),
                    Path("/usr/bin"),
                    Path("/Library/Apple"),
                ):
                    profile += (
                        "(allow file-read* (subpath " + json.dumps(str(base)) + "))"
                    )
                profile += (
                    "(allow file-write* (subpath "
                    + json.dumps(str(writes.resolve()))
                    + "))"
                )
                env = {
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(writes),
                    "HOME": str(writes),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }

                def replay():
                    return subprocess.run(
                        [
                            "/usr/bin/sandbox-exec",
                            "-p",
                            profile,
                            sys.executable,
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                            "-p",
                            "test_room_voice.py"
                            if target == "room_voice.py"
                            else "test_room_refinement.py" if target == "personal_facts.py" else "test_room_dialogue.py",
                        ],
                        env=env,
                        cwd=work,
                        timeout=90,
                        capture_output=True,
                    )

                subprocess.run(
                    ["git", "apply", "-R", "-"],
                    input=diff,
                    cwd=work,
                    text=True,
                    check=True,
                )
                start = time.monotonic()
                baseline_run = replay()
                if baseline_run.returncode == 0:
                    baseline = time.monotonic() - start
                    subprocess.run(
                        ["git", "apply", "-"],
                        input=diff,
                        cwd=work,
                        text=True,
                        check=True,
                    )
                    start = time.monotonic()
                    ok = replay().returncode == 0
                    status = f"replay {'passed' if ok else 'failed'}; suite seconds {baseline:.3f} baseline, {time.monotonic() - start:.3f} candidate"
                elif b"sandbox_apply" in baseline_run.stderr:
                    status = "applies and parses; OS sandbox unavailable for replay"
                else:
                    status = "applies and parses; baseline replay failed"
        except (
            RuntimeError,
            ValueError,
            AttributeError,
            SyntaxError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            status = "review stopped: " + type(error).__name__
        finally:
            with db:
                db.execute(
                    "UPDATE coding_trials SET status=? WHERE id=?", (status, trial)
                )


if __name__ == "__main__":
    os.nice(10)
    if sys.argv[1] == "--review":
        from agent import model_text

        messages, options = json.load(sys.stdin)
        check_ollama_model(sys.argv[2])
        print(model_text(messages, model=sys.argv[2], **options))
    else:
        db_path = Path(sys.argv[1])
        with talos_activity.operation(db_path.parent, "Idle lab", "Evaluate a code change"):
            experiment(db_path, int(sys.argv[2]), sys.argv[3])

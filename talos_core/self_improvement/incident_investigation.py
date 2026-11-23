"""Audit unresolved local incidents against Talos source and existing test assertions."""

import ast
import fcntl
import hashlib
import json
import re
import time
from pathlib import Path

from talos_core.cognition.conversation_activity import conversation_busy
from talos_core.runtime.talos_activity import record
from talos_core.self_improvement import local_source_model as model
from talos_core.self_improvement import self_diagnosis
from talos_core.self_improvement.repair_contracts import read_regular
from talos_core.self_improvement.self_repair import load, save

ROOT = Path(__file__).resolve().parents[2]
WINDOW = 600
TARGETS = {
    "playback": ("talos_core/runtime/voice_recovery.py", ("recent", "report", "handle")),
    "voice": ("talos_core/room/room_recollection.py", ("immediate_recall",)),
    "memory": ("talos_core/self_improvement/index_maintenance.py", ("merge_step",)),
    "vault": ("talos_core/memory/vault_graph.py", ("publish_code", "sync_notes")),
    "room": ("talos_core/room/room_work.py", ("apply_order",)),
    "review": ("talos_core/self_improvement/section_patches.py", ("checked_patch",)),
    "scheduling": ("talos_core/self_improvement/maintenance_schedule.py", ("poll",)),
    "console": ("agent.py", ("interactive_chat",)),
}
PASSED = {"fixture passed; symptom unverified", "fixture passing on recheck; live symptom unverified"}
ELIGIBLE = PASSED | {"fixture assertion failed", "repeatable fixture failure"}
EXCEPTIONS = {"KeyError", "TypeError", "ValueError", "IndexError", "AttributeError", "AssertionError",
              "IntegrityError", "UnicodeDecodeError", "FileNotFoundError", "RuntimeError"}
GRAMMAR = r'''root ::= "quiet" | "{" "\"source\":" id ",\"assertion\":" id ",\"hypothesis\":" phrase ",\"regression\":" phrase "}"
id ::= [1-9] [0-9]?
phrase ::= "\"" [a-zA-Z0-9 .,()_'/+-]{12,180} "\""
'''


def excerpts(root, relative, names, *, assertions=False):
    source = read_regular(root / relative, 320_000).decode()
    result = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef) or fn.name not in names:
            continue
        rows = {}
        for node in ast.walk(fn):
            if assertions:
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr.startswith("assert")):
                    continue
                snippet = ast.unparse(node)
            elif isinstance(node, ast.If):
                snippet = "if " + ast.unparse(node.test)
            elif isinstance(node, (ast.Return, ast.Assign, ast.Raise)) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            ):
                snippet = ast.unparse(node)
            else:
                continue
            if len(snippet) <= 190:
                rows[node.lineno] = snippet
        rows = sorted(rows.items())
        # Keep coverage across each function, including later cleanup/return paths.
        cap = 8 if assertions else max(4, 15 // len(names))
        if len(rows) > cap:
            rows = [rows[i * (len(rows)-1) // (cap-1)] for i in range(cap)]
        result.extend({"line": n, "code": code} for n, code in rows)
    return result


def evidence(root, row):
    area, fixture = row["area"], row["fixture"]
    if area not in TARGETS or fixture != self_diagnosis.CASES.get(area):
        return None
    filename, names = TARGETS[area]
    module, _, method = fixture.split(".")
    test = "tests/" + module + ".py"
    try:
        lines = excerpts(root, filename, names)
        assertions = excerpts(root, test, [method], assertions=True)
    except (OSError, ValueError, SyntaxError, RecursionError):
        return None
    reason = row["symptom"] if row["symptom"] in EXCEPTIONS else (
        "reported problem" if row["kind"] == "reported" else "runtime failure"
    )
    return dict(area=area, fixture=fixture, reason=reason, probe=row["status"],
                source_file=filename, fixture_file=test, operations=lines, assertions=assertions)


def prompt(data):
    system = ("Diagnose an unresolved local incident using only the cited Talos source and regression "
              "assertions. Identify a concrete possible cause and a distinct new regression scenario "
              "with inputs and an observable expected result. Use one complete sentence under 130 characters for each field. Base each mechanism on the actual code operations. A passing fixture leaves the incident "
              "unverified. Never assert a cause is proven. Cite the 1-based LIST INDEX of an operation "
              "and an assertion. Return quiet if unrelated, generic, or evidence is insufficient.")
    indexed = dict(data)
    for key in ("operations", "assertions"):
        indexed[key] = {str(i): row["code"] for i, row in enumerate(data[key], 1)}
    safe = json.dumps(indexed, ensure_ascii=True).replace("<", "\\u003c")
    return (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{safe}"
            "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")


def diagnosis(raw, data):
    if raw == "quiet":
        return {}
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or set(result) != {"source", "assertion", "hypothesis", "regression"}:
            return {}
        chosen = {}
        for key, filename, rows in (("source", "source_file", "operations"), ("assertion", "fixture_file", "assertions")):
            index = result[key]
            if type(index) is not int or not 1 <= index <= len(data[rows]):
                return {}
            chosen[key] = {"file": data[filename], **data[rows][index - 1]}
        for key in ("hypothesis", "regression"):
            value = result[key]
            if not isinstance(value, str) or not 12 <= len(value) <= 180 or not value.isascii() or not value.isprintable():
                return {}
            chosen[key] = value
        narrative = chosen["hypothesis"] + " " + chosen["regression"]
        if any(chosen[field][-1] not in ".!?" or len(chosen[field]) >= 175 for field in ("hypothesis", "regression")):
            return {}
        code = chosen["source"]["code"] + " " + chosen["assertion"]["code"]
        for mechanism in (r"\b(?:race|concurren|thread|lock)", r"\b(?:network|socket|transport)", r"\b(?:database|sqlite)"):
            if re.search(mechanism, narrative, re.I) and not re.search(mechanism, code, re.I):
                return {}
        words = model.terms(narrative)
        if not all(words & (model.terms(chosen[k]["code"]) - {"assert", "equal", "assertion", "result"})
                   for k in ("source", "assertion")):
            return {}
        return chosen
    except (TypeError, ValueError):
        return {}


def tick(state, *, root=ROOT, clock=time.time):
    """Investigate at most one new evidence revision per window, even after restart."""
    state = state.resolve()
    folder = state / "self-repair" / "investigations"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve() != folder or conversation_busy(state):
        return None
    with (folder / "turn.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        schedule = folder / "due.json"
        now = clock()
        due = load(schedule).get("next", 0)
        if now < due <= now + WINDOW:
            return None
        save(schedule, {"next": now + WINDOW})
        rows = self_diagnosis.handoff(state, limit=100)
        for row in rows:
            if row["kind"] not in ("runtime", "reported") or row["status"] not in ELIGIBLE:
                continue
            data = evidence(root, row)
            if data is None:
                continue
            revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
            ident = row["id"]
            path = folder / f"{ident}-{revision}.json"
            if path.exists():
                continue
            receipt = dict(incident=ident, revision=revision, area=data["area"], fixture=data["fixture"],
                           reason=data["reason"], probe=data["probe"], status="abstained", checked=now)
            generic = data["reason"] in {"reported problem", "runtime failure"} and data["probe"] in PASSED
            if not generic and data["operations"] and data["assertions"]:
                runtime = model.installed(root, state)
                if not runtime:
                    return None
                record(state, "Diagnosis", f"Incident #{ident} · {model.MODEL} examining {data['area']} source and fixture")
                raw, inputs, outputs = model.run_cpu(runtime, state, prompt(data), timeout=70, grammar=GRAMMAR)
                receipt.update(model=model.MODEL, input_tokens=inputs, output_tokens=outputs)
                hypothesis = diagnosis(raw, data)
                # A changed source, fixture or independently observed recovery invalidates this turn.
                current = next((r for r in self_diagnosis.handoff(state, limit=100) if r["id"] == ident), None)
                if not current or current["status"] != row["status"] or evidence(root, current) != data:
                    return None
                if hypothesis:
                    receipt.update(status="unverified hypothesis", **hypothesis)
            save(path, receipt)
            detail = f"Incident #{ident} · {data['area']} · {receipt['status']} · self-repair/investigations/{path.name}"
            record(state, "Diagnosis", detail)
            return detail
    return None

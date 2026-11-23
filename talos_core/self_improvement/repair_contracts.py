"""Pinned offline repair surface and independent, existing regression methods."""

import ast
import hashlib
import io
import itertools
import math
import os
import subprocess
import sys
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path

OPS = {"==", "!=", "<", ">", "<=", ">="}


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Contract:
    canonical_sha: str
    values: tuple
    tests: str
    cases: dict
    imports: str
    dependencies: tuple = ()


CONTRACTS = {
    "talos_core/recovery.py": Contract(
        "ac0fe3109464ae26804bbeef8f92f5154fa86685c0d159f139ff099a98293b72",
        tuple("5 10 30 60 1 0 1 1 > 3600 >= 5 .3 15 2 32 0".split()),
        "tests/test_runtime_recovery.py",
        {
            "test_poll_ladder_reset_independence_and_expiry": "6bd9b389bc91339556de70026386139e98592f2efb56298e877e6a0c2e5e76c1",
            "test_reconnect_full_jitter_cap_and_stability": "649f746bbc35cc20c603d90aa44a2a848a22cbd660fe00997b69c4e5786517fe",
        },
        "from unittest.mock import patch\nfrom talos_core.recovery import PollBackoff, stable_connection, reconnect_delay\n",
    ),
    "talos_core/memory/memory_ranking.py": Contract(
        "2612a115a345b5177a3445fc7ccf7626ab7cc52e1cc625ea105f2c1573797822",
        tuple("2 1 0.70 != 1 0.0 < 1 ==".split()),
        "tests/test_tools_and_memory.py",
        {
            "test_recall_returns_distinct_evidence_before_repeated_equal_rank_hits": "e2c59bbd4f03418db58b16a550e3acdce1f6bcc09e93d5ffc152833a48d914d4"
        },
        """import tempfile, sqlite3
from contextlib import closing
from pathlib import Path
from talos_core.memory.transcript_memory import initialize_transcripts, remember, search_transcripts
def open_memory(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    initialize_transcripts(db)
    return db
""",
        (
            (
                "talos_core/memory/transcript_memory.py",
                "19dae6853ff0c4dd92317f745ceb79bd4f02da15d2839c843c522c01e465fb6d",
            ),
        ),
    ),
}


def read_regular(path, limit=65536):
    if (
        path.is_symlink()
        or path.resolve() != path.absolute()
        or not path.is_file()
        or path.stat().st_size > limit
    ):
        raise ValueError("Expected an ordinary bounded source file")
    return path.read_bytes()


def _policy_slots(path, data):
    """Only numeric tokens and comparisons may differ from the audited pure module."""
    spec = CONTRACTS[path]
    text = data.decode("utf-8")
    positions = [0]
    for line in text.splitlines(keepends=True):
        positions.append(positions[-1] + len(line))
    slots = [
        t
        for t in tokenize.generate_tokens(io.StringIO(text).readline)
        if t.type == tokenize.NUMBER or (t.type == tokenize.OP and t.string in OPS)
    ]
    if len(slots) != len(spec.values):
        raise ValueError("Source outside approved repair surface")
    pieces, locations, previous = [], [], 0
    for token, original in zip(slots, spec.values):
        if original in OPS:
            if token.string not in OPS:
                raise ValueError("Expected a comparison")
        elif (
            token.type != tokenize.NUMBER
            or not math.isfinite(float(token.string))
            or not 0 <= float(token.string) <= 3600
        ):
            raise ValueError("Numeric policy out of bounds")
        start = positions[token.start[0] - 1] + token.start[1]
        end = positions[token.end[0] - 1] + token.end[1]
        locations.append((start, end, token.string))
        pieces.extend((text[previous:start], original))
        previous = end
    pieces.append(text[previous:])
    canonical = "".join(pieces).encode()
    if digest(canonical) != spec.canonical_sha:
        raise ValueError("Source outside approved repair surface")
    return canonical, locations


def canonical_source(path, data):
    return _policy_slots(path, data)[0]


def alternatives(path, data, reference):
    """Try the smallest corrections first and leave other policy choices intact."""
    _, slots = _policy_slots(path, data)
    _, basis = _policy_slots(path, reference)
    differing = [i for i in range(len(slots)) if slots[i][2] != basis[i][2]]
    if len(differing) > 4:
        return
    for size in (1, 2):
        for combination in itertools.combinations(differing, size):
            text = data.decode()
            for i in reversed(combination):
                start, end, _ = slots[i]
                text = text[:start] + basis[i][2] + text[end:]
            yield text.encode()


def replay(root, path, data):
    """Execute only pinned independent test methods with synthetic data in a fresh interpreter."""
    spec = CONTRACTS[path]
    canonical_source(path, data)
    source = read_regular(root / spec.tests, 512_000).decode()
    methods = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name in spec.cases:
            method = ast.get_source_segment(source, node)
            if digest(method.encode()) != spec.cases[node.name]:
                raise ValueError("Regression contract changed")
            methods.append("    " + method)
    if len(methods) != len(spec.cases):
        raise ValueError("Regression contract missing")
    with tempfile.TemporaryDirectory(prefix="talos-repair-replay-") as scratch:
        work = Path(scratch)
        dest = work / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        for name, sha in spec.dependencies:
            dependency = read_regular(root / name)
            if digest(dependency) != sha:
                raise ValueError("Regression dependency changed")
            (work / name).write_bytes(dependency)
        harness = (
            "import os,sys,unittest\nsys.path.insert(0,os.getcwd())\n"
            + spec.imports
            + "\nclass Cases(unittest.TestCase):\n"
            + "\n".join(methods)
            + "\nsuite=unittest.defaultTestLoader.loadTestsFromTestCase(Cases)\n"
            + "import traceback\nclass Result(unittest.TestResult):\n    target_errors=0\n"
            "    def addError(self,test,err):\n"
            "        super().addError(test,err)\n"
            + f"        source=os.path.abspath({path!r})\n"
            "        if any(os.path.abspath(f.f_code.co_filename)==source for f,_ in traceback.walk_tb(err[2])):\n"
            "            self.target_errors+=1\n"
            "r=Result(); suite.run(r)\n"
            "valid=not r.skipped and len(r.errors)==r.target_errors\n"
            "print(('green' if r.wasSuccessful() else 'red') if valid else 'error')\n"
        )
        (work / "verify.py").write_text(harness)
        check = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "verify.py"],
            cwd=work,
            env={"PATH": os.defpath, "TMPDIR": str(work), "LANG": "C"},
            timeout=10,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if check.returncode or check.stdout.strip() not in (b"green", b"red"):
            raise RuntimeError("Trusted replay could not run")
        return check.stdout.strip().decode("ascii")

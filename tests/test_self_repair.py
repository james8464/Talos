import difflib
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import self_repair
from repair_contracts import CONTRACTS, digest, replay

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = "talos_core/recovery.py"
RANK = "memory_ranking.py"


class SelfRepairTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.store = self.state / "self-repair"
        for path, spec in CONTRACTS.items():
            for name in (path, spec.tests, *[n for n, _ in spec.dependencies]):
                dest = self.root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, dest)

    def change(self, path, before, after):
        target = self.root / path
        text = target.read_text()
        self.assertIn(before, text)
        target.write_text(text.replace(before, after, 1))
        return target.read_bytes()

    def tick(self):
        return self_repair.run_once(self.root, self.state)

    def audit(self):
        return [json.loads(p.read_text()) for p in self.store.glob("*/receipt.json")]

    def section(self, original, diff, extra):
        key = "1" * 16
        folder = self.state / "section-patches" / key
        folder.mkdir(parents=True)
        (folder / "change.diff").write_text(diff)
        (folder / "regression.py").write_text(extra)
        (folder / "receipt.json").write_text(
            json.dumps(
                dict(
                    source=RANK,
                    target_sha256=digest(original),
                    diff_sha256=digest(diff.encode()),
                )
            )
        )
        with closing(sqlite3.connect(self.state / "section-reviews.sqlite3")) as db, db:
            db.execute("CREATE TABLE section_candidates(id,status)")
            db.execute("INSERT INTO section_candidates VALUES(?,'ready')", (key,))
        return key

    def test_offline_bootstrap_repairs_two_algorithms_and_preserves_unrelated_local_tuning(
        self,
    ):
        old1 = self.change(RECOVERY, "seen > 3600", "seen >= 3600")
        self.change(RECOVERY, "min(32", "min(31")
        old2 = self.change(RANK, "i + 2", "i + 1")
        self.change(RANK, "weight=0.70", "weight=0.69")
        self.assertIn("verified", self.tick())
        self.assertIn("verified", self.tick())
        self.assertIn("min(31", (self.root / RECOVERY).read_text())
        self.assertIn("weight=0.69", (self.root / RANK).read_text())
        self.assertEqual(
            len([r for r in self.audit() if r["outcome"] == "verified"]), 2
        )
        for path, old in ((RECOVERY, old1), (RANK, old2)):
            self.assertEqual(
                replay(self.root, path, (self.root / path).read_bytes()), "green"
            )
            self.assertEqual(replay(self.root, path, old), "red")
        self.assertIsNone(self.tick())

    def test_independent_contract_rejects_model_code_and_never_runs_its_test(self):
        original = self.change(RANK, "i + 2", "i + 1")
        extra = "from pathlib import Path\nPath('arbitrary_ran').write_text('bad')\n"
        bad = original.decode().replace(
            "    return words",
            "    "
            + extra.splitlines()[0]
            + "\n    "
            + extra.splitlines()[1]
            + "\n    return words",
        )
        diff = "".join(
            difflib.unified_diff(
                original.decode().splitlines(True),
                bad.splitlines(True),
                fromfile="a/" + RANK,
                tofile="b/" + RANK,
            )
        )
        key = self.section(original, diff, extra)
        self.tick()
        self.assertIn("verified", self.tick())
        receipts = self.audit()
        self.assertTrue(
            any(
                r["origin"] == "section-" + key and r["outcome"] == "rejected"
                for r in receipts
            )
        )
        self.assertFalse(list(self.root.rglob("arbitrary_ran")))

    def test_section_candidate_passes_existing_regression_and_installs_only_target(
        self,
    ):
        original = self.change(RANK, "i + 2", "i + 1")
        good = (ROOT / RANK).read_bytes()
        diff = "".join(
            difflib.unified_diff(
                original.decode().splitlines(True),
                good.decode().splitlines(True),
                fromfile="a/" + RANK,
                tofile="b/" + RANK,
            )
        )
        key = self.section(
            original, diff, "raise AssertionError('generated test must never execute')"
        )
        self.tick()
        self.assertIn("verified", self.tick())
        self.assertEqual((self.root / RANK).read_bytes(), good)
        receipt = self.audit()[0]
        self.assertEqual(receipt["origin"], "section-" + key)
        self.assertEqual(
            (receipt["baseline"], receipt["candidate"], receipt["post_install"]),
            ("red", "green", "green"),
        )

    def test_healthy_baseline_cannot_be_cached_under_unchecked_edit(self):
        target = self.root / RECOVERY
        original = digest(target.read_bytes())

        def concurrent(root, path, data):
            result = replay(root, path, data)
            self.change(RECOVERY, "seen > 3600", "seen >= 3600")
            return result

        with patch.object(self_repair, "replay", concurrent):
            self.assertIn("concurrent edit preserved", self.tick())
        saved = json.loads((self.store / "status.json").read_text())[RECOVERY]
        self.assertEqual(saved["sha"], original)
        self.assertNotEqual(saved["sha"], digest(target.read_bytes()))
        self.tick()
        self.assertIn("verified", self.tick())

    def test_exception_from_approved_target_is_a_red_regression(self):
        original = self.change(RANK, "high != low", "high == low")
        self.assertEqual(replay(self.root, RANK, original), "red")
        self.tick()
        self.assertIn("verified", self.tick())

    def test_unavailable_section_ledger_and_bad_cached_basis_do_not_block_offline_repair(
        self,
    ):
        self.change(RECOVERY, "seen > 3600", "seen >= 3600")
        self.store.mkdir()
        (self.store / "recovery.green").write_bytes(b"broken(")
        (self.state / "section-reviews.sqlite3").write_bytes(b"not sqlite")
        self.assertIn("verified", self.tick())
        self.assertEqual(self.audit()[0]["origin"], "pinned policy")

    def test_unknown_source_and_changed_regression_are_never_executed(self):
        target = self.root / RECOVERY
        poison = (
            f"from pathlib import Path\nPath({str(self.root / 'marker')!r}).touch()\n"
        )
        target.write_text(poison + target.read_text())
        self.assertIn("deferred", self.tick())
        test = self.root / CONTRACTS[RANK].tests
        test.write_text(
            test.read_text().replace(
                "        from memory_ranking import tokens",
                "        "
                + poison.splitlines()[1]
                + "\n        from memory_ranking import tokens",
            )
        )
        self.assertIn("deferred", self.tick())
        self.assertFalse((self.root / "marker").exists())
        self.assertFalse(self.audit())

    def test_postinstall_failure_restores_original_and_retains_diff_and_hashes(self):
        original = self.change(RECOVERY, "seen > 3600", "seen >= 3600")

        def fail_post(root, path, data):
            if data != original and (root / path).read_bytes() == data:
                return "red"
            return replay(root, path, data)

        with patch.object(self_repair, "replay", fail_post):
            self.assertIn("rolled back", self.tick())
        self.assertEqual((self.root / RECOVERY).read_bytes(), original)
        receipt = self.audit()[0]
        self.assertEqual(
            (receipt["baseline"], receipt["candidate"], receipt["post_install"]),
            ("red", "green", "red"),
        )
        self.assertEqual(receipt["rollback_sha256"], digest(original))
        self.assertIn(
            b"3600", (self.store / receipt["id"] / "change.diff").read_bytes()
        )

    def test_restart_settles_interrupted_exchange_once(self):
        self.change(RECOVERY, "seen > 3600", "seen >= 3600")
        with (
            patch.object(self_repair, "settle", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.tick()
        self.assertTrue((self.store / "pending.json").exists())
        self.assertIn("verified", self.tick())
        self.assertFalse((self.store / "pending.json").exists())
        self.assertEqual(len(self.audit()), 1)

    def test_atomic_exchange_preserves_edit_that_wins_the_last_hash_check_race(self):
        original = self.change(RECOVERY, "seen > 3600", "seen >= 3600")
        exchange = self_repair.exchange
        count = 0

        def race(a, b):
            nonlocal count
            count += 1
            if count == 1:
                a.write_bytes(original + b"# concurrent edit\n")
            exchange(a, b)

        with patch.object(self_repair, "exchange", race):
            self.assertIn("concurrent edit preserved", self.tick())
        self.assertEqual(
            (self.root / RECOVERY).read_bytes(), original + b"# concurrent edit\n"
        )

#!/usr/bin/env python3
"""Tests for the XRPL Payout Reference Auditor."""
import json, os, subprocess, sys, tempfile, unittest
import payout_audit as A

HERE = os.path.dirname(os.path.abspath(__file__))
HASH_A = "9F2BE303A1C4D5E6F708192A3B4C5D6E7F8091A2B3C4D5E6F708192A3B4C5D6E"
HASH_B = "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90"
T1, T2 = "task_one", "task_two"


def p(idx, pid, tid, tx, wallet="rW"):
    return {"index": idx, "payout_id": pid, "task_id": tid, "wallet": wallet, "tx_hash": tx}


def issues(rep):
    return sorted({i["issue"] for i in rep["issues"]})


class TestHashStructure(unittest.TestCase):
    def test_valid_hash_clean(self):
        r = A.audit([p(0, "a", T1, HASH_A)], {T1}, [])
        self.assertEqual(r["status"], "clean")

    def test_lowercase_rejected(self):
        r = A.audit([p(0, "a", T1, HASH_A.lower())], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))

    def test_too_short_rejected(self):
        r = A.audit([p(0, "a", T1, "ABCD")], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))

    def test_too_long_rejected(self):
        r = A.audit([p(0, "a", T1, HASH_A + "AB")], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))

    def test_non_hex_rejected(self):
        r = A.audit([p(0, "a", T1, "G" * 64)], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))


class TestReuse(unittest.TestCase):
    def test_reuse_across_tasks_flags_both(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A)], {T1, T2}, [])
        flagged = [i for i in r["issues"] if i["issue"] == A.REUSED_ACROSS_TASKS]
        self.assertEqual(len(flagged), 2)

    def test_reuse_within_task_flags_both(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T1, HASH_A)], {T1}, [])
        flagged = [i for i in r["issues"] if i["issue"] == A.REUSED_WITHIN_TASK]
        self.assertEqual(len(flagged), 2)

    def test_within_task_reuse_is_not_across_task(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T1, HASH_A)], {T1}, [])
        self.assertNotIn(A.REUSED_ACROSS_TASKS, issues(r))

    def test_distinct_hashes_clean(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_B)], {T1, T2}, [])
        self.assertEqual(r["status"], "clean")

    def test_three_way_reuse_flags_all(self):
        recs = [p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A), p(2, "c", "task_three", HASH_A)]
        r = A.audit(recs, {T1, T2, "task_three"}, [])
        self.assertEqual(len([i for i in r["issues"] if i["issue"] == A.REUSED_ACROSS_TASKS]), 3)


class TestRoster(unittest.TestCase):
    def test_unknown_task_flagged(self):
        r = A.audit([p(0, "a", "task_ghost", HASH_A)], {T1}, [])
        self.assertIn(A.UNKNOWN_TASK_ID, issues(r))

    def test_known_task_clean(self):
        r = A.audit([p(0, "a", T1, HASH_A)], {T1}, [])
        self.assertNotIn(A.UNKNOWN_TASK_ID, issues(r))

    def test_roster_accepts_plain_strings(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(["task_x"], fh); fh.close()
        try:
            self.assertEqual(A.load_roster(fh.name), {"task_x"})
        finally:
            os.unlink(fh.name)

    def test_roster_accepts_objects(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump([{"task_id": "task_x", "note": "n"}], fh); fh.close()
        try:
            self.assertEqual(A.load_roster(fh.name), {"task_x"})
        finally:
            os.unlink(fh.name)

    def test_roster_rejects_bad_element(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump([123], fh); fh.close()
        try:
            with self.assertRaises(A.InputError):
                A.load_roster(fh.name)
        finally:
            os.unlink(fh.name)


class TestMalformed(unittest.TestCase):
    def _payouts(self, obj):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, fh); fh.close()
        try:
            return A.load_payouts(fh.name)
        finally:
            os.unlink(fh.name)

    def test_non_object_element(self):
        _, bad = self._payouts(["nope"])
        self.assertEqual(bad[0]["issue"], A.MALFORMED_RECORD)

    def test_missing_field(self):
        _, bad = self._payouts([{"payout_id": "a", "task_id": T1}])
        self.assertIn("missing field", bad[0]["detail"])

    def test_blank_field(self):
        _, bad = self._payouts([{"payout_id": "a", "task_id": T1, "wallet": " ", "tx_hash": HASH_A}])
        self.assertEqual(bad[0]["issue"], A.MALFORMED_RECORD)

    def test_index_preserved(self):
        _, bad = self._payouts([{"payout_id": "a", "task_id": T1, "wallet": "w", "tx_hash": HASH_A}, "x"])
        self.assertEqual(bad[0]["index"], 1)

    def test_malformed_does_not_abort(self):
        good, bad = self._payouts([{"payout_id": "a", "task_id": T1, "wallet": "w", "tx_hash": HASH_A}, "x"])
        self.assertEqual(len(good), 1)
        self.assertEqual(len(bad), 1)


class TestDeterminism(unittest.TestCase):
    def test_serialize_repeatable(self):
        r = A.audit([p(0, "a", T1, "bad")], {T1}, [])
        self.assertEqual(A.serialize(r), A.serialize(r))

    def test_order_independent(self):
        a = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A)], {T1, T2}, [])
        b = A.audit([p(1, "b", T2, HASH_A), p(0, "a", T1, HASH_A)], {T1, T2}, [])
        self.assertEqual(A.serialize(a), A.serialize(b))


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "payout_audit.py"), *a],
                              capture_output=True, text=True)

    def test_clean_exit_zero(self):
        pr = self._cli(os.path.join(HERE, "payouts_clean.json"), os.path.join(HERE, "roster.json"))
        self.assertEqual(pr.returncode, 0)
        self.assertEqual(json.loads(pr.stdout)["status"], "clean")

    def test_dirty_exit_one(self):
        pr = self._cli(os.path.join(HERE, "payouts_dirty.json"), os.path.join(HERE, "roster.json"))
        self.assertEqual(pr.returncode, 1)

    def test_missing_file_exit_two(self):
        pr = self._cli("/nonexistent.json", os.path.join(HERE, "roster.json"))
        self.assertEqual(pr.returncode, 2)
        self.assertIn("UNREADABLE_INPUT", pr.stderr)

    def test_repeated_runs_identical(self):
        a = os.path.join(HERE, "payouts_dirty.json"); b = os.path.join(HERE, "roster.json")
        self.assertEqual(self._cli(a, b).stdout, self._cli(a, b).stdout)

    def test_dirty_covers_all_codes(self):
        pr = self._cli(os.path.join(HERE, "payouts_dirty.json"), os.path.join(HERE, "roster.json"))
        got = set(json.loads(pr.stdout)["issue_counts"])
        for c in (A.MALFORMED_TX_HASH, A.REUSED_ACROSS_TASKS, A.REUSED_WITHIN_TASK,
                  A.UNKNOWN_TASK_ID, A.MALFORMED_RECORD):
            self.assertIn(c, got, f"{c} not exercised")


if __name__ == "__main__":
    unittest.main(verbosity=2)

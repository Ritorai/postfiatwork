#!/usr/bin/env python3
"""Tests for the XRPL Payout Reference Auditor."""
import json, os, shutil, subprocess, sys, tempfile, unittest
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



class TestTrailingNewlineIsNotAValidHash(unittest.TestCase):
    """A tx_hash of 64 hex characters plus a newline used to audit clean.

    ``TXHASH_RE`` was ``^[0-9A-F]{64}$``. In Python ``$`` matches at the end of
    the string *or* immediately before a trailing newline, so a 65-character
    value ending in ``\n`` satisfied a pattern documented as "exactly 64
    uppercase hex characters".

    The consequence was larger than a missing structural finding. ``audit``
    groups by the raw ``tx_hash`` string, so ``HASH_A`` and ``HASH_A + "\n"``
    land in different buckets. On the parent commit a duplicated hash with one
    byte appended produced *no* finding of any kind: no ``MALFORMED_TX_HASH``
    because the regex accepted it, and no ``REUSED_ACROSS_TASKS`` because the
    two records no longer collided. The report was ``"status": "clean"`` at
    exit 0.

    What this change does and does not do, stated exactly, because the two are
    easy to conflate:

    * it DOES stop the run reporting clean -- the record is now reported as
      ``MALFORMED_TX_HASH`` and the process exits 1;
    * it DOES NOT restore the ``REUSED_ACROSS_TASKS`` finding for that pair.
      ``by_hash`` still keys on the raw string and a malformed hash is
      deliberately not normalised before grouping, so the two records remain
      in separate buckets. Normalising would mean running reuse analysis over
      a value the tool has just declared malformed, and silently repairing
      operator data is exactly the behaviour the README's "lowercase is
      rejected rather than normalised" paragraph argues against.

    ``test_the_evaded_duplicate_is_reported_as_malformed_not_as_reuse`` pins
    that second bullet, so the residual behaviour is a recorded decision
    rather than an accident waiting to be discovered.
    """

    def test_a_trailing_newline_is_malformed(self):
        r = A.audit([p(0, "a", T1, HASH_A + "\n")], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))

    def test_an_evaded_cross_task_duplicate_no_longer_audits_clean(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A + "\n")],
                    {T1, T2}, [])
        self.assertNotEqual(
            r["status"], "clean",
            "a duplicated hash with one byte appended audited clean")

    def test_an_evaded_within_task_duplicate_no_longer_audits_clean(self):
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T1, HASH_A + "\n")],
                    {T1}, [])
        self.assertNotEqual(r["status"], "clean")

    def test_the_evaded_duplicate_is_reported_as_malformed_not_as_reuse(self):
        """Pins the limit of this change rather than papering over it.

        The pair is caught, but it is caught as a malformed hash. The
        ``REUSED_ACROSS_TASKS`` finding is still absent, because ``by_hash``
        keys on the raw string. If a future change decides to normalise
        before grouping, this test fails and that decision gets made on
        purpose.
        """
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A + "\n")],
                    {T1, T2}, [])
        self.assertEqual(issues(r), [A.MALFORMED_TX_HASH])
        self.assertNotIn(A.REUSED_ACROSS_TASKS, issues(r))
        self.assertEqual(r["totals"]["distinct_tx_hashes"], 2)

    def test_an_unevaded_duplicate_is_still_reported_as_reuse(self):
        """Control: the reuse path itself is untouched by this change."""
        r = A.audit([p(0, "a", T1, HASH_A), p(1, "b", T2, HASH_A)],
                    {T1, T2}, [])
        self.assertEqual(issues(r), [A.REUSED_ACROSS_TASKS])

    def test_the_exact_64_character_hash_is_still_accepted(self):
        r = A.audit([p(0, "a", T1, HASH_A)], {T1}, [])
        self.assertEqual(r["status"], "clean")

    # Control: ``^`` was anchored, so only the tail of the value was ever
    # open. This one passes on the parent commit too, and is here so the
    # change is not mistaken for having fixed both ends. Written as a
    # comment rather than a docstring so `unittest -v` prints the test's
    # name in the committed transcript instead of this prose.
    def test_a_leading_newline_was_already_rejected(self):
        r = A.audit([p(0, "a", T1, "\n" + HASH_A)], {T1}, [])
        self.assertIn(A.MALFORMED_TX_HASH, issues(r))

    # Every printable ASCII character plus the ASCII whitespace and NUL
    # controls, placed once before and once after a valid hash: 202 values,
    # all 65 characters long, so every one must be rejected.
    #
    # Being straight about what this is worth: exactly ONE of those 202
    # cases discriminated between the old pattern and the new one, and it
    # is the same trailing-LF case that
    # test_a_trailing_newline_is_malformed already covers on its own. `^`
    # was anchored, so the 101 prefix cases and 100 of the 101 suffix cases
    # were already rejected on the parent commit. This is a guard against a
    # future re-widening of the pattern, not evidence that the defect was
    # broad. The case count and the alphabet size are both asserted so the
    # matrix cannot shrink and keep passing.
    #
    # Comment, not docstring, so `unittest -v` prints the test name.
    def test_no_single_character_affix_makes_a_valid_hash(self):
        affixes = [chr(c) for c in range(0x20, 0x7F)] + list("\t\n\r\v\f\x00")
        checked = 0
        accepted = []
        for ch in affixes:
            for value in (ch + HASH_A, HASH_A + ch):
                self.assertEqual(len(value), 65)
                checked += 1
                if A.TXHASH_RE.match(value):
                    accepted.append(repr(value[:3] + "..." + value[-3:]))
        self.assertEqual(accepted, [],
                         "65-character values accepted: %s" % ", ".join(accepted))
        self.assertEqual(checked, 2 * len(affixes))
        self.assertEqual(len(affixes), 101,
                         "the affix alphabet changed size; update the README")


class TestTrailingNewlineEndToEnd(unittest.TestCase):
    """The same bypass through the CLI, on a temporary fixture."""

    def _run(self, records):
        d = tempfile.mkdtemp(prefix="xrpl_newline_")
        try:
            payouts = os.path.join(d, "payouts.json")
            roster = os.path.join(d, "roster.json")
            with open(payouts, "w", encoding="utf-8") as fh:
                json.dump(records, fh)
            with open(roster, "w", encoding="utf-8") as fh:
                json.dump([T1, T2], fh)
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "payout_audit.py"),
                 payouts, roster],
                capture_output=True, text=True)
        finally:
            shutil.rmtree(d)          # created by this method, three lines up

    def test_the_cli_does_not_report_clean_on_the_evaded_duplicate(self):
        rec = [{"payout_id": "a", "task_id": T1, "wallet": "rW",
                "tx_hash": HASH_A},
               {"payout_id": "b", "task_id": T2, "wallet": "rW",
                "tx_hash": HASH_A + "\n"}]
        pr = self._run(rec)
        self.assertEqual(pr.returncode, 1,
                         "exit 0 means the duplicate was not reported")
        self.assertNotEqual(json.loads(pr.stdout)["status"], "clean")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the Deterministic Reward Reconciliation CLI."""
import json, os, subprocess, sys, tempfile, unittest
from decimal import Decimal
import reconcile

HERE = os.path.dirname(os.path.abspath(__file__))
W1 = "rJ8St5nxoH4DvX"
W2 = "rPJ3VzY3L41jE2"
T1 = "task_b3a3e54adcac730636afd7e9ca80b798"
T2 = "task_2712d3e81d714fc6ab77d237491a58b4"
T3 = "task_a2df370465b16b8923304d8605b7c448"


def r(task_id=T1, wallet=W1, amount="3.5"):
    return {"task_id": task_id, "wallet": wallet, "amount": amount}


def load(recs, label="expected"):
    return reconcile._load_records.__wrapped__ if False else None


def mk(recs):
    """Turn plain dicts into parsed internal records via a temp file."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(recs, fh)
        path = fh.name
    try:
        return reconcile._load_records(path, reconcile.EXPECTED_FIELDS, "expected")
    finally:
        os.unlink(path)


class TestMismatchTypes(unittest.TestCase):
    def test_balanced(self):
        rep = reconcile.reconcile(mk([r()]), mk([r()]))
        self.assertEqual(rep["status"], "balanced")
        self.assertEqual(rep["findings"], [])

    def test_missing_payout(self):
        rep = reconcile.reconcile(mk([r()]), mk([]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.MISSING_PAYOUT)

    def test_duplicate_payout(self):
        rep = reconcile.reconcile(mk([r()]), mk([r(), r()]))
        f = rep["findings"][0]
        self.assertEqual(f["issue"], reconcile.DUPLICATE_PAYOUT)
        self.assertEqual(f["payout_count"], 2)
        self.assertEqual(f["payout_amount"], "7.000000")

    def test_unexpected_payout(self):
        rep = reconcile.reconcile(mk([]), mk([r()]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.UNEXPECTED_PAYOUT)

    def test_amount_mismatch_with_delta(self):
        rep = reconcile.reconcile(mk([r(amount="3.5")]), mk([r(amount="3.25")]))
        f = rep["findings"][0]
        self.assertEqual(f["issue"], reconcile.AMOUNT_MISMATCH)
        self.assertEqual(f["delta"], "-0.250000")

    def test_wallet_mismatch(self):
        rep = reconcile.reconcile(mk([r(wallet=W1)]), mk([r(wallet=W2)]))
        codes = [f["issue"] for f in rep["findings"]]
        self.assertIn(reconcile.WALLET_MISMATCH, codes)

    def test_wallet_and_amount_mismatch_both_reported(self):
        rep = reconcile.reconcile(mk([r(wallet=W1, amount="3.5")]), mk([r(wallet=W2, amount="1.0")]))
        codes = sorted(f["issue"] for f in rep["findings"])
        self.assertEqual(codes, [reconcile.AMOUNT_MISMATCH, reconcile.WALLET_MISMATCH])


class TestDecimalPrecision(unittest.TestCase):
    def test_no_float_drift(self):
        exp = mk([r(task_id=T1, amount="0.1"), r(task_id=T2, amount="0.2")])
        pay = mk([r(task_id=T1, amount="0.1"), r(task_id=T2, amount="0.2")])
        rep = reconcile.reconcile(exp, pay)
        self.assertEqual(rep["totals"]["expected_total"], "0.300000")
        self.assertEqual(rep["status"], "balanced")

    def test_sub_micro_difference_detected(self):
        rep = reconcile.reconcile(mk([r(amount="1.000000")]), mk([r(amount="1.000001")]))
        self.assertEqual(rep["findings"][0]["issue"], reconcile.AMOUNT_MISMATCH)

    def test_integer_amount_accepted(self):
        rep = reconcile.reconcile(mk([r(amount=3)]), mk([r(amount="3")]))
        self.assertEqual(rep["status"], "balanced")

    def test_float_amount_rejected(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(amount=3.5)])


class TestOrderingAndCanonical(unittest.TestCase):
    def test_findings_sorted_regardless_of_input_order(self):
        a = reconcile.reconcile(mk([r(T1), r(T2), r(T3)]), mk([]))
        b = reconcile.reconcile(mk([r(T3), r(T1), r(T2)]), mk([]))
        self.assertEqual([f["task_id"] for f in a["findings"]],
                         [f["task_id"] for f in b["findings"]])
        self.assertEqual(reconcile.canonical_json(a), reconcile.canonical_json(b))

    def test_canonical_json_is_byte_identical_across_runs(self):
        rep = reconcile.reconcile(mk([r()]), mk([r(amount="1")]))
        self.assertEqual(reconcile.canonical_json(rep), reconcile.canonical_json(rep))

    def test_canonical_json_sorted_keys_and_newline(self):
        text = reconcile.canonical_json(reconcile.reconcile(mk([]), mk([])))
        self.assertTrue(text.endswith("\n"))
        self.assertLess(text.index('"issue_counts"'), text.index('"totals"'))


class TestMalformedInput(unittest.TestCase):
    def test_missing_field(self):
        with self.assertRaises(reconcile.InputError):
            mk([{"task_id": T1, "wallet": W1}])

    def test_empty_wallet(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(wallet="  ")])

    def test_bad_amount_string(self):
        with self.assertRaises(reconcile.InputError):
            mk([r(amount="abc")])

    def test_duplicate_expected_task_rejected(self):
        with self.assertRaises(reconcile.InputError):
            reconcile.reconcile(mk([r(), r()]), mk([]))


class TestCliExitCodes(unittest.TestCase):
    def _run(self, exp_text, pay_text):
        paths = []
        for text in (exp_text, pay_text):
            fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            fh.write(text); fh.close(); paths.append(fh.name)
        try:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "reconcile.py")] + paths,
                capture_output=True, text=True)
        finally:
            for p in paths:
                os.unlink(p)

    def test_exit_zero_balanced(self):
        p = self._run(json.dumps([r()]), json.dumps([r()]))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "balanced")

    def test_exit_one_mismatched(self):
        p = self._run(json.dumps([r()]), json.dumps([]))
        self.assertEqual(p.returncode, 1)

    def test_exit_two_invalid_json(self):
        p = self._run("{not json", json.dumps([]))
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_exit_two_not_array(self):
        p = self._run(json.dumps({"a": 1}), json.dumps([]))
        self.assertEqual(p.returncode, 2)

    def test_cli_output_byte_identical_twice(self):
        e, y = json.dumps([r(T1), r(T2, amount="1.5")]), json.dumps([r(T1, amount="2"), r(T3)])
        a = self._run(e, y).stdout
        b = self._run(e, y).stdout
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit tests for reconcile_anomaly.py."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

import reconcile_anomaly as ra

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "reconcile_anomaly.py")

AT1 = "2026-01-01T00:00:00Z"
AT2 = "2026-01-01T00:05:00Z"
AT3 = "2026-01-01T00:10:00+00:00"


def task(task_id="t1", status="accepted", price="10.00"):
    return {"task_id": task_id, "status": status, "price": price}


def payout(payout_id="p1", task_id="t1", amount="10.00", at=AT1):
    return {"payout_id": payout_id, "task_id": task_id, "amount": amount, "at": at}


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, CLI] + args, capture_output=True, text=True, cwd=cwd or HERE
    )


def write_json(obj):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(obj, fh)
    fh.close()
    return fh.name


def codes_of(report):
    return sorted(f["code"] for f in report["findings"])


# ---------------------------------------------------------------------------
# _parse_finite_decimal / _parse_money
# ---------------------------------------------------------------------------
class TestParseFiniteDecimal(unittest.TestCase):
    def test_accepts_plain_string(self):
        v, err = ra._parse_finite_decimal("10.50")
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("10.50"))

    def test_accepts_int(self):
        v, err = ra._parse_finite_decimal(7)
        self.assertIsNone(err)
        self.assertEqual(v, Decimal(7))

    def test_accepts_decimal_passthrough(self):
        v, err = ra._parse_finite_decimal(Decimal("3.14"))
        self.assertEqual(v, Decimal("3.14"))

    def test_rejects_bool_true(self):
        v, err = ra._parse_finite_decimal(True)
        self.assertIsNotNone(err)

    def test_rejects_bool_false(self):
        v, err = ra._parse_finite_decimal(False)
        self.assertIsNotNone(err)

    def test_rejects_none(self):
        v, err = ra._parse_finite_decimal(None)
        self.assertIsNotNone(err)

    def test_rejects_empty_string(self):
        v, err = ra._parse_finite_decimal("")
        self.assertIsNotNone(err)

    def test_rejects_whitespace_string(self):
        v, err = ra._parse_finite_decimal("   ")
        self.assertIsNotNone(err)

    def test_rejects_junk_string(self):
        v, err = ra._parse_finite_decimal("abc")
        self.assertIsNotNone(err)

    def test_rejects_list(self):
        v, err = ra._parse_finite_decimal([1, 2])
        self.assertIsNotNone(err)

    def test_rejects_dict(self):
        v, err = ra._parse_finite_decimal({"a": 1})
        self.assertIsNotNone(err)

    def test_rejects_nonfinite_nan_sentinel(self):
        v, err = ra._parse_finite_decimal(ra._NonFinite("NaN"))
        self.assertIsNotNone(err)

    def test_rejects_nonfinite_infinity_sentinel(self):
        v, err = ra._parse_finite_decimal(ra._NonFinite("Infinity"))
        self.assertIsNotNone(err)

    def test_rejects_decimal_nan_directly(self):
        v, err = ra._parse_finite_decimal(Decimal("NaN"))
        self.assertIsNotNone(err)

    def test_rejects_decimal_infinity_directly(self):
        v, err = ra._parse_finite_decimal(Decimal("Infinity"))
        self.assertIsNotNone(err)

    def test_rejects_string_nan(self):
        v, err = ra._parse_finite_decimal("NaN")
        self.assertIsNotNone(err)

    def test_rejects_string_infinity(self):
        v, err = ra._parse_finite_decimal("Infinity")
        self.assertIsNotNone(err)

    def test_accepts_negative_string(self):
        v, err = ra._parse_finite_decimal("-5.00")
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("-5.00"))

    def test_accepts_zero(self):
        v, err = ra._parse_finite_decimal("0")
        self.assertEqual(v, Decimal("0"))

    def test_high_precision_string_exact(self):
        s = "123456789012345678.123456789"
        v, err = ra._parse_finite_decimal(s)
        self.assertIsNone(err)
        self.assertEqual(v, Decimal(s))
        self.assertEqual(str(v), s)


class TestParseMoney(unittest.TestCase):
    def test_negative_rejected_by_default(self):
        v, err = ra._parse_money("-1.00")
        self.assertIsNotNone(err)

    def test_negative_allowed_when_flagged(self):
        v, err = ra._parse_money("-1.00", allow_negative=True)
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("-1.00"))

    def test_zero_allowed(self):
        v, err = ra._parse_money("0")
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("0"))


class TestAmtStr(unittest.TestCase):
    def test_no_scientific_notation_small_delta(self):
        d = Decimal("10.0000000001") - Decimal("10")
        self.assertEqual(ra._amt_str(d), "0.0000000001")

    def test_negative_zero_normalized(self):
        d = Decimal("0") * -1
        self.assertEqual(str(d), "-0")
        self.assertEqual(ra._amt_str(d), "0")

    def test_plain_value(self):
        self.assertEqual(ra._amt_str(Decimal("12.50")), "12.50")


class TestIso8601Check(unittest.TestCase):
    def test_z_suffix_ok(self):
        self.assertTrue(ra._looks_like_iso8601_utc("2026-01-01T00:00:00Z"))

    def test_offset_ok(self):
        self.assertTrue(ra._looks_like_iso8601_utc("2026-01-01T00:00:00+00:00"))

    def test_non_utc_offset_still_parses(self):
        self.assertTrue(ra._looks_like_iso8601_utc("2026-01-01T00:00:00+05:00"))

    def test_naive_rejected(self):
        self.assertFalse(ra._looks_like_iso8601_utc("2026-01-01T00:00:00"))

    def test_garbage_rejected(self):
        self.assertFalse(ra._looks_like_iso8601_utc("not-a-date"))

    def test_empty_rejected(self):
        self.assertFalse(ra._looks_like_iso8601_utc(""))


# ---------------------------------------------------------------------------
# _parse_tasks
# ---------------------------------------------------------------------------
class TestParseTasks(unittest.TestCase):
    def test_valid_task(self):
        tasks, findings = ra._parse_tasks([task()])
        self.assertEqual(findings, [])
        self.assertIn("t1", tasks)
        self.assertEqual(tasks["t1"]["price"], Decimal("10.00"))
        self.assertEqual(tasks["t1"]["status"], "accepted")

    def test_non_dict_record(self):
        tasks, findings = ra._parse_tasks(["not-a-dict"])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)
        self.assertEqual(tasks, {})

    def test_missing_task_id(self):
        tasks, findings = ra._parse_tasks([{"status": "accepted", "price": "1"}])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_missing_status(self):
        tasks, findings = ra._parse_tasks([{"task_id": "t1", "price": "1"}])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)
        self.assertIn("status", findings[0]["detail"])

    def test_missing_price(self):
        tasks, findings = ra._parse_tasks([{"task_id": "t1", "status": "accepted"}])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_empty_task_id(self):
        tasks, findings = ra._parse_tasks([task(task_id="   ")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_non_string_task_id(self):
        tasks, findings = ra._parse_tasks([{"task_id": 5, "status": "accepted", "price": "1"}])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_unknown_status_malformed(self):
        tasks, findings = ra._parse_tasks([task(status="bogus")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)
        self.assertNotIn("t1", tasks)

    def test_all_five_statuses_accepted(self):
        for st in ra.VALID_STATUSES:
            tasks, findings = ra._parse_tasks([task(task_id=f"t_{st}", status=st)])
            self.assertEqual(findings, [], f"status {st} should not be flagged")

    def test_invalid_price_string(self):
        tasks, findings = ra._parse_tasks([task(price="not-a-number")])
        self.assertEqual(findings[0]["code"], ra.INVALID_PRICE)
        self.assertIn("t1", tasks)
        self.assertIsNone(tasks["t1"]["price"])

    def test_negative_price_invalid(self):
        tasks, findings = ra._parse_tasks([task(price="-5.00")])
        self.assertEqual(findings[0]["code"], ra.INVALID_PRICE)

    def test_zero_price_valid(self):
        tasks, findings = ra._parse_tasks([task(price="0")])
        self.assertEqual(findings, [])
        self.assertEqual(tasks["t1"]["price"], Decimal("0"))

    def test_price_as_number(self):
        tasks, findings = ra._parse_tasks([{"task_id": "t1", "status": "accepted", "price": 25}])
        self.assertEqual(findings, [])
        self.assertEqual(tasks["t1"]["price"], Decimal(25))

    def test_price_as_string_vs_number_equal(self):
        t1, _ = ra._parse_tasks([{"task_id": "a", "status": "accepted", "price": "25.00"}])
        t2, _ = ra._parse_tasks([{"task_id": "a", "status": "accepted", "price": 25}])
        self.assertEqual(t1["a"]["price"], t2["a"]["price"])

    def test_duplicate_task_id_first_wins(self):
        recs = [task(task_id="dup", price="10.00"), task(task_id="dup", price="999.00", status="rewarded")]
        tasks, findings = ra._parse_tasks(recs)
        self.assertEqual(tasks["dup"]["price"], Decimal("10.00"))
        self.assertEqual(tasks["dup"]["status"], "accepted")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)
        self.assertIn("duplicate task_id", findings[0]["detail"])
        self.assertIn("index 0", findings[0]["detail"])

    def test_price_precision_preserved(self):
        s = "123456789012345678.123456789"
        tasks, findings = ra._parse_tasks([{"task_id": "t1", "status": "accepted", "price": Decimal(s)}])
        self.assertEqual(findings, [])
        self.assertEqual(str(tasks["t1"]["price"]), s)

    def test_empty_list(self):
        tasks, findings = ra._parse_tasks([])
        self.assertEqual(tasks, {})
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# _parse_payouts
# ---------------------------------------------------------------------------
class TestParsePayouts(unittest.TestCase):
    def test_valid_payout(self):
        parsed, findings = ra._parse_payouts([payout()])
        self.assertEqual(findings, [])
        self.assertEqual(parsed[0]["amount"], Decimal("10.00"))
        self.assertTrue(parsed[0]["amount_valid"])

    def test_non_dict_record(self):
        parsed, findings = ra._parse_payouts([42])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)
        self.assertEqual(parsed, [])

    def test_missing_payout_id(self):
        rec = payout(); del rec["payout_id"]
        parsed, findings = ra._parse_payouts([rec])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_missing_task_id(self):
        rec = payout(); del rec["task_id"]
        parsed, findings = ra._parse_payouts([rec])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_missing_amount(self):
        rec = payout(); del rec["amount"]
        parsed, findings = ra._parse_payouts([rec])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_missing_at(self):
        rec = payout(); del rec["at"]
        parsed, findings = ra._parse_payouts([rec])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_empty_payout_id(self):
        parsed, findings = ra._parse_payouts([payout(payout_id="")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_empty_task_id(self):
        parsed, findings = ra._parse_payouts([payout(task_id="")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_bad_at_format(self):
        parsed, findings = ra._parse_payouts([payout(at="not-a-date")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_naive_at_rejected(self):
        parsed, findings = ra._parse_payouts([payout(at="2026-01-01T00:00:00")])
        self.assertEqual(findings[0]["code"], ra.MALFORMED_RECORD)

    def test_invalid_amount_string(self):
        parsed, findings = ra._parse_payouts([payout(amount="abc")])
        self.assertEqual(findings[0]["code"], ra.INVALID_AMOUNT)
        self.assertFalse(parsed[0]["amount_valid"])
        # still structurally present for cross-checks
        self.assertEqual(parsed[0]["payout_id"], "p1")

    def test_negative_amount_invalid(self):
        parsed, findings = ra._parse_payouts([payout(amount="-1.00")])
        self.assertEqual(findings[0]["code"], ra.INVALID_AMOUNT)

    def test_zero_amount_valid(self):
        parsed, findings = ra._parse_payouts([payout(amount="0")])
        self.assertEqual(findings, [])
        self.assertEqual(parsed[0]["amount"], Decimal("0"))

    def test_nan_amount_invalid(self):
        parsed, findings = ra._parse_payouts([payout(amount=ra._NonFinite("NaN"))])
        self.assertEqual(findings[0]["code"], ra.INVALID_AMOUNT)

    def test_amount_as_number(self):
        parsed, findings = ra._parse_payouts([{"payout_id": "p1", "task_id": "t1", "amount": 10, "at": AT1}])
        self.assertEqual(findings, [])
        self.assertEqual(parsed[0]["amount"], Decimal(10))

    def test_empty_list(self):
        parsed, findings = ra._parse_payouts([])
        self.assertEqual(parsed, [])
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# reconcile(): one test class per anomaly code
# ---------------------------------------------------------------------------
class TestClean(unittest.TestCase):
    def test_empty_both(self):
        rep = ra.reconcile([], [])
        self.assertEqual(rep["status"], "clean")
        self.assertEqual(rep["findings"], [])

    def test_tasks_no_payouts_clean(self):
        rep = ra.reconcile([task(task_id=f"t_{s}", status=s) for s in ra.VALID_STATUSES], [])
        self.assertEqual(rep["status"], "clean")

    def test_matching_amount_clean(self):
        rep = ra.reconcile([task(price="10.00")], [payout(amount="10.00")])
        self.assertEqual(rep["status"], "clean")

    def test_rewarded_task_with_matching_payout_not_flagged(self):
        rep = ra.reconcile([task(status="rewarded", price="42.00")],
                            [payout(amount="42.00")])
        self.assertEqual(rep["findings"], [])

    def test_refused_task_no_payout_not_flagged(self):
        # A refused task with NO payout at all is normal/expected, not an anomaly.
        rep = ra.reconcile([task(status="refused", price="5.00")], [])
        self.assertEqual(rep["findings"], [])

    def test_trailing_zero_decimal_equality(self):
        rep = ra.reconcile([task(price="99.990000")], [payout(amount="99.99")])
        self.assertEqual(rep["findings"], [])

    def test_zero_zero_clean(self):
        rep = ra.reconcile([task(price="0")], [payout(amount="0")])
        self.assertEqual(rep["findings"], [])


class TestPayoutForRefusedTask(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([task(status="refused", price="100.00")], [payout(amount="100.00")])
        self.assertEqual(codes_of(rep), [ra.PAYOUT_FOR_REFUSED_TASK])
        f = rep["findings"][0]
        self.assertEqual(f["task_id"], "t1")
        self.assertEqual(f["payout_id"], "p1")
        self.assertEqual(f["price"], "100.00")
        self.assertEqual(f["amount"], "100.00")

    def test_refused_and_amount_mismatch_both_reported(self):
        rep = ra.reconcile([task(status="refused", price="100.00")], [payout(amount="150.00")])
        self.assertEqual(codes_of(rep), [ra.AMOUNT_ABOVE_PRICE, ra.PAYOUT_FOR_REFUSED_TASK])

    def test_non_refused_statuses_not_flagged(self):
        for st in ("proposed", "accepted", "submitted", "rewarded"):
            rep = ra.reconcile([task(status=st, price="10.00")], [payout(amount="10.00")])
            self.assertNotIn(ra.PAYOUT_FOR_REFUSED_TASK, codes_of(rep), f"status={st}")


class TestDuplicatePayout(unittest.TestCase):
    def test_two_payouts_same_task(self):
        rep = ra.reconcile([task(price="10.00")],
                            [payout(payout_id="p1", amount="10.00"), payout(payout_id="p2", amount="10.00")])
        self.assertEqual(codes_of(rep), [ra.DUPLICATE_PAYOUT])
        f = rep["findings"][0]
        self.assertEqual(f["payout_ids"], ["p1", "p2"])
        self.assertEqual(f["count"], 2)

    def test_three_payouts_same_task(self):
        pays = [payout(payout_id=f"p{i}", amount="10.00") for i in range(3)]
        rep = ra.reconcile([task(price="10.00")], pays)
        self.assertEqual(codes_of(rep), [ra.DUPLICATE_PAYOUT])
        self.assertEqual(rep["findings"][0]["count"], 3)
        self.assertEqual(rep["findings"][0]["payout_ids"], ["p0", "p1", "p2"])

    def test_duplicate_suppresses_amount_check(self):
        # Even though neither individual payout matches price, DUPLICATE_PAYOUT
        # is reported instead of (ambiguous) AMOUNT_ABOVE/BELOW_PRICE.
        rep = ra.reconcile(
            [task(price="10.00")],
            [payout(payout_id="p1", amount="999.00"), payout(payout_id="p2", amount="1.00")],
        )
        self.assertEqual(codes_of(rep), [ra.DUPLICATE_PAYOUT])

    def test_duplicate_with_missing_task_reports_both(self):
        rep = ra.reconcile([], [payout(payout_id="p1"), payout(payout_id="p2")])
        self.assertEqual(codes_of(rep), [ra.DUPLICATE_PAYOUT, ra.PAYOUT_WITHOUT_TASK, ra.PAYOUT_WITHOUT_TASK])


class TestAmountAbovePrice(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([task(price="20.00")], [payout(amount="25.50")])
        self.assertEqual(codes_of(rep), [ra.AMOUNT_ABOVE_PRICE])
        f = rep["findings"][0]
        self.assertEqual(f["price"], "20.00")
        self.assertEqual(f["amount"], "25.50")
        self.assertEqual(f["delta"], "5.50")

    def test_tolerance_suppresses(self):
        rep = ra.reconcile([task(price="20.00")], [payout(amount="20.05")], tolerance=Decimal("0.10"))
        self.assertEqual(rep["findings"], [])

    def test_tolerance_boundary_exactly_equal_not_flagged(self):
        # delta == tolerance -> NOT flagged (strict '>' semantics).
        rep = ra.reconcile([task(price="20.00")], [payout(amount="20.10")], tolerance=Decimal("0.10"))
        self.assertEqual(rep["findings"], [])

    def test_tolerance_boundary_one_cent_over_flags(self):
        rep = ra.reconcile([task(price="20.00")], [payout(amount="20.11")], tolerance=Decimal("0.10"))
        self.assertEqual(codes_of(rep), [ra.AMOUNT_ABOVE_PRICE])
        self.assertEqual(rep["findings"][0]["delta"], "0.11")


class TestAmountBelowPrice(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([task(price="30.00")], [payout(amount="10.00")])
        self.assertEqual(codes_of(rep), [ra.AMOUNT_BELOW_PRICE])
        f = rep["findings"][0]
        self.assertEqual(f["price"], "30.00")
        self.assertEqual(f["amount"], "10.00")
        self.assertEqual(f["delta"], "-20.00")

    def test_tolerance_boundary_exactly_equal_not_flagged(self):
        rep = ra.reconcile([task(price="20.00")], [payout(amount="19.90")], tolerance=Decimal("0.10"))
        self.assertEqual(rep["findings"], [])

    def test_tolerance_boundary_one_cent_under_flags(self):
        rep = ra.reconcile([task(price="20.00")], [payout(amount="19.89")], tolerance=Decimal("0.10"))
        self.assertEqual(codes_of(rep), [ra.AMOUNT_BELOW_PRICE])
        self.assertEqual(rep["findings"][0]["delta"], "-0.11")

    def test_price_invalid_skips_amount_check(self):
        rep = ra.reconcile([task(price="bad")], [payout(amount="1.00")])
        self.assertEqual(codes_of(rep), [ra.INVALID_PRICE])


class TestPayoutWithoutTask(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([], [payout(task_id="ghost")])
        self.assertEqual(codes_of(rep), [ra.PAYOUT_WITHOUT_TASK])
        f = rep["findings"][0]
        self.assertEqual(f["task_id"], "ghost")
        self.assertEqual(f["payout_id"], "p1")

    def test_other_tasks_unaffected(self):
        rep = ra.reconcile([task(task_id="t1", price="1")],
                            [payout(payout_id="p1", task_id="t1", amount="1"),
                             payout(payout_id="p2", task_id="ghost")])
        self.assertEqual(codes_of(rep), [ra.PAYOUT_WITHOUT_TASK])


class TestDuplicatePayoutId(unittest.TestCase):
    def test_same_payout_id_same_task(self):
        rep = ra.reconcile([task(price="10.00")],
                            [payout(payout_id="dup", amount="10.00"), payout(payout_id="dup", amount="10.00")])
        codes = codes_of(rep)
        self.assertIn(ra.DUPLICATE_PAYOUT_ID, codes)
        self.assertIn(ra.DUPLICATE_PAYOUT, codes)

    def test_same_payout_id_different_tasks(self):
        rep = ra.reconcile(
            [task(task_id="a", price="5.00"), task(task_id="b", price="5.00")],
            [payout(payout_id="dup", task_id="a", amount="5.00"), payout(payout_id="dup", task_id="b", amount="5.00")],
        )
        self.assertEqual(codes_of(rep), [ra.DUPLICATE_PAYOUT_ID])
        f = [x for x in rep["findings"] if x["code"] == ra.DUPLICATE_PAYOUT_ID][0]
        self.assertEqual(f["task_ids"], ["a", "b"])
        self.assertEqual(f["count"], 2)

    def test_three_occurrences(self):
        rep = ra.reconcile(
            [task(task_id="a", price="5.00")],
            [payout(payout_id="dup", task_id="a", amount="5.00") for _ in range(3)],
        )
        f = [x for x in rep["findings"] if x["code"] == ra.DUPLICATE_PAYOUT_ID][0]
        self.assertEqual(f["count"], 3)


class TestInvalidAmount(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([task(price="10.00")], [payout(amount="not-a-number")])
        self.assertEqual(codes_of(rep), [ra.INVALID_AMOUNT])

    def test_invalid_amount_skips_price_comparison(self):
        rep = ra.reconcile([task(price="10.00")], [payout(amount="garbage")])
        self.assertNotIn(ra.AMOUNT_ABOVE_PRICE, codes_of(rep))
        self.assertNotIn(ra.AMOUNT_BELOW_PRICE, codes_of(rep))

    def test_invalid_amount_still_flags_refused(self):
        rep = ra.reconcile([task(status="refused", price="10.00")], [payout(amount="garbage")])
        codes = codes_of(rep)
        self.assertIn(ra.INVALID_AMOUNT, codes)
        self.assertIn(ra.PAYOUT_FOR_REFUSED_TASK, codes)
        f = [x for x in rep["findings"] if x["code"] == ra.PAYOUT_FOR_REFUSED_TASK][0]
        self.assertIsNone(f["amount"])


class TestInvalidPrice(unittest.TestCase):
    def test_basic(self):
        rep = ra.reconcile([task(price="garbage")], [])
        self.assertEqual(codes_of(rep), [ra.INVALID_PRICE])

    def test_negative_price(self):
        rep = ra.reconcile([task(price="-1.00")], [])
        self.assertEqual(codes_of(rep), [ra.INVALID_PRICE])

    def test_nan_price(self):
        rep = ra.reconcile([{"task_id": "t1", "status": "accepted", "price": ra._NonFinite("NaN")}], [])
        self.assertEqual(codes_of(rep), [ra.INVALID_PRICE])


class TestMalformedRecord(unittest.TestCase):
    def test_task_missing_field(self):
        rep = ra.reconcile([{"task_id": "t1", "price": "1"}], [])
        self.assertEqual(codes_of(rep), [ra.MALFORMED_RECORD])

    def test_payout_missing_field(self):
        rep = ra.reconcile([], [{"payout_id": "p1", "task_id": "t1", "amount": "1"}])
        self.assertEqual(codes_of(rep), [ra.MALFORMED_RECORD])

    def test_duplicate_task_id(self):
        rep = ra.reconcile([task(task_id="d", price="1"), task(task_id="d", price="2")], [])
        self.assertEqual(codes_of(rep), [ra.MALFORMED_RECORD])

    def test_unknown_status(self):
        rep = ra.reconcile([task(status="weird")], [])
        self.assertEqual(codes_of(rep), [ra.MALFORMED_RECORD])


# ---------------------------------------------------------------------------
# Combination / determinism / canonical JSON
# ---------------------------------------------------------------------------
class TestOrderingAndDeterminism(unittest.TestCase):
    def test_findings_order_independent_of_task_input_order(self):
        # Reversing the TASKS array (with no duplicate task_ids) must not
        # change the report at all: tasks are looked up by key, not by
        # position. Payout order is deliberately left unchanged here because
        # a payout's array position is itself meaningful data (echoed in
        # each finding's "index" field), so reordering payouts legitimately
        # changes "index" values -- that is covered separately below.
        tasks = [task(task_id="a", status="refused", price="1"),
                 task(task_id="b", status="refused", price="1"),
                 task(task_id="c", status="refused", price="1")]
        pays = [payout(payout_id="pa", task_id="a", amount="1"),
                payout(payout_id="pb", task_id="b", amount="1"),
                payout(payout_id="pc", task_id="c", amount="1")]
        rep1 = ra.reconcile(tasks, pays)
        rep2 = ra.reconcile(list(reversed(tasks)), pays)
        self.assertEqual(ra.canonical_json(rep1), ra.canonical_json(rep2))

    def test_finding_set_independent_of_payout_input_order(self):
        # Reversing PAYOUT order changes each finding's "index" (which
        # legitimately encodes source-array position) but must not change
        # which codes/task_ids/payout_ids are reported, nor their relative
        # sort order by (code, task_id, payout_id).
        tasks = [task(task_id="a", status="refused", price="1"),
                 task(task_id="b", status="refused", price="1"),
                 task(task_id="c", status="refused", price="1")]
        pays = [payout(payout_id="pa", task_id="a", amount="1"),
                payout(payout_id="pb", task_id="b", amount="1"),
                payout(payout_id="pc", task_id="c", amount="1")]
        rep1 = ra.reconcile(tasks, pays)
        rep2 = ra.reconcile(tasks, list(reversed(pays)))
        strip = lambda rep: [(f["code"], f["task_id"], f["payout_id"]) for f in rep["findings"]]
        self.assertEqual(strip(rep1), strip(rep2))

    def test_repeated_calls_byte_identical(self):
        tasks = [task(status="refused")]
        pays = [payout()]
        a = ra.canonical_json(ra.reconcile(tasks, pays))
        b = ra.canonical_json(ra.reconcile(tasks, pays))
        self.assertEqual(a, b)

    def test_canonical_json_sorted_keys_and_trailing_newline(self):
        text = ra.canonical_json(ra.reconcile([], []))
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        self.assertLess(text.index('"findings"'), text.index('"report_version"'))

    def test_canonical_json_no_spaces(self):
        text = ra.canonical_json(ra.reconcile([task()], []))
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_multiple_anomaly_codes_all_present_and_sorted(self):
        tasks = [
            task(task_id="refused1", status="refused", price="100.00"),
            task(task_id="above1", status="accepted", price="20.00"),
            task(task_id="below1", status="accepted", price="30.00"),
        ]
        pays = [
            payout(payout_id="p1", task_id="refused1", amount="100.00"),
            payout(payout_id="p2", task_id="above1", amount="25.00"),
            payout(payout_id="p3", task_id="below1", amount="10.00"),
            payout(payout_id="p4", task_id="ghost", amount="5.00"),
        ]
        rep = ra.reconcile(tasks, pays)
        codes = set(codes_of(rep))
        self.assertEqual(codes, {ra.PAYOUT_FOR_REFUSED_TASK, ra.AMOUNT_ABOVE_PRICE,
                                  ra.AMOUNT_BELOW_PRICE, ra.PAYOUT_WITHOUT_TASK})
        # findings list is sorted by code alphabetically-first-key
        as_list = [f["code"] for f in rep["findings"]]
        self.assertEqual(as_list, sorted(as_list))


class TestPrecision(unittest.TestCase):
    def test_high_precision_exact_match_via_real_json_file(self):
        # End-to-end through the actual json.loads(parse_float=Decimal) path
        # (not just constructing Decimal directly in Python), since that is
        # the exact mechanism the hard contract requires and the exact
        # mechanism an earlier tool in this repo got wrong.
        s = "123456789012345678.123456789"
        tpath = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        ppath = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        with open(tpath, "w") as f:
            f.write('[{"task_id":"t1","status":"rewarded","price":%s}]' % s)
        with open(ppath, "w") as f:
            f.write('[{"payout_id":"p1","task_id":"t1","amount":%s,"at":"%s"}]' % (s, AT1))
        try:
            raw_tasks = ra._load_array(tpath, "tasks")
            raw_payouts = ra._load_array(ppath, "payouts")
            self.assertEqual(str(raw_tasks[0]["price"]), s)
            self.assertEqual(str(raw_payouts[0]["amount"]), s)
            rep = ra.reconcile(raw_tasks, raw_payouts)
            self.assertEqual(rep["findings"], [])
            self.assertEqual(rep["status"], "clean")
        finally:
            os.unlink(tpath); os.unlink(ppath)

    def test_high_precision_exact_match_clean(self):
        s = "123456789012345678.123456789"
        tasks = [{"task_id": "t1", "status": "rewarded", "price": Decimal(s)}]
        pays = [{"payout_id": "p1", "task_id": "t1", "amount": Decimal(s), "at": AT1}]
        rep = ra.reconcile(tasks, pays)
        self.assertEqual(rep["findings"], [])
        self.assertEqual(rep["status"], "clean")

    def test_float_would_have_corrupted_this_value(self):
        s = "123456789012345678.123456789"
        naive = Decimal(str(float(s)))
        self.assertNotEqual(naive, Decimal(s), "sanity check: naive float round-trip must corrupt this value")

    def test_sub_cent_delta_detected(self):
        rep = ra.reconcile([task(price="1.000000000000000001")],
                            [payout(amount="1.000000000000000002")])
        self.assertEqual(codes_of(rep), [ra.AMOUNT_ABOVE_PRICE])
        self.assertEqual(rep["findings"][0]["delta"], "0.000000000000000001")


# ---------------------------------------------------------------------------
# canonical_json / tolerance parsing
# ---------------------------------------------------------------------------
class TestToleranceParsing(unittest.TestCase):
    def test_default_zero(self):
        self.assertEqual(ra._parse_tolerance("0"), Decimal("0"))

    def test_parses_decimal_string(self):
        self.assertEqual(ra._parse_tolerance("1.50"), Decimal("1.50"))

    def test_rejects_negative(self):
        with self.assertRaises(ra.InputError):
            ra._parse_tolerance("-1")

    def test_rejects_junk(self):
        with self.assertRaises(ra.InputError):
            ra._parse_tolerance("abc")

    def test_rejects_nan_string(self):
        with self.assertRaises(ra.InputError):
            ra._parse_tolerance("NaN")


# ---------------------------------------------------------------------------
# _load_json / _load_array
# ---------------------------------------------------------------------------
class TestLoadJson(unittest.TestCase):
    def test_file_not_found(self):
        with self.assertRaises(ra.InputError):
            ra._load_json("/definitely/does/not/exist.json")

    def test_invalid_json_syntax(self):
        path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        with open(path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(ra.InputError):
            ra._load_json(path)
        os.unlink(path)

    def test_parse_float_is_decimal(self):
        path = write_json([1.5])
        data = ra._load_json(path)
        self.assertIsInstance(data[0], Decimal)
        os.unlink(path)

    def test_bare_nan_becomes_nonfinite(self):
        path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        with open(path, "w") as fh:
            fh.write("[NaN]")
        data = ra._load_json(path)
        self.assertIsInstance(data[0], ra._NonFinite)
        os.unlink(path)

    def test_load_array_rejects_object(self):
        path = write_json({"a": 1})
        with self.assertRaises(ra.InputError):
            ra._load_array(path, "tasks")
        os.unlink(path)

    def test_load_array_accepts_list(self):
        path = write_json([1, 2, 3])
        self.assertEqual(ra._load_array(path, "tasks"), [1, 2, 3])
        os.unlink(path)

    def test_directory_raises(self):
        with self.assertRaises(ra.InputError):
            ra._load_json(HERE)


class TestSafeRepr(unittest.TestCase):
    def test_string_passthrough(self):
        self.assertEqual(ra._safe_repr("hi"), "hi")

    def test_int_passthrough(self):
        self.assertEqual(ra._safe_repr(5), 5)

    def test_none_passthrough(self):
        self.assertIsNone(ra._safe_repr(None))

    def test_decimal_to_str(self):
        self.assertEqual(ra._safe_repr(Decimal("1.5")), "1.5")

    def test_nonfinite_to_token(self):
        self.assertEqual(ra._safe_repr(ra._NonFinite("NaN")), "NaN")

    def test_list_to_repr_string(self):
        self.assertEqual(ra._safe_repr([1, 2]), repr([1, 2]))


# ---------------------------------------------------------------------------
# CLI subprocess tests
# ---------------------------------------------------------------------------
class TestCli(unittest.TestCase):
    def _run(self, tasks_obj, payouts_obj, extra_args=None):
        tpath = write_json(tasks_obj)
        ppath = write_json(payouts_obj)
        try:
            args = [tpath, ppath] + (extra_args or [])
            return run_cli(args)
        finally:
            os.unlink(tpath)
            os.unlink(ppath)

    def test_exit_zero_clean(self):
        p = self._run([task(price="1.00")], [payout(amount="1.00")])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clean")

    def test_exit_one_anomalies(self):
        p = self._run([task(status="refused", price="1.00")], [payout(amount="1.00")])
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["status"], "anomalies")

    def test_exit_two_bad_json(self):
        ppath = write_json([])
        tpath = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        with open(tpath, "w") as fh:
            fh.write("{not json")
        try:
            p = run_cli([tpath, ppath])
            self.assertEqual(p.returncode, 2)
            self.assertIn("INVALID_INPUT", p.stderr)
        finally:
            os.unlink(tpath); os.unlink(ppath)

    def test_exit_two_nonexistent_file(self):
        ppath = write_json([])
        try:
            p = run_cli(["/nonexistent.json", ppath])
            self.assertEqual(p.returncode, 2)
            self.assertIn("INVALID_INPUT", p.stderr)
        finally:
            os.unlink(ppath)

    def test_exit_two_missing_arg(self):
        tpath = write_json([])
        try:
            p = run_cli([tpath])
            self.assertEqual(p.returncode, 2)
        finally:
            os.unlink(tpath)

    def test_exit_two_top_level_not_array(self):
        tpath = write_json({"a": 1})
        ppath = write_json([])
        try:
            p = run_cli([tpath, ppath])
            self.assertEqual(p.returncode, 2)
        finally:
            os.unlink(tpath); os.unlink(ppath)

    def test_output_flag_short(self):
        outpath = tempfile.NamedTemporaryFile(delete=False).name
        os.unlink(outpath)
        p = self._run([task(price="1.00")], [payout(amount="1.00")], extra_args=["-o", outpath])
        self.assertEqual(p.returncode, 0)
        self.assertTrue(os.path.exists(outpath))
        with open(outpath) as fh:
            self.assertEqual(json.load(fh)["status"], "clean")
        os.unlink(outpath)

    def test_output_flag_long(self):
        outpath = tempfile.NamedTemporaryFile(delete=False).name
        os.unlink(outpath)
        p = self._run([task(price="1.00")], [payout(amount="1.00")], extra_args=["--output", outpath])
        self.assertEqual(p.returncode, 0)
        self.assertTrue(os.path.exists(outpath))
        os.unlink(outpath)

    def test_repeat_runs_byte_identical(self):
        tasks = [task(status="refused", price="1.00"), task(task_id="t2", price="5.00")]
        pays = [payout(amount="1.00"), payout(payout_id="p2", task_id="t2", amount="9.00")]
        a = self._run(tasks, pays).stdout
        b = self._run(tasks, pays).stdout
        self.assertEqual(a, b)

    def test_tolerance_changes_findings(self):
        tasks = [task(price="20.00")]
        pays = [payout(amount="20.05")]
        p_default = self._run(tasks, pays)
        p_tol = self._run(tasks, pays, extra_args=["--tolerance", "1"])
        self.assertEqual(p_default.returncode, 1)
        self.assertEqual(p_tol.returncode, 0)

    def test_tolerance_rejects_negative(self):
        p = self._run([task()], [payout()], extra_args=["--tolerance", "-1"])
        self.assertEqual(p.returncode, 2)

    def test_stdout_used_when_no_output_flag(self):
        p = self._run([], [])
        self.assertTrue(p.stdout.strip())

    def test_stderr_empty_on_success(self):
        p = self._run([], [])
        self.assertEqual(p.stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

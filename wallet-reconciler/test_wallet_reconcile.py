#!/usr/bin/env python3
"""Tests for the Deterministic Wallet Ledger Reconciliation CLI."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

import wallet_reconcile as wr

HERE = os.path.dirname(os.path.abspath(__file__))


def ev(event_id="e1", type_="reward", amount="10", at="2026-01-01T00:00:00Z"):
    return {"event_id": event_id, "type": type_, "amount": amount, "at": at}


def run_ledger(doc):
    """Round-trip a Python dict through real JSON text and _load_ledger, so
    tests exercise the same parse_float=Decimal / parse_constant path the
    CLI uses, then reconcile it. Returns the report dict."""
    text = json.dumps(doc, default=str)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        opening, closing, events = wr._load_ledger(path)
        return wr.reconcile(opening, closing, events)
    finally:
        os.unlink(path)


def run_ledger_raw_text(text):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        opening, closing, events = wr._load_ledger(path)
        return wr.reconcile(opening, closing, events)
    finally:
        os.unlink(path)


def base_doc(opening="0", closing="0", events=None):
    return {"opening_balance": opening, "closing_balance": closing, "events": events or []}


def codes_of(report):
    return [f["code"] for f in report["findings"]]


class TestAmountCoercion(unittest.TestCase):
    def test_plain_integer_string(self):
        v, err = wr._coerce_amount("10")
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("10"))

    def test_decimal_string(self):
        v, err = wr._coerce_amount("10.5")
        self.assertEqual(v, Decimal("10.5"))

    def test_json_int_number(self):
        v, err = wr._coerce_amount(10)
        self.assertEqual(v, Decimal(10))

    def test_json_decimal_number(self):
        v, err = wr._coerce_amount(Decimal("10.500000"))
        self.assertEqual(v, Decimal("10.500000"))

    def test_negative_string_rejected(self):
        v, err = wr._coerce_amount("-1")
        self.assertIsNone(v)
        self.assertIn("non-negative", err)

    def test_negative_int_rejected(self):
        v, err = wr._coerce_amount(-1)
        self.assertIsNone(v)

    def test_zero_accepted(self):
        v, err = wr._coerce_amount("0")
        self.assertIsNone(err)
        self.assertEqual(v, Decimal("0"))

    def test_zero_int_accepted(self):
        v, err = wr._coerce_amount(0)
        self.assertIsNone(err)

    def test_garbage_string_rejected(self):
        v, err = wr._coerce_amount("not-a-number")
        self.assertIsNone(v)
        self.assertIn("not a valid decimal", err)

    def test_empty_string_rejected(self):
        v, err = wr._coerce_amount("")
        self.assertIsNone(v)

    def test_none_rejected(self):
        v, err = wr._coerce_amount(None)
        self.assertIsNone(v)
        self.assertIn("null", err)

    def test_bool_true_rejected(self):
        v, err = wr._coerce_amount(True)
        self.assertIsNone(v)
        self.assertIn("boolean", err)

    def test_bool_false_rejected(self):
        v, err = wr._coerce_amount(False)
        self.assertIsNone(v)
        self.assertIn("boolean", err)

    def test_list_rejected(self):
        v, err = wr._coerce_amount([1, 2])
        self.assertIsNone(v)
        self.assertIn("unsupported", err)

    def test_dict_rejected(self):
        v, err = wr._coerce_amount({"a": 1})
        self.assertIsNone(v)

    def test_nan_sentinel_rejected(self):
        v, err = wr._coerce_amount(wr._NonFinite("NaN"))
        self.assertIsNone(v)
        self.assertIn("NaN", err)

    def test_infinity_sentinel_rejected(self):
        v, err = wr._coerce_amount(wr._NonFinite("Infinity"))
        self.assertIsNone(v)

    def test_negative_infinity_sentinel_rejected(self):
        v, err = wr._coerce_amount(wr._NonFinite("-Infinity"))
        self.assertIsNone(v)

    def test_nan_string_rejected(self):
        # Decimal("NaN") does NOT raise InvalidOperation -- it must be
        # caught by the explicit is_nan() check, not the try/except.
        v, err = wr._coerce_amount("NaN")
        self.assertIsNone(v)
        self.assertIn("finite", err)

    def test_infinity_string_rejected(self):
        v, err = wr._coerce_amount("Infinity")
        self.assertIsNone(v)
        self.assertIn("finite", err)

    def test_high_precision_string_exact(self):
        s = "123456789012345678.123456789"
        v, err = wr._coerce_amount(s)
        self.assertIsNone(err)
        self.assertEqual(str(v), s)

    def test_high_precision_via_json_number_exact(self):
        # Proves parse_float=Decimal builds from source text, not float64.
        text = '{"x": 123456789012345678.123456789}'
        parsed = json.loads(text, parse_float=Decimal, parse_constant=wr._NonFinite)
        self.assertEqual(str(parsed["x"]), "123456789012345678.123456789")

    def test_float_would_have_corrupted_this_value(self):
        s = "123456789012345678.123456789"
        # Demonstrate the bug this tool must NOT reproduce: round-tripping
        # through float silently drops precision.
        self.assertNotEqual(str(Decimal(str(float(s)))), s)
        # ...whereas our coercion path preserves it exactly.
        v, _ = wr._coerce_amount(s)
        self.assertEqual(str(v), s)


class TestTimestampCoercion(unittest.TestCase):
    def test_z_suffix(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00Z")
        self.assertIsNone(err)

    def test_explicit_utc_offset(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00+00:00")
        self.assertIsNone(err)

    def test_negative_zero_utc_offset(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00-00:00")
        self.assertIsNone(err)

    def test_non_utc_offset_rejected(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00+05:00")
        self.assertIsNone(dt)
        self.assertIn("UTC", err)

    def test_naive_timestamp_rejected(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00")
        self.assertIsNone(dt)
        self.assertIn("offset", err)

    def test_garbage_string_rejected(self):
        dt, err = wr._coerce_timestamp("not-a-timestamp")
        self.assertIsNone(dt)

    def test_empty_string_rejected(self):
        dt, err = wr._coerce_timestamp("")
        self.assertIsNone(dt)

    def test_non_string_rejected(self):
        dt, err = wr._coerce_timestamp(20260101)
        self.assertIsNone(dt)
        self.assertIn("string", err)

    def test_lowercase_z_accepted(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00z")
        self.assertIsNone(err)

    def test_microseconds_accepted(self):
        dt, err = wr._coerce_timestamp("2026-01-01T00:00:00.123456Z")
        self.assertIsNone(err)


class TestDuplicateEventId(unittest.TestCase):
    def test_no_duplicates_clean(self):
        rep = run_ledger(base_doc(events=[ev("e1"), ev("e2")]))
        self.assertNotIn(wr.DUPLICATE_EVENT_ID, codes_of(rep))

    def test_duplicate_flagged_on_second_occurrence(self):
        rep = run_ledger(base_doc(events=[ev("e1"), ev("e1")]))
        dups = [f for f in rep["findings"] if f["code"] == wr.DUPLICATE_EVENT_ID]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["index"], 1)
        self.assertEqual(dups[0]["first_index"], 0)

    def test_triple_duplicate_flags_each_repeat(self):
        rep = run_ledger(base_doc(events=[ev("e1"), ev("e1"), ev("e1")]))
        dups = [f for f in rep["findings"] if f["code"] == wr.DUPLICATE_EVENT_ID]
        self.assertEqual(len(dups), 2)
        self.assertEqual([d["index"] for d in dups], [1, 2])
        self.assertTrue(all(d["first_index"] == 0 for d in dups))

    def test_duplicate_event_still_applied_to_balance(self):
        rep = run_ledger(base_doc(opening="0", closing="20",
                                   events=[ev("e1", amount="10"), ev("e1", amount="10")]))
        self.assertEqual(rep["computed_closing_balance"], "20")


class TestOutOfOrderTimestamp(unittest.TestCase):
    def test_ascending_order_clean(self):
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-01T00:00:00Z"),
            ev("e2", at="2026-01-02T00:00:00Z"),
        ]))
        self.assertNotIn(wr.OUT_OF_ORDER_TIMESTAMP, codes_of(rep))

    def test_earlier_timestamp_flagged(self):
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-02T00:00:00Z"),
            ev("e2", at="2026-01-01T00:00:00Z"),
        ]))
        f = [x for x in rep["findings"] if x["code"] == wr.OUT_OF_ORDER_TIMESTAMP]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["event_id"], "e2")
        self.assertEqual(f[0]["previous_event_id"], "e1")

    def test_exact_tie_is_not_out_of_order(self):
        # Design decision (documented in README): two events at the exact
        # same instant are NOT flagged -- only strictly earlier timestamps
        # are. Simultaneous events (e.g. same-block settlement) are common
        # and not inherently an ordering error.
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-01T00:00:00Z"),
            ev("e2", at="2026-01-01T00:00:00Z"),
        ]))
        self.assertNotIn(wr.OUT_OF_ORDER_TIMESTAMP, codes_of(rep))

    def test_first_event_has_nothing_to_compare_against(self):
        rep = run_ledger(base_doc(events=[ev("e1", at="2026-01-01T00:00:00Z")]))
        self.assertNotIn(wr.OUT_OF_ORDER_TIMESTAMP, codes_of(rep))

    def test_invalid_timestamp_does_not_disturb_cursor(self):
        # e2 has a broken "at" -- it must not become the new "previous"
        # timestamp, and must not itself be compared for ordering.
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-05T00:00:00Z"),
            ev("e2", at="garbage"),
            ev("e3", at="2026-01-06T00:00:00Z"),
        ]))
        self.assertNotIn(wr.OUT_OF_ORDER_TIMESTAMP, codes_of(rep))

    def test_invalid_timestamp_event_itself_not_flagged_out_of_order(self):
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-05T00:00:00Z"),
            ev("e2", at="garbage"),
        ]))
        oo = [f for f in rep["findings"] if f["code"] == wr.OUT_OF_ORDER_TIMESTAMP]
        self.assertEqual(oo, [])

    def test_out_of_order_compares_only_to_immediately_preceding_event(self):
        # Spec wording is literal: "earlier than the previous event's".
        # e3 (01-02) is earlier than e1 (01-05) but NOT earlier than its
        # immediate predecessor e2 (01-01), so only e2 is flagged -- this
        # is a deliberate adjacent-pair comparison, not a running watermark
        # against the highest timestamp seen so far. See README limitations.
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-05T00:00:00Z"),
            ev("e2", at="2026-01-01T00:00:00Z"),
            ev("e3", at="2026-01-02T00:00:00Z"),
        ]))
        oo = [f for f in rep["findings"] if f["code"] == wr.OUT_OF_ORDER_TIMESTAMP]
        self.assertEqual(len(oo), 1)
        self.assertEqual(oo[0]["event_id"], "e2")

    def test_multiple_independent_out_of_order_events_each_flagged(self):
        rep = run_ledger(base_doc(events=[
            ev("e1", at="2026-01-05T00:00:00Z"),
            ev("e2", at="2026-01-01T00:00:00Z"),
            ev("e3", at="2026-01-10T00:00:00Z"),
            ev("e4", at="2026-01-03T00:00:00Z"),
        ]))
        oo = [f for f in rep["findings"] if f["code"] == wr.OUT_OF_ORDER_TIMESTAMP]
        self.assertEqual(len(oo), 2)
        self.assertEqual([f["event_id"] for f in oo], ["e2", "e4"])


class TestNegativeRunningBalance(unittest.TestCase):
    def test_going_negative_flagged(self):
        rep = run_ledger(base_doc(opening="10", closing="-5",
                                   events=[ev("e1", type_="chat_spend", amount="15")]))
        neg = [f for f in rep["findings"] if f["code"] == wr.NEGATIVE_RUNNING_BALANCE]
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0]["event_id"], "e1")
        self.assertEqual(neg[0]["balance"], "-5")

    def test_exact_zero_not_flagged(self):
        rep = run_ledger(base_doc(opening="10", closing="0",
                                   events=[ev("e1", type_="chat_spend", amount="10")]))
        self.assertNotIn(wr.NEGATIVE_RUNNING_BALANCE, codes_of(rep))
        self.assertEqual(rep["trace"][0]["running_balance"], "0")

    def test_zero_amount_chat_spend_from_zero_balance_prints_positive_zero(self):
        # Regression test for a real bug caught during development: Decimal
        # multiplication of a zero magnitude by -1 produces Decimal('-0'),
        # and str(Decimal('-0')) == '-0'. Without normalization this would
        # print a misleading negative-looking balance for a no-op event.
        rep = run_ledger(base_doc(opening="0", closing="0",
                                   events=[ev("e1", type_="chat_spend", amount="0")]))
        self.assertEqual(rep["trace"][0]["running_balance"], "0")
        self.assertEqual(rep["trace"][0]["signed_amount"], "0")
        self.assertNotIn("-0", rep["trace"][0]["running_balance"])
        self.assertNotIn(wr.NEGATIVE_RUNNING_BALANCE, codes_of(rep))

    def test_recovers_after_going_negative_only_flags_negative_events(self):
        rep = run_ledger(base_doc(opening="0", closing="5", events=[
            ev("e1", type_="chat_spend", amount="10"),
            ev("e2", type_="reward", amount="15"),
        ]))
        neg = [f for f in rep["findings"] if f["code"] == wr.NEGATIVE_RUNNING_BALANCE]
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0]["event_id"], "e1")

    def test_negative_opening_balance_flagged_as_virtual_checkpoint(self):
        rep = run_ledger(base_doc(opening="-5", closing="-5", events=[]))
        neg = [f for f in rep["findings"] if f["code"] == wr.NEGATIVE_RUNNING_BALANCE]
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0]["index"], -1)
        self.assertEqual(neg[0]["event_id"], None)
        self.assertEqual(neg[0]["context"], "opening_balance")
        self.assertEqual(neg[0]["balance"], "-5")

    def test_unapplied_event_does_not_change_balance_or_trigger_negative(self):
        rep = run_ledger(base_doc(opening="0", closing="0",
                                   events=[ev("e1", type_="bogus", amount="10")]))
        self.assertNotIn(wr.NEGATIVE_RUNNING_BALANCE, codes_of(rep))
        self.assertEqual(rep["trace"][0]["running_balance"], "0")


class TestClosingBalanceMismatch(unittest.TestCase):
    def test_exact_match_no_finding(self):
        rep = run_ledger(base_doc(opening="0", closing="10", events=[ev("e1", amount="10")]))
        self.assertNotIn(wr.CLOSING_BALANCE_MISMATCH, codes_of(rep))
        self.assertEqual(rep["status"], "reconciled")

    def test_tiny_delta_flagged(self):
        rep = run_ledger(base_doc(opening="0", closing="10.0000000001",
                                   events=[ev("e1", amount="10")]))
        mm = [f for f in rep["findings"] if f["code"] == wr.CLOSING_BALANCE_MISMATCH]
        self.assertEqual(len(mm), 1)
        # Regression check: str(Decimal) alone would render this delta in
        # scientific notation ("-1E-10"), which is not human-auditable.
        # _amt_str must force plain fixed-point notation.
        self.assertEqual(mm[0]["delta"], "-0.0000000001")
        self.assertNotIn("E", mm[0]["delta"])
        self.assertEqual(mm[0]["computed_closing_balance"], "10")
        self.assertEqual(mm[0]["stated_closing_balance"], "10.0000000001")

    def test_positive_delta_when_computed_exceeds_stated(self):
        rep = run_ledger(base_doc(opening="0", closing="5", events=[ev("e1", amount="10")]))
        mm = [f for f in rep["findings"] if f["code"] == wr.CLOSING_BALANCE_MISMATCH][0]
        self.assertEqual(mm["delta"], "5")

    def test_empty_events_opening_ne_closing_flagged(self):
        rep = run_ledger(base_doc(opening="10", closing="20", events=[]))
        self.assertIn(wr.CLOSING_BALANCE_MISMATCH, codes_of(rep))
        self.assertEqual(rep["computed_closing_balance"], "10")

    def test_empty_events_opening_eq_closing_clean(self):
        rep = run_ledger(base_doc(opening="10", closing="10", events=[]))
        self.assertEqual(rep["status"], "reconciled")
        self.assertEqual(rep["event_count"], 0)

    def test_mismatch_sorts_after_all_event_findings(self):
        rep = run_ledger(base_doc(opening="0", closing="999", events=[
            ev("e1", type_="bogus", amount="1"),
        ]))
        self.assertEqual(rep["findings"][-1]["code"], wr.CLOSING_BALANCE_MISMATCH)


class TestUnknownEventType(unittest.TestCase):
    def test_unknown_string_type(self):
        rep = run_ledger(base_doc(events=[ev("e1", type_="bonus")]))
        f = [x for x in rep["findings"] if x["code"] == wr.UNKNOWN_EVENT_TYPE]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["type"], "bonus")

    def test_case_sensitive(self):
        rep = run_ledger(base_doc(events=[ev("e1", type_="Reward")]))
        self.assertIn(wr.UNKNOWN_EVENT_TYPE, codes_of(rep))

    def test_numeric_type_flagged(self):
        doc = base_doc(events=[{"event_id": "e1", "type": 5, "amount": "1", "at": "2026-01-01T00:00:00Z"}])
        rep = run_ledger(doc)
        f = [x for x in rep["findings"] if x["code"] == wr.UNKNOWN_EVENT_TYPE]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["type"], 5)

    def test_all_four_known_types_accepted(self):
        for t in ("reward", "grant", "airdrop", "chat_spend"):
            rep = run_ledger(base_doc(events=[ev("e1", type_=t, amount="0")]))
            self.assertNotIn(wr.UNKNOWN_EVENT_TYPE, codes_of(rep), t)

    def test_unknown_type_event_not_applied(self):
        rep = run_ledger(base_doc(opening="5", closing="5", events=[ev("e1", type_="bogus", amount="100")]))
        self.assertEqual(rep["trace"][0]["applied"], False)
        self.assertEqual(rep["computed_closing_balance"], "5")


class TestInvalidAmountFinding(unittest.TestCase):
    def test_negative_amount_flagged(self):
        rep = run_ledger(base_doc(events=[ev("e1", amount="-1")]))
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_nan_amount_via_bare_token_flagged(self):
        rep = run_ledger_raw_text(
            '{"opening_balance":"0","closing_balance":"0","events":'
            '[{"event_id":"e1","type":"reward","amount":NaN,"at":"2026-01-01T00:00:00Z"}]}'
        )
        f = [x for x in rep["findings"] if x["code"] == wr.INVALID_AMOUNT]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["amount"], "NaN")

    def test_infinity_amount_via_bare_token_flagged(self):
        rep = run_ledger_raw_text(
            '{"opening_balance":"0","closing_balance":"0","events":'
            '[{"event_id":"e1","type":"reward","amount":Infinity,"at":"2026-01-01T00:00:00Z"}]}'
        )
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_negative_infinity_amount_flagged(self):
        rep = run_ledger_raw_text(
            '{"opening_balance":"0","closing_balance":"0","events":'
            '[{"event_id":"e1","type":"reward","amount":-Infinity,"at":"2026-01-01T00:00:00Z"}]}'
        )
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_null_amount_flagged(self):
        doc = base_doc(events=[{"event_id": "e1", "type": "reward", "amount": None, "at": "2026-01-01T00:00:00Z"}])
        rep = run_ledger(doc)
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_bool_amount_flagged(self):
        doc = base_doc(events=[{"event_id": "e1", "type": "reward", "amount": True, "at": "2026-01-01T00:00:00Z"}])
        rep = run_ledger(doc)
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_non_numeric_string_flagged(self):
        rep = run_ledger(base_doc(events=[ev("e1", amount="ten dollars")]))
        self.assertIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_invalid_amount_event_not_applied(self):
        rep = run_ledger(base_doc(opening="5", closing="5", events=[ev("e1", amount="-1")]))
        self.assertEqual(rep["trace"][0]["applied"], False)
        self.assertEqual(rep["computed_closing_balance"], "5")

    def test_zero_amount_not_flagged(self):
        rep = run_ledger(base_doc(events=[ev("e1", amount="0")]))
        self.assertNotIn(wr.INVALID_AMOUNT, codes_of(rep))

    def test_amount_as_json_number_accepted(self):
        doc = base_doc(opening="0", closing="10", events=[{"event_id": "e1", "type": "reward", "amount": 10, "at": "2026-01-01T00:00:00Z"}])
        rep = run_ledger(doc)
        self.assertEqual(rep["status"], "reconciled")

    def test_amount_as_json_string_accepted(self):
        doc = base_doc(opening="0", closing="10", events=[{"event_id": "e1", "type": "reward", "amount": "10", "at": "2026-01-01T00:00:00Z"}])
        rep = run_ledger(doc)
        self.assertEqual(rep["status"], "reconciled")

    def test_number_and_string_amount_give_identical_balance(self):
        num_doc = base_doc(opening="0", closing="10.5", events=[{"event_id": "e1", "type": "reward", "amount": 10.5, "at": "2026-01-01T00:00:00Z"}])
        str_doc = base_doc(opening="0", closing="10.5", events=[{"event_id": "e1", "type": "reward", "amount": "10.5", "at": "2026-01-01T00:00:00Z"}])
        rep_num = run_ledger(num_doc)
        rep_str = run_ledger(str_doc)
        self.assertEqual(rep_num["computed_closing_balance"], rep_str["computed_closing_balance"])
        self.assertEqual(rep_num["status"], "reconciled")


class TestInvalidTimestampFinding(unittest.TestCase):
    def test_garbage_at_flagged(self):
        rep = run_ledger(base_doc(events=[ev("e1", at="banana")]))
        self.assertIn(wr.INVALID_TIMESTAMP, codes_of(rep))

    def test_non_string_at_flagged(self):
        doc = base_doc(events=[{"event_id": "e1", "type": "reward", "amount": "1", "at": 12345}])
        rep = run_ledger(doc)
        self.assertIn(wr.INVALID_TIMESTAMP, codes_of(rep))

    def test_offset_timezone_flagged_invalid(self):
        rep = run_ledger(base_doc(events=[ev("e1", at="2026-01-01T00:00:00+05:00")]))
        self.assertIn(wr.INVALID_TIMESTAMP, codes_of(rep))

    def test_invalid_timestamp_event_still_applied(self):
        # A broken "at" only blocks the timestamp-ordering check, not
        # balance application: the amount is still valid, so it applies
        # normally. The ledger still reports "findings" overall because
        # INVALID_TIMESTAMP itself is a finding, even though the balance
        # math reconciles exactly.
        rep = run_ledger(base_doc(opening="0", closing="10", events=[ev("e1", amount="10", at="garbage")]))
        self.assertEqual(rep["trace"][0]["applied"], True)
        self.assertEqual(rep["computed_closing_balance"], "10")
        self.assertEqual(rep["closing_delta"], "0")
        self.assertNotIn(wr.CLOSING_BALANCE_MISMATCH, codes_of(rep))
        self.assertEqual(rep["status"], "findings")
        self.assertEqual(codes_of(rep), [wr.INVALID_TIMESTAMP])


class TestStructuralInputErrors(unittest.TestCase):
    def _expect_input_error(self, doc_text):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(doc_text)
            path = fh.name
        try:
            with self.assertRaises(wr.InputError):
                wr._load_ledger(path)
        finally:
            os.unlink(path)

    def test_top_level_not_object(self):
        self._expect_input_error(json.dumps([1, 2, 3]))

    def test_missing_opening_balance(self):
        self._expect_input_error(json.dumps({"closing_balance": "0", "events": []}))

    def test_missing_closing_balance(self):
        self._expect_input_error(json.dumps({"opening_balance": "0", "events": []}))

    def test_missing_events(self):
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0"}))

    def test_events_not_a_list(self):
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": {}}))

    def test_event_not_an_object(self):
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": ["nope"]}))

    def test_event_missing_event_id(self):
        bad = {"type": "reward", "amount": "1", "at": "2026-01-01T00:00:00Z"}
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_event_missing_type(self):
        bad = {"event_id": "e1", "amount": "1", "at": "2026-01-01T00:00:00Z"}
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_event_missing_amount(self):
        bad = {"event_id": "e1", "type": "reward", "at": "2026-01-01T00:00:00Z"}
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_event_missing_at(self):
        bad = {"event_id": "e1", "type": "reward", "amount": "1"}
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_empty_event_id_rejected(self):
        bad = ev("   ")
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_non_string_event_id_rejected(self):
        bad = {"event_id": 5, "type": "reward", "amount": "1", "at": "2026-01-01T00:00:00Z"}
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "0", "events": [bad]}))

    def test_opening_balance_non_numeric(self):
        self._expect_input_error(json.dumps({"opening_balance": "abc", "closing_balance": "0", "events": []}))

    def test_closing_balance_non_numeric(self):
        self._expect_input_error(json.dumps({"opening_balance": "0", "closing_balance": "abc", "events": []}))

    def test_opening_balance_nan_is_structural(self):
        text = '{"opening_balance":NaN,"closing_balance":"0","events":[]}'
        self._expect_input_error(text)

    def test_closing_balance_infinity_is_structural(self):
        text = '{"opening_balance":"0","closing_balance":Infinity,"events":[]}'
        self._expect_input_error(text)

    def test_malformed_json_syntax(self):
        self._expect_input_error("{not json")

    def test_file_not_found(self):
        with self.assertRaises(wr.InputError):
            wr._load_ledger("/definitely/not/a/real/path.json")


class TestTraceCompleteness(unittest.TestCase):
    def test_trace_length_matches_event_count(self):
        rep = run_ledger(base_doc(events=[ev("e1"), ev("e2"), ev("e3")]))
        self.assertEqual(len(rep["trace"]), 3)
        self.assertEqual(rep["event_count"], 3)

    def test_trace_entry_keys(self):
        rep = run_ledger(base_doc(events=[ev("e1")]))
        entry = rep["trace"][0]
        expected_keys = {"index", "event_id", "type", "at", "amount", "signed_amount",
                          "applied", "running_balance", "codes"}
        self.assertEqual(set(entry.keys()), expected_keys)

    def test_trace_running_balance_progression(self):
        rep = run_ledger(base_doc(opening="0", closing="15", events=[
            ev("e1", type_="reward", amount="10"),
            ev("e2", type_="chat_spend", amount="5"),
            ev("e3", type_="grant", amount="10"),
        ]))
        balances = [t["running_balance"] for t in rep["trace"]]
        self.assertEqual(balances, ["10", "5", "15"])

    def test_finding_counts_matches_findings(self):
        rep = run_ledger(base_doc(events=[ev("e1", type_="bogus"), ev("e2", type_="bogus")]))
        self.assertEqual(rep["finding_counts"].get(wr.UNKNOWN_EVENT_TYPE), 2)


class TestCanonicalJson(unittest.TestCase):
    def test_trailing_newline(self):
        rep = run_ledger(base_doc())
        text = wr.canonical_json(rep)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)

    def test_sorted_keys(self):
        rep = run_ledger(base_doc())
        text = wr.canonical_json(rep)
        self.assertLess(text.index('"closing_delta"'), text.index('"status"'))

    def test_compact_separators(self):
        rep = run_ledger(base_doc())
        text = wr.canonical_json(rep)
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_byte_identical_repeated_serialization(self):
        rep = run_ledger(base_doc(events=[ev("e1"), ev("e2", type_="bogus")]))
        self.assertEqual(wr.canonical_json(rep), wr.canonical_json(rep))

    def test_finding_order_deterministic_across_runs(self):
        doc = base_doc(opening="10", closing="0", events=[
            ev("e1", type_="chat_spend", amount="50"),
            ev("e2", type_="bogus"),
            ev("e1", type_="reward", amount="1"),
        ])
        a = run_ledger(doc)
        b = run_ledger(doc)
        self.assertEqual(wr.canonical_json(a), wr.canonical_json(b))

    def test_ascii_only(self):
        rep = run_ledger(base_doc())
        text = wr.canonical_json(rep)
        self.assertEqual(text, text.encode("ascii", "backslashreplace").decode("ascii"))


class TestFindingSortOrder(unittest.TestCase):
    def test_opening_balance_finding_sorts_first(self):
        rep = run_ledger(base_doc(opening="-1", closing="-1", events=[
            ev("e1", type_="bogus"),
        ]))
        self.assertEqual(rep["findings"][0]["code"], wr.NEGATIVE_RUNNING_BALANCE)
        self.assertEqual(rep["findings"][0]["index"], -1)

    def test_multiple_codes_same_index_sorted_alphabetically(self):
        # e1 with an unknown type AND an invalid amount both at index 0:
        # INVALID_AMOUNT < UNKNOWN_EVENT_TYPE alphabetically.
        doc = base_doc(events=[ev("e1", type_="bogus", amount="-1")])
        rep = run_ledger(doc)
        this_index_codes = [f["code"] for f in rep["findings"] if f.get("index") == 0]
        self.assertEqual(this_index_codes, sorted(this_index_codes))
        self.assertEqual(this_index_codes, [wr.INVALID_AMOUNT, wr.UNKNOWN_EVENT_TYPE])

    def test_closing_mismatch_always_last(self):
        doc = base_doc(opening="0", closing="999", events=[
            ev("e1", type_="bogus"),
            ev("e2", amount="-1"),
        ])
        rep = run_ledger(doc)
        self.assertEqual(rep["findings"][-1]["code"], wr.CLOSING_BALANCE_MISMATCH)


class TestReconciledStatus(unittest.TestCase):
    def test_clean_ledger_status_reconciled(self):
        rep = run_ledger(base_doc(opening="0", closing="10", events=[ev("e1", amount="10")]))
        self.assertEqual(rep["status"], "reconciled")
        self.assertEqual(rep["findings"], [])
        self.assertEqual(rep["finding_counts"], {})

    def test_any_finding_flips_status(self):
        rep = run_ledger(base_doc(events=[ev("e1", type_="bogus")]))
        self.assertEqual(rep["status"], "findings")

    def test_sign_convention_field_present(self):
        rep = run_ledger(base_doc())
        self.assertEqual(rep["sign_convention"], {
            "reward": "+", "grant": "+", "airdrop": "+", "chat_spend": "-",
        })


class TestCliExitCodes(unittest.TestCase):
    def _write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        fh.write(text)
        fh.close()
        return fh.name

    def _run_args(self, args, stdin_text=None):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "wallet_reconcile.py")] + args,
            capture_output=True, text=True, input=stdin_text,
        )

    def test_exit_zero_on_ledger_ok_fixture(self):
        p = self._run_args([os.path.join(HERE, "ledger_ok.json")])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "reconciled")

    def test_exit_one_on_ledger_bad_fixture(self):
        p = self._run_args([os.path.join(HERE, "ledger_bad.json")])
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["status"], "findings")

    def test_exit_two_on_missing_file(self):
        p = self._run_args(["/no/such/file.json"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_exit_two_on_malformed_json(self):
        path = self._write("{not json")
        try:
            p = self._run_args([path])
            self.assertEqual(p.returncode, 2)
        finally:
            os.unlink(path)

    def test_stdin_dash_reads_stdin(self):
        text = json.dumps(base_doc(opening="0", closing="10", events=[ev("e1", amount="10")]))
        p = self._run_args(["-"], stdin_text=text)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "reconciled")

    def test_stdin_nan_amount_yields_finding_not_crash(self):
        text = ('{"opening_balance":"0","closing_balance":"0","events":'
                '[{"event_id":"e1","type":"reward","amount":NaN,"at":"2026-01-01T00:00:00Z"}]}')
        p = self._run_args(["-"], stdin_text=text)
        self.assertEqual(p.returncode, 1)
        report = json.loads(p.stdout)
        self.assertIn(wr.INVALID_AMOUNT, [f["code"] for f in report["findings"]])

    def test_output_flag_writes_file(self):
        outpath = os.path.join(tempfile.gettempdir(), "wr_test_output.json")
        try:
            p = self._run_args([os.path.join(HERE, "ledger_ok.json"), "-o", outpath])
            self.assertEqual(p.returncode, 0)
            self.assertEqual(p.stdout, "")
            with open(outpath) as fh:
                report = json.load(fh)
            self.assertEqual(report["status"], "reconciled")
        finally:
            if os.path.exists(outpath):
                os.unlink(outpath)

    def test_output_long_flag_writes_file(self):
        outpath = os.path.join(tempfile.gettempdir(), "wr_test_output2.json")
        try:
            p = self._run_args([os.path.join(HERE, "ledger_ok.json"), "--output", outpath])
            self.assertEqual(p.returncode, 0)
            self.assertTrue(os.path.exists(outpath))
        finally:
            if os.path.exists(outpath):
                os.unlink(outpath)

    def test_repeat_runs_byte_identical(self):
        out1 = os.path.join(tempfile.gettempdir(), "wr_run1.json")
        out2 = os.path.join(tempfile.gettempdir(), "wr_run2.json")
        try:
            self._run_args([os.path.join(HERE, "ledger_bad.json"), "-o", out1])
            self._run_args([os.path.join(HERE, "ledger_bad.json"), "-o", out2])
            with open(out1, "rb") as f1, open(out2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())
        finally:
            for p in (out1, out2):
                if os.path.exists(p):
                    os.unlink(p)

    def test_precision_demo_no_float_corruption(self):
        doc = base_doc(
            opening=0,
            closing=Decimal("123456789012345678.123456789"),
            events=[{"event_id": "e1", "type": "reward",
                     "amount": Decimal("123456789012345678.123456789"),
                     "at": "2026-01-01T00:00:00Z"}],
        )
        text = json.dumps(doc, default=str)
        p = self._run_args(["-"], stdin_text=text)
        self.assertEqual(p.returncode, 0)
        report = json.loads(p.stdout)
        self.assertEqual(report["computed_closing_balance"], "123456789012345678.123456789")

    def test_ledger_bad_triggers_all_seven_codes(self):
        p = self._run_args([os.path.join(HERE, "ledger_bad.json")])
        report = json.loads(p.stdout)
        codes = {f["code"] for f in report["findings"]}
        expected = {
            wr.DUPLICATE_EVENT_ID, wr.OUT_OF_ORDER_TIMESTAMP, wr.NEGATIVE_RUNNING_BALANCE,
            wr.CLOSING_BALANCE_MISMATCH, wr.UNKNOWN_EVENT_TYPE, wr.INVALID_AMOUNT,
            wr.INVALID_TIMESTAMP,
        }
        self.assertEqual(codes, expected)

    def test_usage_error_missing_positional_exits_two(self):
        p = self._run_args([])
        self.assertEqual(p.returncode, 2)

    def test_help_flag_exits_zero(self):
        p = self._run_args(["--help"])
        self.assertEqual(p.returncode, 0)


class TestDuplicateJsonKeysAreRejected(unittest.TestCase):
    """A repeated object key used to be resolved silently by the parser.

    json.load() builds objects with dict(pairs), so a document may state
    two different amounts for one event and the tool sees only the second.
    Two shapes, both bad, and the second is the worse one:

      wrong value LAST  -> the tool reports a balance discrepancy that is
                           really an ambiguous file, sending a reader
                           after a number no event claims;
      wrong value FIRST -> the tool exits 0 and calls the document
                           "reconciled".

    ledger_duplicate_key.json is the committed reproducer, in the second
    orientation.
    """

    def _write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def _run(self, args, stdin_text=None):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "wallet_reconcile.py")] + args,
            capture_output=True, text=True, input=stdin_text)

    # ---- the committed fixture -------------------------------------
    def test_the_committed_fixture_is_rejected(self):
        p = self._run([os.path.join(HERE, "ledger_duplicate_key.json")])
        self.assertEqual(p.returncode, 2, p.stdout[:300])
        self.assertIn("INVALID_INPUT", p.stderr)
        self.assertIn("duplicate key", p.stderr)

    def test_the_committed_fixture_really_does_contain_a_duplicate(self):
        """Pins the fixture itself, so it cannot rot into a clean file."""
        with open(os.path.join(HERE, "ledger_duplicate_key.json"),
                  encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count('"amount"'), 2)
        # and the stdlib silently resolves it, which is the whole point
        self.assertEqual(
            json.loads(text)["events"][0]["amount"], "10",
            "the stdlib no longer keeps the last duplicate; this fixture "
            "and the rule it demonstrates need re-examining")

    def test_the_fixture_produces_no_report_at_all(self):
        p = self._run([os.path.join(HERE, "ledger_duplicate_key.json")])
        self.assertEqual(p.stdout, "")

    # ---- both orientations, built here so the harm is explicit ------
    def _ledger_with(self, first, second):
        return ('{"opening_balance": "0", "closing_balance": "10", "events": ['
                '{"event_id": "e1", "type": "reward", "amount": "%s", '
                '"amount": "%s", "at": "2026-01-01T00:00:00Z"}]}'
                % (first, second))

    def test_wrong_value_last_is_rejected(self):
        path = self._write(self._ledger_with("10", "9999"))
        p = self._run([path])
        self.assertEqual(p.returncode, 2)
        self.assertIn("duplicate key 'amount'", p.stderr)

    def test_wrong_value_first_is_rejected(self):
        path = self._write(self._ledger_with("9999", "10"))
        p = self._run([path])
        self.assertEqual(p.returncode, 2)
        self.assertIn("duplicate key 'amount'", p.stderr)

    def test_a_duplicate_whose_values_agree_is_still_rejected(self):
        """Same value twice is still a document that says a thing twice.

        Accepting it would mean the rule depends on the values, so a
        reader could not tell from the exit code whether the parser had
        chosen for them.
        """
        path = self._write(self._ledger_with("10", "10"))
        p = self._run([path])
        self.assertEqual(p.returncode, 2)

    # ---- where the duplicate sits ----------------------------------
    def test_duplicate_at_the_top_level_is_rejected(self):
        path = self._write('{"opening_balance": "0", "opening_balance": "5", '
                           '"closing_balance": "0", "events": []}')
        p = self._run([path])
        self.assertEqual(p.returncode, 2)
        self.assertIn("duplicate key 'opening_balance'", p.stderr)

    def test_duplicate_of_a_key_the_tool_does_not_read_is_rejected(self):
        """The rule is about the document, not about the fields we use."""
        path = self._write('{"opening_balance": "0", "closing_balance": "0", '
                           '"events": [], "note": "a", "note": "b"}')
        p = self._run([path])
        self.assertEqual(p.returncode, 2)
        self.assertIn("duplicate key 'note'", p.stderr)

    def test_the_message_names_the_event_it_is_in(self):
        path = self._write(
            '{"opening_balance": "0", "closing_balance": "0", "events": ['
            '{"event_id": "first", "type": "reward", "amount": "1", '
            '"at": "2026-01-01T00:00:00Z"},'
            '{"event_id": "second", "type": "reward", "amount": "1", '
            '"amount": "2", "at": "2026-01-02T00:00:00Z"}]}')
        p = self._run([path])
        self.assertEqual(p.returncode, 2)
        self.assertIn("second", p.stderr)
        self.assertNotIn("event_id 'first'", p.stderr)

    def test_the_stdin_path_rejects_it_too(self):
        """Both entry points share one loader; this pins that they do."""
        p = self._run(["-"], stdin_text=self._ledger_with("9999", "10"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("duplicate key", p.stderr)

    # ---- valid input is untouched ----------------------------------
    def test_the_clean_fixture_is_unaffected(self):
        p = self._run([os.path.join(HERE, "ledger_ok.json")])
        self.assertEqual(p.returncode, 0, p.stderr[:300])
        self.assertEqual(json.loads(p.stdout)["status"], "reconciled")

    def test_the_findings_fixture_still_exits_1(self):
        """A setup-error change must not touch the findings path."""
        p = self._run([os.path.join(HERE, "ledger_bad.json")])
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["status"], "findings")

    def test_repeated_keys_in_DIFFERENT_objects_are_fine(self):
        """Every event has an "amount"; that is not a duplicate."""
        path = self._write(
            '{"opening_balance": "0", "closing_balance": "3", "events": ['
            '{"event_id": "e1", "type": "reward", "amount": "1", '
            '"at": "2026-01-01T00:00:00Z"},'
            '{"event_id": "e2", "type": "reward", "amount": "2", '
            '"at": "2026-01-02T00:00:00Z"}]}')
        p = self._run([path])
        self.assertEqual(p.returncode, 0, p.stderr[:300])

    def test_decimal_and_non_finite_handling_survive_the_new_hook(self):
        """object_pairs_hook composes with parse_float/parse_constant."""
        path = self._write('{"opening_balance": 1.5, "closing_balance": 1.5, '
                           '"events": []}')
        opening, closing, events = wr._load_ledger(path)
        self.assertIsInstance(opening, Decimal)
        self.assertEqual(opening, Decimal("1.5"))

    def test_a_nan_literal_is_still_the_sentinel_not_a_float(self):
        path = self._write('{"opening_balance": "0", "closing_balance": "0", '
                           '"events": [{"event_id": "e1", "type": "reward", '
                           '"amount": NaN, "at": "2026-01-01T00:00:00Z"}]}')
        p = self._run([path])
        self.assertEqual(p.returncode, 1, p.stderr[:300])
        codes = {f["code"] for f in json.loads(p.stdout)["findings"]}
        self.assertIn("INVALID_AMOUNT", codes)

    # ---- the hook itself -------------------------------------------
    def test_the_hook_builds_an_ordinary_dict_when_keys_are_unique(self):
        self.assertEqual(
            wr._object_pairs_no_duplicates([("a", 1), ("b", 2)]),
            {"a": 1, "b": 2})

    def test_the_hook_raises_on_the_first_repeat(self):
        with self.assertRaises(wr._DuplicateKey) as caught:
            wr._object_pairs_no_duplicates([("a", 1), ("b", 2), ("a", 3),
                                            ("b", 4)])
        self.assertEqual(caught.exception.key, "a")

    def test_the_hook_reports_it_as_input_error_not_a_finding(self):
        """Exit 2, never exit 1: the file could not be read, full stop."""
        path = self._write(self._ledger_with("1", "2"))
        with self.assertRaises(wr.InputError):
            wr._load_ledger(path)


class TestSafeRepr(unittest.TestCase):
    def test_string_passthrough(self):
        self.assertEqual(wr._safe_repr("hi"), "hi")

    def test_int_passthrough(self):
        self.assertEqual(wr._safe_repr(5), 5)

    def test_none_passthrough(self):
        self.assertIsNone(wr._safe_repr(None))

    def test_bool_passthrough(self):
        self.assertEqual(wr._safe_repr(True), True)

    def test_decimal_becomes_string(self):
        self.assertEqual(wr._safe_repr(Decimal("1.5")), "1.5")

    def test_nonfinite_becomes_token(self):
        self.assertEqual(wr._safe_repr(wr._NonFinite("NaN")), "NaN")

    def test_list_becomes_repr_string(self):
        result = wr._safe_repr([1, 2])
        self.assertIsInstance(result, str)


class TestAmtStr(unittest.TestCase):
    def test_normalizes_negative_zero(self):
        self.assertEqual(wr._amt_str(Decimal("-0")), "0")

    def test_normalizes_negative_zero_with_scale(self):
        self.assertEqual(wr._amt_str(Decimal("-0.00")), "0")

    def test_preserves_nonzero(self):
        self.assertEqual(wr._amt_str(Decimal("5.50")), "5.50")

    def test_preserves_positive_zero(self):
        self.assertEqual(wr._amt_str(Decimal("0")), "0")


class TestNonFiniteSentinel(unittest.TestCase):
    def test_equality(self):
        self.assertEqual(wr._NonFinite("NaN"), wr._NonFinite("NaN"))

    def test_inequality_different_token(self):
        self.assertNotEqual(wr._NonFinite("NaN"), wr._NonFinite("Infinity"))

    def test_not_equal_to_plain_string(self):
        self.assertNotEqual(wr._NonFinite("NaN"), "NaN")

    def test_repr(self):
        self.assertEqual(repr(wr._NonFinite("Infinity")), "Infinity")


class TestMixedRealisticLedger(unittest.TestCase):
    def test_multi_event_clean_ledger_with_high_precision(self):
        rep = run_ledger(base_doc(
            opening="1000.123456789",
            closing="1055.373456789",
            events=[
                ev("e1", "reward", "50", "2026-01-01T00:00:00Z"),
                ev("e2", "grant", "25.25", "2026-01-02T00:00:00Z"),
                ev("e3", "airdrop", "0.000000001", "2026-01-03T00:00:00Z"),
                ev("e4", "chat_spend", "20.000000001", "2026-01-04T00:00:00Z"),
            ],
        ))
        self.assertEqual(rep["status"], "reconciled")
        self.assertEqual(len(rep["trace"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

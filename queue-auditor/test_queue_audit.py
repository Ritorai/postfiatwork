"""test_queue_audit.py -- unittest suite for queue_audit.py.

Run with:  python3 -m unittest test_queue_audit -v

Standard library only (unittest, json, os, sys, subprocess, tempfile,
copy). No third-party imports.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import queue_audit  # noqa: E402


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(THIS_DIR, "queue_audit.py")
CLEAN_FIXTURE = os.path.join(THIS_DIR, "snapshot_clean.json")
DIRTY_FIXTURE = os.path.join(THIS_DIR, "snapshot_dirty.json")


def run_cli(args, stdin_text=None):
    cmd = [sys.executable, SCRIPT_PATH] + args
    proc = subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def make_task(**overrides):
    base = {
        "task_id": "T-1",
        "title": "Sample task",
        "status": "outstanding",
        "list": "outstanding",
        "reward": 10,
        "created_at": "2026-01-01T00:00:00Z",
        "deadline": "2026-01-02T00:00:00Z",
    }
    base.update(overrides)
    return base


def make_document(tasks, summary=None):
    return {"tasks": tasks, "summary": {} if summary is None else summary}


def codes_of(findings):
    return [f["code"] for f in findings]


def task_ids_with_code(findings, code):
    return sorted(f["task_id"] for f in findings if f["code"] == code)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------


class TestConstants(unittest.TestCase):
    def test_all_codes_are_distinct_strings(self):
        self.assertEqual(len(queue_audit.ALL_CODES), len(set(queue_audit.ALL_CODES)))
        for code in queue_audit.ALL_CODES:
            self.assertIsInstance(code, str)

    def test_duplicate_task_id_value(self):
        self.assertEqual(queue_audit.DUPLICATE_TASK_ID, "DUPLICATE_TASK_ID")

    def test_malformed_record_value(self):
        self.assertEqual(queue_audit.MALFORMED_RECORD, "MALFORMED_RECORD")

    def test_status_list_mismatch_value(self):
        self.assertEqual(queue_audit.STATUS_LIST_MISMATCH, "STATUS_LIST_MISMATCH")

    def test_invalid_reward_value(self):
        self.assertEqual(queue_audit.INVALID_REWARD, "INVALID_REWARD")

    def test_invalid_timestamp_value(self):
        self.assertEqual(queue_audit.INVALID_TIMESTAMP, "INVALID_TIMESTAMP")

    def test_deadline_before_created_value(self):
        self.assertEqual(queue_audit.DEADLINE_BEFORE_CREATED, "DEADLINE_BEFORE_CREATED")

    def test_summary_count_mismatch_value(self):
        self.assertEqual(queue_audit.SUMMARY_COUNT_MISMATCH, "SUMMARY_COUNT_MISMATCH")

    def test_exit_codes(self):
        self.assertEqual(queue_audit.EXIT_CLEAN, 0)
        self.assertEqual(queue_audit.EXIT_FINDINGS, 1)
        self.assertEqual(queue_audit.EXIT_INVALID_INPUT, 2)

    def test_required_fields_tuple(self):
        self.assertEqual(
            queue_audit.REQUIRED_FIELDS,
            ("task_id", "title", "status", "list", "reward", "created_at", "deadline"),
        )


# ---------------------------------------------------------------------
# canonical_dumps / build_report
# ---------------------------------------------------------------------


class TestCanonicalDumps(unittest.TestCase):
    def test_ends_with_single_newline(self):
        text = queue_audit.canonical_dumps({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_spaces_after_separators(self):
        text = queue_audit.canonical_dumps({"b": 1, "a": [1, 2]})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_keys_sorted(self):
        text = queue_audit.canonical_dumps({"z": 1, "a": 2, "m": 3})
        self.assertLess(text.index('"a"'), text.index('"m"'))
        self.assertLess(text.index('"m"'), text.index('"z"'))

    def test_ensure_ascii_escapes_unicode(self):
        text = queue_audit.canonical_dumps({"title": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u00e9", text)

    def test_deterministic_across_calls(self):
        obj = {"x": [3, 1, 2], "y": "z"}
        self.assertEqual(queue_audit.canonical_dumps(obj), queue_audit.canonical_dumps(copy.deepcopy(obj)))

    def test_output_is_valid_json_minus_trailing_newline(self):
        text = queue_audit.canonical_dumps({"a": 1, "b": [1, 2, 3]})
        parsed = json.loads(text)
        self.assertEqual(parsed, {"a": 1, "b": [1, 2, 3]})


class TestBuildReport(unittest.TestCase):
    def test_clean_report(self):
        report = queue_audit.build_report([], 3)
        self.assertEqual(report["result"], "clean")
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(report["task_count"], 3)
        self.assertEqual(report["findings"], [])

    def test_findings_report(self):
        findings = [{"code": "X", "task_id": "T1", "detail": "d"}]
        report = queue_audit.build_report(findings, 1)
        self.assertEqual(report["result"], "findings")
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["task_count"], 1)


# ---------------------------------------------------------------------
# parse_utc_timestamp -- data-driven
# ---------------------------------------------------------------------


class TestParseTimestamp(unittest.TestCase):
    pass


TIMESTAMP_CASES = [
    ("z_suffix_valid", "2026-01-01T00:00:00Z", True),
    ("lowercase_z_valid", "2026-01-01T00:00:00z", True),
    ("plus_zero_offset_valid", "2026-01-01T00:00:00+00:00", True),
    ("minus_zero_offset_valid", "2026-01-01T00:00:00-00:00", True),
    ("fractional_seconds_valid", "2026-01-01T00:00:00.123456Z", True),
    ("leap_day_valid", "2024-02-29T12:00:00Z", True),
    ("end_of_year_valid", "2025-12-31T23:59:59Z", True),
    ("naive_no_offset_invalid", "2026-01-01T00:00:00", False),
    ("positive_offset_invalid", "2026-01-01T00:00:00+05:00", False),
    ("negative_offset_invalid", "2026-01-01T00:00:00-05:00", False),
    ("date_only_invalid", "2026-01-01", False),
    ("garbage_string_invalid", "not-a-timestamp", False),
    ("empty_string_invalid", "", False),
    ("invalid_month_invalid", "2026-13-01T00:00:00Z", False),
    ("invalid_day_invalid", "2026-01-99T00:00:00Z", False),
    ("integer_value_invalid", 1234567890, False),
    ("none_value_invalid", None, False),
    ("list_value_invalid", ["2026-01-01T00:00:00Z"], False),
    ("trailing_garbage_invalid", "2026-01-01T00:00:00Zjunk", False),
]


def _make_timestamp_test(value, expected_valid):
    def test(self):
        result = queue_audit.parse_utc_timestamp(value)
        if expected_valid:
            self.assertIsNotNone(result, "expected %r to parse as valid UTC" % (value,))
            self.assertIsNone(result.tzinfo.utcoffset(result) if False else None) if False else None
        else:
            self.assertIsNone(result, "expected %r to be rejected" % (value,))
    return test


for _name, _value, _expected in TIMESTAMP_CASES:
    setattr(TestParseTimestamp, "test_timestamp_%s" % _name, _make_timestamp_test(_value, _expected))


# ---------------------------------------------------------------------
# validate_reward -- data-driven
# ---------------------------------------------------------------------


class TestValidateReward(unittest.TestCase):
    def test_positive_int_returns_decimal(self):
        value, error = queue_audit.validate_reward(10)
        self.assertIsNone(error)
        self.assertEqual(value, Decimal(10))

    def test_positive_decimal_returns_decimal(self):
        value, error = queue_audit.validate_reward(Decimal("12.5"))
        self.assertIsNone(error)
        self.assertEqual(value, Decimal("12.5"))
        self.assertIsInstance(value, Decimal)

    def test_bare_finite_float_is_rejected(self):
        # validate_reward's contract requires callers to parse JSON with
        # parse_float=Decimal. A bare finite float reaching here means
        # that contract was violated, and must be rejected rather than
        # silently accepted (see the precision-loss bug this guards
        # against, exercised in TestRewardPrecisionRegression below).
        value, error = queue_audit.validate_reward(12.5)
        self.assertIsNone(value)
        self.assertIsNotNone(error)


REWARD_CASES = [
    ("zero_int_valid", 0, True),
    ("zero_decimal_valid", Decimal("0.0"), True),
    ("large_int_valid", 10_000_000, True),
    ("small_positive_decimal_valid", Decimal("0.01"), True),
    ("high_precision_decimal_valid", Decimal("123456789012345678.123456"), True),
    ("negative_int_invalid", -1, False),
    ("negative_decimal_invalid", Decimal("-0.5"), False),
    ("negative_tiny_decimal_invalid", Decimal("-1e-400"), False),
    ("negative_large_invalid", -999999, False),
    ("string_numeric_invalid", "10", False),
    ("string_non_numeric_invalid", "abc", False),
    ("string_empty_invalid", "", False),
    ("boolean_true_invalid", True, False),
    ("boolean_false_invalid", False, False),
    ("list_invalid", [1, 2, 3], False),
    ("dict_invalid", {"amount": 5}, False),
    ("none_invalid", None, False),
    ("nan_invalid", float("nan"), False),
    ("pos_inf_invalid", float("inf"), False),
    ("neg_inf_invalid", float("-inf"), False),
    ("decimal_nan_invalid", Decimal("NaN"), False),
    ("decimal_infinity_invalid", Decimal("Infinity"), False),
    ("bare_finite_float_invalid", 12.5, False),
]


def _make_reward_test(value, expected_valid):
    def test(self):
        decimal_value, error = queue_audit.validate_reward(value)
        if expected_valid:
            self.assertIsNone(error)
            self.assertIsInstance(decimal_value, Decimal)
            self.assertGreaterEqual(decimal_value, 0)
        else:
            self.assertIsNotNone(error)
            self.assertIsNone(decimal_value)
    return test


for _name, _value, _expected in REWARD_CASES:
    setattr(TestValidateReward, "test_reward_%s" % _name, _make_reward_test(_value, _expected))


# ---------------------------------------------------------------------
# audit_document -- malformed records (missing / wrong type per field)
# ---------------------------------------------------------------------


class TestMalformedRecords(unittest.TestCase):
    pass


STRING_FIELDS = ["task_id", "title", "status", "list", "created_at", "deadline"]


def _make_missing_field_test(field):
    def test(self):
        task = make_task()
        del task[field]
        findings, count = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
        self.assertTrue(
            any(
                f["code"] == queue_audit.MALFORMED_RECORD and field in f["detail"]
                for f in findings
            )
        )
        self.assertEqual(count, 1)
    return test


def _make_wrong_type_field_test(field):
    def test(self):
        task = make_task()
        task[field] = 42  # every one of these fields must be a string
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
    return test


def _make_null_field_test(field):
    def test(self):
        task = make_task()
        task[field] = None
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
    return test


def _make_empty_string_field_test(field):
    def test(self):
        task = make_task()
        task[field] = ""
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
    return test


for _field in STRING_FIELDS:
    setattr(TestMalformedRecords, "test_missing_%s" % _field, _make_missing_field_test(_field))
    setattr(TestMalformedRecords, "test_wrong_type_%s" % _field, _make_wrong_type_field_test(_field))
    setattr(TestMalformedRecords, "test_null_%s" % _field, _make_null_field_test(_field))
    setattr(TestMalformedRecords, "test_empty_string_%s" % _field, _make_empty_string_field_test(_field))


class TestMalformedRecordsMisc(unittest.TestCase):
    def test_missing_reward(self):
        task = make_task()
        del task["reward"]
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
        self.assertNotIn(queue_audit.INVALID_REWARD, codes_of(findings))

    def test_null_reward(self):
        task = make_task(reward=None)
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))

    def test_record_not_a_dict_string(self):
        findings, count = queue_audit.audit_document(make_document(["not-a-task"]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))
        self.assertEqual(count, 1)
        self.assertEqual(findings[0]["task_id"], "<index:0>")

    def test_record_not_a_dict_null(self):
        findings, _ = queue_audit.audit_document(make_document([None]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))

    def test_record_not_a_dict_number(self):
        findings, _ = queue_audit.audit_document(make_document([42]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))

    def test_record_not_a_dict_list(self):
        findings, _ = queue_audit.audit_document(make_document([[1, 2]]))
        self.assertIn(queue_audit.MALFORMED_RECORD, codes_of(findings))

    def test_valid_task_produces_no_malformed_finding(self):
        findings, _ = queue_audit.audit_document(make_document([make_task()], summary={"outstanding": 1}))
        self.assertNotIn(queue_audit.MALFORMED_RECORD, codes_of(findings))

    def test_unknown_extra_fields_are_ignored(self):
        task = make_task(extra_field="ignored", another=123)
        findings, _ = queue_audit.audit_document(make_document([task], summary={"outstanding": 1}))
        self.assertEqual(findings, [])

    def test_multiple_missing_fields_all_reported(self):
        task = {"task_id": "T-1"}
        findings, _ = queue_audit.audit_document(make_document([task]))
        malformed = [f for f in findings if f["code"] == queue_audit.MALFORMED_RECORD]
        # title, status, list, reward, created_at, deadline all missing
        self.assertEqual(len(malformed), 6)


# ---------------------------------------------------------------------
# status/list mismatch
# ---------------------------------------------------------------------


STATUS_LIST_CASES = [
    ("matching_outstanding", "outstanding", "outstanding", False),
    ("matching_rewarded", "rewarded", "rewarded", False),
    ("matching_expired", "expired", "expired", False),
    ("mismatch_rewarded_outstanding", "rewarded", "outstanding", True),
    ("mismatch_expired_rewarded", "expired", "rewarded", True),
    ("mismatch_case_sensitive", "Outstanding", "outstanding", True),
]


class TestStatusListMismatch(unittest.TestCase):
    def test_skipped_when_status_missing(self):
        task = make_task()
        del task["status"]
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.STATUS_LIST_MISMATCH, codes_of(findings))

    def test_skipped_when_list_missing(self):
        task = make_task()
        del task["list"]
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.STATUS_LIST_MISMATCH, codes_of(findings))


def _make_status_list_test(status, list_val, expect_mismatch):
    def test(self):
        task = make_task(status=status, list=list_val)
        findings, _ = queue_audit.audit_document(make_document([task]))
        if expect_mismatch:
            self.assertIn(queue_audit.STATUS_LIST_MISMATCH, codes_of(findings))
        else:
            self.assertNotIn(queue_audit.STATUS_LIST_MISMATCH, codes_of(findings))
    return test


for _name, _status, _list, _expect in STATUS_LIST_CASES:
    setattr(TestStatusListMismatch, "test_%s" % _name, _make_status_list_test(_status, _list, _expect))


# ---------------------------------------------------------------------
# duplicate task_id
# ---------------------------------------------------------------------


class TestDuplicateTaskId(unittest.TestCase):
    def test_no_duplicates(self):
        tasks = [make_task(task_id="A"), make_task(task_id="B")]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        self.assertNotIn(queue_audit.DUPLICATE_TASK_ID, codes_of(findings))

    def test_single_pair_duplicate(self):
        tasks = [make_task(task_id="A"), make_task(task_id="A")]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        dup = [f for f in findings if f["code"] == queue_audit.DUPLICATE_TASK_ID]
        self.assertEqual(len(dup), 1)
        self.assertIn("2 times", dup[0]["detail"])

    def test_triple_duplicate(self):
        tasks = [make_task(task_id="A"), make_task(task_id="A"), make_task(task_id="A")]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        dup = [f for f in findings if f["code"] == queue_audit.DUPLICATE_TASK_ID]
        self.assertEqual(len(dup), 1)
        self.assertIn("3 times", dup[0]["detail"])

    def test_multiple_duplicate_groups(self):
        tasks = [
            make_task(task_id="A"), make_task(task_id="A"),
            make_task(task_id="B"), make_task(task_id="B"), make_task(task_id="B"),
            make_task(task_id="C"),
        ]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        dup_ids = task_ids_with_code(findings, queue_audit.DUPLICATE_TASK_ID)
        self.assertEqual(dup_ids, ["A", "B"])

    def test_malformed_task_ids_not_counted_as_duplicates(self):
        t1 = make_task()
        del t1["task_id"]
        t2 = make_task()
        del t2["task_id"]
        findings, _ = queue_audit.audit_document(make_document([t1, t2]))
        self.assertNotIn(queue_audit.DUPLICATE_TASK_ID, codes_of(findings))

    def test_duplicate_finding_task_id_matches_shared_id(self):
        tasks = [make_task(task_id="XYZ"), make_task(task_id="XYZ")]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        dup = [f for f in findings if f["code"] == queue_audit.DUPLICATE_TASK_ID][0]
        self.assertEqual(dup["task_id"], "XYZ")


# ---------------------------------------------------------------------
# deadline before created
# ---------------------------------------------------------------------


class TestDeadlineBeforeCreated(unittest.TestCase):
    def test_deadline_after_created_is_fine(self):
        task = make_task(created_at="2026-01-01T00:00:00Z", deadline="2026-02-01T00:00:00Z")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))

    def test_deadline_before_created_flagged(self):
        task = make_task(created_at="2026-02-01T00:00:00Z", deadline="2026-01-01T00:00:00Z")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))

    def test_equal_timestamps_not_flagged(self):
        task = make_task(created_at="2026-01-01T00:00:00Z", deadline="2026-01-01T00:00:00Z")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))

    def test_skipped_when_created_at_invalid(self):
        task = make_task(created_at="garbage", deadline="2026-01-01T00:00:00Z")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))
        self.assertIn(queue_audit.INVALID_TIMESTAMP, codes_of(findings))

    def test_skipped_when_deadline_invalid(self):
        task = make_task(created_at="2026-01-01T00:00:00Z", deadline="garbage")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))

    def test_one_second_before_flagged(self):
        task = make_task(created_at="2026-01-01T00:00:01Z", deadline="2026-01-01T00:00:00Z")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))

    def test_different_utc_representations_equal_not_flagged(self):
        # same instant, different spelling (Z vs +00:00)
        task = make_task(created_at="2026-01-01T00:00:00Z", deadline="2026-01-01T00:00:00+00:00")
        findings, _ = queue_audit.audit_document(make_document([task]))
        self.assertNotIn(queue_audit.DEADLINE_BEFORE_CREATED, codes_of(findings))


# ---------------------------------------------------------------------
# summary count mismatch
# ---------------------------------------------------------------------


class TestSummaryCountMismatch(unittest.TestCase):
    def test_matching_counts_no_findings(self):
        tasks = [make_task(task_id="A", list="outstanding"), make_task(task_id="B", list="rewarded", status="rewarded")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 1, "rewarded": 1}))
        self.assertNotIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_mismatch_too_high(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 5}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_mismatch_too_low(self):
        tasks = [make_task(task_id="A", list="outstanding"), make_task(task_id="B", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 1}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_bucket_named_with_zero_tasks_matches(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 1, "rewarded": 0}))
        self.assertNotIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_bucket_in_summary_but_absent_from_tasks(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 1, "phantom_bucket": 3}))
        mismatches = task_ids_with_code(findings, queue_audit.SUMMARY_COUNT_MISMATCH)
        self.assertIn("phantom_bucket", mismatches)

    def test_bucket_in_tasks_but_absent_from_summary(self):
        tasks = [make_task(task_id="A", list="mystery_bucket", status="mystery_bucket")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {}))
        mismatches = task_ids_with_code(findings, queue_audit.SUMMARY_COUNT_MISMATCH)
        self.assertIn("mystery_bucket", mismatches)

    def test_empty_tasks_and_empty_summary_is_clean(self):
        findings, count = queue_audit.audit_document(make_document([], {}))
        self.assertEqual(findings, [])
        self.assertEqual(count, 0)

    def test_empty_tasks_nonzero_summary_mismatch(self):
        findings, _ = queue_audit.audit_document(make_document([], {"outstanding": 2}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_non_numeric_summary_value_string(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": "one"}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_non_numeric_summary_value_null(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": None}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_boolean_summary_value_rejected(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": True}))
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_malformed_task_not_counted_in_any_bucket(self):
        bad = {"title": "no id"}  # missing task_id and list
        findings, _ = queue_audit.audit_document(make_document([bad], {}))
        self.assertNotIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_float_summary_value_matches_int_count(self):
        tasks = [make_task(task_id="A", list="outstanding")]
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 1.0}))
        self.assertNotIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))


# ---------------------------------------------------------------------
# top-level document shape errors (ValueError -> exit 2 at CLI layer)
# ---------------------------------------------------------------------


class TestDocumentShape(unittest.TestCase):
    def test_top_level_not_a_dict_list(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document([1, 2, 3])

    def test_top_level_not_a_dict_string(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document("hello")

    def test_top_level_not_a_dict_number(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document(42)

    def test_top_level_not_a_dict_null(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document(None)

    def test_tasks_missing(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document({"summary": {}})

    def test_tasks_wrong_type(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document({"tasks": "not-a-list", "summary": {}})

    def test_tasks_is_dict_invalid(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document({"tasks": {"a": 1}, "summary": {}})

    def test_summary_wrong_type(self):
        with self.assertRaises(ValueError):
            queue_audit.audit_document({"tasks": [], "summary": "nope"})

    def test_summary_missing_defaults_to_empty(self):
        findings, count = queue_audit.audit_document({"tasks": []})
        self.assertEqual(findings, [])
        self.assertEqual(count, 0)

    def test_summary_missing_with_tasks_causes_mismatch(self):
        findings, _ = queue_audit.audit_document({"tasks": [make_task(task_id="A")]})
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes_of(findings))

    def test_empty_tasks_list_is_valid_shape(self):
        findings, count = queue_audit.audit_document({"tasks": [], "summary": {}})
        self.assertEqual(findings, [])
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------
# Determinism / sorting
# ---------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def test_findings_sorted_by_code_then_task_id_then_detail(self):
        tasks = [
            make_task(task_id="Z", reward=-1),
            make_task(task_id="A", reward=-1),
            make_task(task_id="M", reward=-1),
        ]
        findings, _ = queue_audit.audit_document(make_document(tasks))
        reward_findings = [f for f in findings if f["code"] == queue_audit.INVALID_REWARD]
        ids = [f["task_id"] for f in reward_findings]
        self.assertEqual(ids, sorted(ids))

    def test_repeated_audit_same_input_same_output(self):
        with open(DIRTY_FIXTURE, encoding="utf-8") as fh:
            doc = json.loads(fh.read(), parse_float=Decimal)
        findings1, count1 = queue_audit.audit_document(copy.deepcopy(doc))
        findings2, count2 = queue_audit.audit_document(copy.deepcopy(doc))
        self.assertEqual(findings1, findings2)
        self.assertEqual(count1, count2)
        text1 = queue_audit.canonical_dumps(queue_audit.build_report(findings1, count1))
        text2 = queue_audit.canonical_dumps(queue_audit.build_report(findings2, count2))
        self.assertEqual(text1, text2)

    def test_dict_key_order_in_source_does_not_affect_output(self):
        task_a = make_task(task_id="A")
        # Build an equivalent dict with keys inserted in a different order.
        task_a_reordered = {}
        for key in reversed(list(task_a.keys())):
            task_a_reordered[key] = task_a[key]
        findings1, _ = queue_audit.audit_document(make_document([task_a]))
        findings2, _ = queue_audit.audit_document(make_document([task_a_reordered]))
        self.assertEqual(findings1, findings2)

    def test_unicode_title_does_not_affect_other_findings(self):
        task = make_task(task_id="U1", title="日本語のタイトル 🎉")
        findings, _ = queue_audit.audit_document(make_document([task], {"outstanding": 1}))
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------
# CLI-level integration tests (subprocess)
# ---------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def test_clean_fixture_exit_0(self):
        code, out, err = run_cli([CLEAN_FIXTURE])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["result"], "clean")
        self.assertEqual(report["findings"], [])

    def test_dirty_fixture_exit_1(self):
        code, out, err = run_cli([DIRTY_FIXTURE])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["result"], "findings")
        self.assertGreater(report["finding_count"], 0)

    def test_dirty_fixture_triggers_all_codes(self):
        code, out, err = run_cli([DIRTY_FIXTURE])
        report = json.loads(out)
        observed = set(codes_of(report["findings"]))
        self.assertEqual(observed, set(queue_audit.ALL_CODES))

    def test_stdin_dirty_fixture(self):
        with open(DIRTY_FIXTURE, encoding="utf-8") as fh:
            text = fh.read()
        code, out, err = run_cli(["-"], stdin_text=text)
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["result"], "findings")

    def test_stdin_clean_fixture(self):
        with open(CLEAN_FIXTURE, encoding="utf-8") as fh:
            text = fh.read()
        code, out, err = run_cli(["-"], stdin_text=text)
        self.assertEqual(code, 0)

    def test_nonexistent_file_exit_2(self):
        code, out, err = run_cli(["/definitely/does/not/exist.json"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotEqual(err, "")

    def test_malformed_json_stdin_exit_2(self):
        code, out, err = run_cli(["-"], stdin_text="{not json")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_no_arguments_usage_error_exit_2(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_output_flag_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.json")
            code, out, err = run_cli([DIRTY_FIXTURE, "-o", out_path])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as fh:
                content = fh.read()
            report = json.loads(content)
            self.assertEqual(report["result"], "findings")

    def test_output_flag_long_form(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.json")
            code, out, err = run_cli([CLEAN_FIXTURE, "--output", out_path])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out_path))

    def test_two_runs_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = os.path.join(tmpdir, "r1.json")
            path2 = os.path.join(tmpdir, "r2.json")
            run_cli([DIRTY_FIXTURE, "-o", path1])
            run_cli([DIRTY_FIXTURE, "-o", path2])
            with open(path1, "rb") as fh:
                bytes1 = fh.read()
            with open(path2, "rb") as fh:
                bytes2 = fh.read()
            self.assertEqual(bytes1, bytes2)

    def test_invalid_top_level_shape_exit_2(self):
        code, out, err = run_cli(["-"], stdin_text=json.dumps([1, 2, 3]))
        self.assertEqual(code, 2)

    def test_output_ends_with_newline_on_stdout(self):
        code, out, err = run_cli([CLEAN_FIXTURE])
        self.assertTrue(out.endswith("\n"))

    def test_empty_stdin_exit_2(self):
        code, out, err = run_cli(["-"], stdin_text="")
        self.assertEqual(code, 2)

    def test_help_flag_exits_zero(self):
        code, out, err = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())


# ---------------------------------------------------------------------
# Full-document integration scenarios combining multiple issues
# ---------------------------------------------------------------------


class TestIntegrationScenarios(unittest.TestCase):
    def test_single_fully_valid_task_document(self):
        tasks = [make_task(task_id="OK-1")]
        findings, count = queue_audit.audit_document(make_document(tasks, {"outstanding": 1}))
        self.assertEqual(findings, [])
        self.assertEqual(count, 1)

    def test_reward_given_as_json_string_is_invalid_reward_not_malformed(self):
        task = make_task(task_id="STR", reward="10")
        findings, _ = queue_audit.audit_document(make_document([task]))
        codes = codes_of(findings)
        self.assertIn(queue_audit.INVALID_REWARD, codes)
        self.assertNotIn(queue_audit.MALFORMED_RECORD, codes)

    def test_task_with_unknown_list_bucket_flags_summary_mismatch_only(self):
        task = make_task(task_id="ODD", list="quarantine", status="quarantine")
        findings, _ = queue_audit.audit_document(make_document([task], {}))
        codes = codes_of(findings)
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes)
        self.assertNotIn(queue_audit.STATUS_LIST_MISMATCH, codes)
        self.assertNotIn(queue_audit.MALFORMED_RECORD, codes)

    def test_task_triggers_multiple_codes_simultaneously(self):
        task = make_task(
            task_id="MULTI",
            status="rewarded",
            list="outstanding",
            reward=-5,
            created_at="2026-05-01T00:00:00Z",
            deadline="2026-01-01T00:00:00Z",
        )
        findings, _ = queue_audit.audit_document(make_document([task], {}))
        codes = set(codes_of(findings))
        self.assertIn(queue_audit.STATUS_LIST_MISMATCH, codes)
        self.assertIn(queue_audit.INVALID_REWARD, codes)
        self.assertIn(queue_audit.DEADLINE_BEFORE_CREATED, codes)
        self.assertIn(queue_audit.SUMMARY_COUNT_MISMATCH, codes)

    def test_large_valid_document_no_findings(self):
        tasks = [make_task(task_id="T-%03d" % i) for i in range(50)]
        findings, count = queue_audit.audit_document(make_document(tasks, {"outstanding": 50}))
        self.assertEqual(findings, [])
        self.assertEqual(count, 50)

    def test_repeated_ids_three_and_four_times_each_one_finding(self):
        tasks = (
            [make_task(task_id="A")] * 3
            + [make_task(task_id="B")] * 4
        )
        findings, _ = queue_audit.audit_document(make_document(tasks, {"outstanding": 7}))
        dup = [f for f in findings if f["code"] == queue_audit.DUPLICATE_TASK_ID]
        self.assertEqual(len(dup), 2)
        details = {f["task_id"]: f["detail"] for f in dup}
        self.assertIn("3 times", details["A"])
        self.assertIn("4 times", details["B"])

    def test_mixed_valid_and_malformed_tasks(self):
        good = make_task(task_id="GOOD")
        bad = {"task_id": "BAD"}  # missing everything else
        findings, count = queue_audit.audit_document(make_document([good, bad], {"outstanding": 1}))
        self.assertEqual(count, 2)
        malformed_ids = task_ids_with_code(findings, queue_audit.MALFORMED_RECORD)
        self.assertEqual(malformed_ids, ["BAD"] * len(malformed_ids))

    def test_clean_fixture_file_is_actually_clean(self):
        with open(CLEAN_FIXTURE, encoding="utf-8") as fh:
            doc = json.load(fh, parse_float=Decimal)
        findings, count = queue_audit.audit_document(doc)
        self.assertEqual(findings, [])
        self.assertGreater(count, 0)

    def test_dirty_fixture_file_covers_every_code(self):
        with open(DIRTY_FIXTURE, encoding="utf-8") as fh:
            doc = json.load(fh, parse_float=Decimal)
        findings, _ = queue_audit.audit_document(doc)
        observed = set(codes_of(findings))
        self.assertEqual(observed, set(queue_audit.ALL_CODES))

    def test_clean_fixture_fractional_rewards_parse_without_precision_loss(self):
        # CLN-1002 (12.5) and CLN-1005 (75.25) are the fractional-reward
        # regression coverage for this fixture: loading with the same
        # parse_float=Decimal contract the CLI uses must not raise any
        # INVALID_REWARD finding for either of them.
        with open(CLEAN_FIXTURE, encoding="utf-8") as fh:
            doc = json.load(fh, parse_float=Decimal)
        findings, _ = queue_audit.audit_document(doc)
        self.assertEqual(codes_of(findings), [])


class TestRewardPrecisionRegression(unittest.TestCase):
    """Regression coverage for a real bug found during development.

    queue_audit.run() originally called json.loads(text) with no
    parse_float override, so every fractional/exponential JSON number
    literal was first parsed into a 64-bit binary float, and only THEN
    converted to Decimal via Decimal(str(raw)). For values with more
    significant digits than a float64 can hold, that round trip silently
    corrupted the reward (e.g. a reward of 123456789012345678.123456 was
    rounded to 123456789012345680, i.e. off by more than the fractional
    part, with no finding raised at all). It also let extremely small
    negative floats that underflow to -0.0 (e.g. -1e-400) pass as a
    non-negative reward.

    The fix parses with parse_float=Decimal so numeric literals are
    built directly from the original source text and never touch float,
    and validate_reward() now rejects any bare finite float outright
    instead of trusting a str()-round-trip conversion.
    """

    def test_high_precision_reward_preserved_exactly_via_parse_float_decimal(self):
        text = (
            '{"tasks":[{"task_id":"P1","title":"x","status":"outstanding",'
            '"list":"outstanding","reward":123456789012345678.123456,'
            '"created_at":"2026-01-01T00:00:00Z","deadline":"2026-01-02T00:00:00Z"}],'
            '"summary":{"outstanding":1}}'
        )
        doc = json.loads(text, parse_float=Decimal)
        raw_reward = doc["tasks"][0]["reward"]
        self.assertEqual(raw_reward, Decimal("123456789012345678.123456"))
        findings, _ = queue_audit.audit_document(doc)
        self.assertEqual(findings, [])

    def test_high_precision_reward_would_have_been_corrupted_by_plain_float_parsing(self):
        # Demonstrates the bug that was fixed: the *old* parsing strategy
        # (plain json.loads, no parse_float override) silently loses
        # precision for this exact value.
        text = '123456789012345678.123456'
        old_style_value = float(json.loads(text))
        self.assertNotEqual(
            Decimal(str(old_style_value)),
            Decimal("123456789012345678.123456"),
            "this float round-trip should lose precision -- if it stops "
            "losing precision the regression this test guards no longer applies",
        )

    def test_tiny_negative_reward_flagged_via_decimal_no_underflow(self):
        value, error = queue_audit.validate_reward(Decimal("-1e-400"))
        self.assertIsNone(value)
        self.assertIsNotNone(error)
        self.assertIn("negative", error)

    def test_tiny_negative_float_would_have_underflowed_to_negative_zero(self):
        # Demonstrates the bug: as a bare float, -1e-400 underflows to
        # -0.0, which is NOT less than zero, so the old
        # Decimal(str(raw)) < 0 check would have missed it.
        underflowed = float(Decimal("-1e-400"))
        self.assertEqual(underflowed, -0.0)
        self.assertFalse(underflowed < 0)

    def test_cli_end_to_end_preserves_high_precision_reward(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, "in.json")
            with open(in_path, "w", encoding="utf-8") as fh:
                fh.write(
                    '{"tasks":[{"task_id":"P1","title":"x","status":"outstanding",'
                    '"list":"outstanding","reward":123456789012345678.123456,'
                    '"created_at":"2026-01-01T00:00:00Z","deadline":"2026-01-02T00:00:00Z"}],'
                    '"summary":{"outstanding":1}}'
                )
            code, out, err = run_cli([in_path])
            self.assertEqual(code, 0)
            report = json.loads(out)
            self.assertEqual(report["findings"], [])


if __name__ == "__main__":
    unittest.main()

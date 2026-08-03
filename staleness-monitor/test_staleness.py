"""test_staleness.py -- stdlib-only unittest suite for staleness.py.

Run with:  python3 -m unittest test_staleness -v
"""

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

import staleness as sl

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "staleness.py")
FRESH_FIXTURE = os.path.join(HERE, "tasks_fresh.json")
STALE_FIXTURE = os.path.join(HERE, "tasks_stale.json")

NOW = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2030-01-01T00:00:00Z"


def run_cli(args, cwd=HERE):
    """Run the CLI as a subprocess exactly like a real user would."""
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def make_task(task_id="T-1", title="A task", status="proposed",
              created_at="2029-12-01T00:00:00Z", deadline=None):
    return {
        "task_id": task_id,
        "title": title,
        "status": status,
        "created_at": created_at,
        "deadline": deadline,
    }


# Small throwaway JSON fixture files used only by TestCLI, created once for
# the whole module (before any test class runs) and removed again at the
# end so the working tree stays clean.
_SIDE_FIXTURES = {
    "not_json.txt": "this is { not valid json",
    "object_not_array.json": json.dumps({"task_id": "x"}),
    "missing_key.json": json.dumps([{"task_id": "x", "title": "t", "status": "proposed"}]),
    "empty.json": "[]",
}
_SIDE_FIXTURE_PATHS = []


def setUpModule():
    for name, content in _SIDE_FIXTURES.items():
        path = os.path.join(HERE, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        _SIDE_FIXTURE_PATHS.append(path)


def tearDownModule():
    for path in _SIDE_FIXTURE_PATHS:
        if os.path.exists(path):
            os.remove(path)


# ==========================================================================
# parse_utc_timestamp
# ==========================================================================

class TestParseUtcTimestampValid(unittest.TestCase):
    pass


VALID_TIMESTAMP_CASES = {
    "uppercase_z": ("2026-08-02T00:00:00Z", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "lowercase_z": ("2026-08-02T00:00:00z", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "plus_zero_offset": ("2026-08-02T00:00:00+00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "minus_zero_offset": ("2026-08-02T00:00:00-00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "microseconds_with_z": ("2026-08-02T00:00:00.500000Z", datetime(2026, 8, 2, 0, 0, 0, 500000, tzinfo=timezone.utc)),
    "microseconds_with_offset": ("2026-08-02T00:00:00.123456+00:00", datetime(2026, 8, 2, 0, 0, 0, 123456, tzinfo=timezone.utc)),
    "leading_trailing_whitespace": ("  2026-08-02T00:00:00Z  ", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "end_of_year": ("2026-12-31T23:59:59Z", datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
    "leap_day": ("2028-02-29T12:00:00Z", datetime(2028, 2, 29, 12, 0, 0, tzinfo=timezone.utc)),
    "space_separator_with_offset": ("2026-08-02 00:00:00+00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
}


def _make_valid_test(raw, expected):
    def test(self):
        result = sl.parse_utc_timestamp(raw)
        self.assertEqual(result, expected)
        self.assertEqual(result.tzinfo, timezone.utc)
    return test


for _name, (_raw, _expected) in VALID_TIMESTAMP_CASES.items():
    setattr(TestParseUtcTimestampValid, f"test_valid_{_name}", _make_valid_test(_raw, _expected))


class TestParseUtcTimestampInvalid(unittest.TestCase):
    pass


INVALID_TIMESTAMP_CASES = {
    "positive_offset_0530": "2026-08-02T00:00:00+05:30",
    "negative_offset_0800": "2026-08-02T00:00:00-08:00",
    "timezone_naive": "2026-08-02T00:00:00",
    "empty_string": "",
    "whitespace_only": "   ",
    "garbage_text": "not-a-real-date",
    "bad_month": "2026-13-01T00:00:00Z",
    "bad_day": "2026-02-30T00:00:00Z",
    "bad_hour": "2026-08-02T25:00:00Z",
    "z_with_embedded_offset": "2026-08-02T00:00:00+00:00Z",
    "date_only_no_z": "2026-08-02",
    "none_value": None,
    "integer_value": 20260802,
    "float_value": 1735689600.0,
    "list_value": ["2026-08-02T00:00:00Z"],
    "dict_value": {"iso": "2026-08-02T00:00:00Z"},
    "boolean_value": True,
    "slash_date": "2026/08/02T00:00:00Z",
    "trailing_garbage_after_z": "2026-08-02T00:00:00Zgarbage",
}


def _make_invalid_test(raw):
    def test(self):
        with self.assertRaises(ValueError):
            sl.parse_utc_timestamp(raw)
    return test


for _name, _raw in INVALID_TIMESTAMP_CASES.items():
    setattr(TestParseUtcTimestampInvalid, f"test_invalid_{_name}", _make_invalid_test(_raw))


# ==========================================================================
# format_age
# ==========================================================================

class TestFormatAge(unittest.TestCase):
    pass


FORMAT_AGE_CASES = {
    "zero": (0, "0d 0h 0m"),
    "one_minute": (60, "0d 0h 1m"),
    "one_hour": (3600, "0d 1h 0m"),
    "one_day": (86400, "1d 0h 0m"),
    "example_from_spec": (262800, "3d 1h 0m"),
    "sub_minute_truncated": (59, "0d 0h 0m"),
    "sub_minute_truncated_2": (119, "0d 0h 1m"),
    "just_under_a_day": (86399, "0d 23h 59m"),
    "large_value": (10 * 86400 + 5 * 3600 + 30 * 60, "10d 5h 30m"),
    "negative_one_hour": (-3600, "-0d 1h 0m"),
    "negative_large": (-(10 * 86400 + 5 * 3600 + 30 * 60), "-10d 5h 30m"),
    "negative_small": (-59, "-0d 0h 0m"),
    "float_input_truncated": (3600.9, "0d 1h 0m"),
    "negative_float_input": (-3600.9, "-0d 1h 0m"),
    "exactly_60_days": (60 * 86400, "60d 0h 0m"),
}


def _make_format_age_test(seconds, expected):
    def test(self):
        self.assertEqual(sl.format_age(seconds), expected)
    return test


for _name, (_seconds, _expected) in FORMAT_AGE_CASES.items():
    setattr(TestFormatAge, f"test_{_name}", _make_format_age_test(_seconds, _expected))


# ==========================================================================
# bucket_for_overdue_proposed
# ==========================================================================

class TestBucketForOverdueProposed(unittest.TestCase):
    def test_just_above_zero_is_info(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(1), sl.BUCKET_INFO)

    def test_just_below_6h_is_info(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(6 * 3600 - 1), sl.BUCKET_INFO)

    def test_exactly_6h_is_warning(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(6 * 3600), sl.BUCKET_WARNING)

    def test_just_below_24h_is_warning(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(24 * 3600 - 1), sl.BUCKET_WARNING)

    def test_exactly_24h_is_critical(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(24 * 3600), sl.BUCKET_CRITICAL)

    def test_way_above_24h_is_critical(self):
        self.assertEqual(sl.bucket_for_overdue_proposed(30 * 86400), sl.BUCKET_CRITICAL)


# ==========================================================================
# bucket_for_stale
# ==========================================================================

class TestBucketForStale(unittest.TestCase):
    WINDOW = 48 * 3600.0  # seconds, matches default accepted-stale-hours

    def test_just_above_window_is_info(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW + 1, self.WINDOW), sl.BUCKET_INFO)

    def test_just_below_1_5x_window_is_info(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW * 1.5 - 1, self.WINDOW), sl.BUCKET_INFO)

    def test_exactly_1_5x_window_is_warning(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW * 1.5, self.WINDOW), sl.BUCKET_WARNING)

    def test_just_below_2x_window_is_warning(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW * 2 - 1, self.WINDOW), sl.BUCKET_WARNING)

    def test_exactly_2x_window_is_critical(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW * 2, self.WINDOW), sl.BUCKET_CRITICAL)

    def test_way_above_2x_window_is_critical(self):
        self.assertEqual(sl.bucket_for_stale(self.WINDOW * 10, self.WINDOW), sl.BUCKET_CRITICAL)

    def test_different_window_submitted_default(self):
        window = 72 * 3600.0
        self.assertEqual(sl.bucket_for_stale(window * 1.2, window), sl.BUCKET_INFO)
        self.assertEqual(sl.bucket_for_stale(window * 1.6, window), sl.BUCKET_WARNING)
        self.assertEqual(sl.bucket_for_stale(window * 2.5, window), sl.BUCKET_CRITICAL)

    def test_zero_window(self):
        # A zero-hour window means any positive age at all is >= window (critical territory).
        self.assertEqual(sl.bucket_for_stale(1, 0), sl.BUCKET_CRITICAL)


# ==========================================================================
# evaluate_task
# ==========================================================================

class TestEvaluateTaskOverdueProposed(unittest.TestCase):
    def test_no_deadline_no_finding(self):
        task = make_task(status="proposed", deadline=None)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_future_deadline_no_finding(self):
        task = make_task(status="proposed", deadline="2030-01-02T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_deadline_equal_now_no_finding(self):
        task = make_task(status="proposed", deadline=NOW_ISO)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_deadline_one_second_past_triggers_info(self):
        task = make_task(status="proposed", deadline="2029-12-31T23:59:59Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["code"], sl.CODE_OVERDUE_PROPOSED)
        self.assertEqual(f["bucket"], sl.BUCKET_INFO)
        self.assertEqual(f["age_seconds"], 1)
        self.assertEqual(f["age_human"], "0d 0h 0m")

    def test_deadline_two_days_past_triggers_critical(self):
        task = make_task(status="proposed", deadline="2029-12-30T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["code"], sl.CODE_OVERDUE_PROPOSED)
        self.assertEqual(f["bucket"], sl.BUCKET_CRITICAL)
        self.assertEqual(f["age_seconds"], 172800)
        self.assertEqual(f["age_human"], "2d 0h 0m")

    def test_accepted_status_with_past_deadline_not_overdue_proposed(self):
        # OVERDUE_PROPOSED only applies to status == proposed.
        task = make_task(status="accepted", created_at="2029-12-31T23:00:00Z",
                          deadline="2029-12-30T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_OVERDUE_PROPOSED, codes)

    def test_finding_includes_task_metadata(self):
        task = make_task(task_id="X-9", title="My Title", status="proposed",
                          deadline="2029-12-30T00:00:00Z")
        f = sl.evaluate_task(task, NOW, 48, 72)[0]
        self.assertEqual(f["task_id"], "X-9")
        self.assertEqual(f["title"], "My Title")
        self.assertEqual(f["status"], "proposed")


class TestEvaluateTaskStaleAccepted(unittest.TestCase):
    def test_fresh_no_finding(self):
        task = make_task(status="accepted", created_at="2029-12-31T23:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_exactly_at_boundary_no_finding(self):
        created = NOW - timedelta(hours=48)
        task = make_task(status="accepted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_one_second_past_boundary_triggers(self):
        created = NOW - timedelta(hours=48, seconds=1)
        task = make_task(status="accepted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], sl.CODE_STALE_ACCEPTED)
        self.assertEqual(findings[0]["age_seconds"], 48 * 3600 + 1)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_INFO)

    def test_critical_bucket_at_2x_window(self):
        created = NOW - timedelta(hours=96)
        task = make_task(status="accepted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_CRITICAL)

    def test_negative_age_future_created_at_no_finding(self):
        task = make_task(status="accepted", created_at="2030-01-05T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_custom_window_respected(self):
        created = NOW - timedelta(hours=10)
        task = make_task(status="accepted", created_at=created.isoformat().replace("+00:00", "Z"))
        self.assertEqual(sl.evaluate_task(task, NOW, 48, 72), [])
        findings = sl.evaluate_task(task, NOW, 5, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["accepted_stale_hours"], 5)

    def test_submitted_status_not_flagged_as_stale_accepted(self):
        created = NOW - timedelta(hours=1000)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_STALE_ACCEPTED, codes)


class TestEvaluateTaskStaleSubmitted(unittest.TestCase):
    def test_fresh_no_finding(self):
        task = make_task(status="submitted", created_at="2029-12-31T23:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_exactly_at_boundary_no_finding(self):
        created = NOW - timedelta(hours=72)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_one_second_past_boundary_triggers(self):
        created = NOW - timedelta(hours=72, seconds=1)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], sl.CODE_STALE_SUBMITTED)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_INFO)

    def test_critical_bucket_at_2x_window(self):
        created = NOW - timedelta(hours=144)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_CRITICAL)

    def test_warning_bucket_at_1_5x_window(self):
        created = NOW - timedelta(hours=108)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_WARNING)

    def test_custom_window_respected(self):
        created = NOW - timedelta(hours=200)
        task = make_task(status="submitted", created_at=created.isoformat().replace("+00:00", "Z"))
        findings = sl.evaluate_task(task, NOW, 48, 9999)
        self.assertEqual(findings, [])


class TestEvaluateTaskMalformedDeadline(unittest.TestCase):
    def test_garbage_string(self):
        task = make_task(deadline="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_CRITICAL)
        self.assertEqual(findings[0]["deadline_raw"], "garbage")

    def test_non_utc_offset(self):
        task = make_task(deadline="2030-01-02T00:00:00+05:30")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)

    def test_null_deadline_is_not_malformed(self):
        task = make_task(deadline=None)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_MALFORMED_DEADLINE, codes)

    def test_non_string_deadline_value(self):
        task = make_task(deadline=12345)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)

    def test_boolean_deadline_value(self):
        task = make_task(deadline=True)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)

    def test_malformed_deadline_does_not_crash_on_accepted_status(self):
        # created_at kept fresh (1h old) so STALE_ACCEPTED does not also fire,
        # isolating the MALFORMED_DEADLINE behavior.
        task = make_task(status="accepted", created_at="2029-12-31T23:00:00Z", deadline="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)


class TestEvaluateTaskMalformedCreatedAt(unittest.TestCase):
    def test_garbage_string(self):
        task = make_task(created_at="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_CREATED_AT)
        self.assertEqual(findings[0]["bucket"], sl.BUCKET_CRITICAL)
        self.assertEqual(findings[0]["created_at_raw"], "garbage")

    def test_non_utc_offset(self):
        task = make_task(created_at="2029-12-01T00:00:00+05:30")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_CREATED_AT)

    def test_null_created_at(self):
        task = make_task(created_at=None)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_CREATED_AT)

    def test_malformed_created_at_suppresses_stale_accepted(self):
        task = make_task(status="accepted", created_at="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_STALE_ACCEPTED, codes)

    def test_malformed_created_at_suppresses_stale_submitted(self):
        task = make_task(status="submitted", created_at="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_STALE_SUBMITTED, codes)

    def test_malformed_created_at_suppresses_deadline_before_created(self):
        task = make_task(created_at="garbage", deadline="2029-01-01T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)


class TestEvaluateTaskDeadlineBeforeCreated(unittest.TestCase):
    def test_basic_case(self):
        task = make_task(created_at="2029-12-10T00:00:00Z", deadline="2029-12-05T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)
        f = [f for f in findings if f["code"] == sl.CODE_DEADLINE_BEFORE_CREATED][0]
        self.assertEqual(f["bucket"], sl.BUCKET_CRITICAL)
        self.assertEqual(f["age_seconds"], 5 * 86400)

    def test_deadline_equal_created_no_finding(self):
        task = make_task(created_at="2029-12-10T00:00:00Z", deadline="2029-12-10T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)

    def test_deadline_after_created_no_finding(self):
        task = make_task(created_at="2029-12-10T00:00:00Z", deadline="2029-12-15T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)

    def test_null_deadline_no_finding(self):
        task = make_task(created_at="2029-12-10T00:00:00Z", deadline=None)
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertNotIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)

    def test_combines_with_overdue_proposed(self):
        # deadline is before created_at AND before now -> two findings on one task.
        task = make_task(status="proposed", created_at="2029-12-10T00:00:00Z",
                          deadline="2029-12-05T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = sorted(f["code"] for f in findings)
        self.assertEqual(codes, sorted([sl.CODE_DEADLINE_BEFORE_CREATED, sl.CODE_OVERDUE_PROPOSED]))

    def test_works_for_non_proposed_status_too(self):
        task = make_task(status="accepted", created_at="2029-12-10T00:00:00Z",
                          deadline="2029-12-05T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        codes = [f["code"] for f in findings]
        self.assertIn(sl.CODE_DEADLINE_BEFORE_CREATED, codes)


class TestEvaluateTaskUnknownStatus(unittest.TestCase):
    def test_unknown_status_with_past_deadline_no_overdue(self):
        task = make_task(status="blocked", created_at="2029-01-01T00:00:00Z",
                          deadline="2029-06-01T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_unknown_status_with_old_created_at_no_stale(self):
        task = make_task(status="archived", created_at="2020-01-01T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_unknown_status_empty_string(self):
        task = make_task(status="", created_at="2020-01-01T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])

    def test_unknown_status_still_flags_malformed_deadline(self):
        task = make_task(status="weird", deadline="garbage")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings[0]["code"], sl.CODE_MALFORMED_DEADLINE)

    def test_case_sensitive_status(self):
        # "Proposed" (capitalized) is not in the allowed set -> inert.
        # deadline is after created_at (no DEADLINE_BEFORE_CREATED) and in the
        # past relative to NOW, so a real "proposed" status would overdue here.
        task = make_task(status="Proposed", created_at="2029-12-01T00:00:00Z",
                          deadline="2029-12-15T00:00:00Z")
        findings = sl.evaluate_task(task, NOW, 48, 72)
        self.assertEqual(findings, [])


# ==========================================================================
# build_report
# ==========================================================================

class TestBuildReportShape(unittest.TestCase):
    def test_empty_list_zero_findings(self):
        report, total = sl.build_report([], NOW, 48, 72)
        self.assertEqual(total, 0)
        self.assertEqual(report["summary"]["total_tasks"], 0)
        self.assertEqual(report["findings"]["critical"], [])
        self.assertEqual(report["findings"]["warning"], [])
        self.assertEqual(report["findings"]["info"], [])

    def test_not_a_list_raises(self):
        with self.assertRaises(sl.InputError):
            sl.build_report({"not": "a list"}, NOW, 48, 72)

    def test_list_of_non_objects_raises(self):
        with self.assertRaises(sl.InputError):
            sl.build_report(["not-a-task"], NOW, 48, 72)

    def test_missing_task_id_raises(self):
        task = make_task()
        del task["task_id"]
        with self.assertRaises(sl.InputError):
            sl.build_report([task], NOW, 48, 72)

    def test_missing_status_raises(self):
        task = make_task()
        del task["status"]
        with self.assertRaises(sl.InputError):
            sl.build_report([task], NOW, 48, 72)

    def test_missing_created_at_raises(self):
        task = make_task()
        del task["created_at"]
        with self.assertRaises(sl.InputError):
            sl.build_report([task], NOW, 48, 72)

    def test_missing_deadline_key_raises(self):
        task = make_task()
        del task["deadline"]
        with self.assertRaises(sl.InputError):
            sl.build_report([task], NOW, 48, 72)

    def test_missing_title_raises(self):
        task = make_task()
        del task["title"]
        with self.assertRaises(sl.InputError):
            sl.build_report([task], NOW, 48, 72)

    def test_windows_reflected_in_report(self):
        report, _ = sl.build_report([], NOW, 12, 34)
        self.assertEqual(report["windows"]["accepted_stale_hours"], 12)
        self.assertEqual(report["windows"]["submitted_stale_hours"], 34)

    def test_generated_at_reflects_now(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        self.assertEqual(report["generated_at"], NOW_ISO)

    def test_summary_counts_match_buckets(self):
        tasks = [
            make_task(task_id="A", status="proposed", deadline="2029-12-01T00:00:00Z"),  # critical
            make_task(task_id="B", status="proposed", deadline="2029-12-31T22:00:00Z"),  # info
        ]
        report, total = sl.build_report(tasks, NOW, 48, 72)
        self.assertEqual(total, 2)
        s = report["summary"]
        self.assertEqual(s["total_findings"], 2)
        self.assertEqual(s["critical"] + s["warning"] + s["info"], 2)
        self.assertEqual(len(report["findings"]["critical"]), s["critical"])
        self.assertEqual(len(report["findings"]["warning"]), s["warning"])
        self.assertEqual(len(report["findings"]["info"]), s["info"])

    def test_findings_sorted_by_task_id_within_bucket(self):
        tasks = [
            make_task(task_id="Z", status="proposed", deadline="2029-12-01T00:00:00Z"),
            make_task(task_id="A", status="proposed", deadline="2029-12-02T00:00:00Z"),
            make_task(task_id="M", status="proposed", deadline="2029-12-03T00:00:00Z"),
        ]
        report, _ = sl.build_report(tasks, NOW, 48, 72)
        ids = [f["task_id"] for f in report["findings"]["critical"]]
        self.assertEqual(ids, sorted(ids))

    def test_findings_sorted_by_code_within_same_task_id(self):
        task = make_task(task_id="DUP", status="proposed",
                          created_at="2029-12-10T00:00:00Z", deadline="2029-12-05T00:00:00Z")
        report, _ = sl.build_report([task], NOW, 48, 72)
        codes = [f["code"] for f in report["findings"]["critical"]]
        self.assertEqual(codes, sorted(codes))

    def test_order_is_deterministic_across_calls(self):
        tasks = [
            make_task(task_id="B", status="proposed", deadline="2029-12-01T00:00:00Z"),
            make_task(task_id="A", status="accepted", created_at="2020-01-01T00:00:00Z"),
        ]
        report1, _ = sl.build_report(tasks, NOW, 48, 72)
        report2, _ = sl.build_report(list(tasks), NOW, 48, 72)
        self.assertEqual(report1, report2)

    def test_multiple_tasks_all_contribute(self):
        # deadline is after the default created_at but still before NOW, so
        # each task contributes exactly one OVERDUE_PROPOSED finding (and
        # NOT a DEADLINE_BEFORE_CREATED finding too).
        tasks = [
            make_task(task_id=f"T-{i}", status="proposed", deadline="2029-12-15T00:00:00Z")
            for i in range(5)
        ]
        report, total = sl.build_report(tasks, NOW, 48, 72)
        self.assertEqual(total, 5)
        self.assertEqual(report["summary"]["total_tasks"], 5)


# ==========================================================================
# canonical_json
# ==========================================================================

class TestCanonicalJson(unittest.TestCase):
    def test_ends_with_single_newline(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        out = sl.canonical_json(report)
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_no_extra_whitespace_separators(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        out = sl.canonical_json(report).rstrip("\n")
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_keys_are_sorted(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        out = sl.canonical_json(report)
        obj = json.loads(out)
        raw_no_newline = out[:-1]
        reserialized = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(raw_no_newline, reserialized)

    def test_ascii_only_with_unicode_title(self):
        task = make_task(title="café ☃", status="proposed", deadline="2029-12-15T00:00:00Z")
        report, _ = sl.build_report([task], NOW, 48, 72)
        out = sl.canonical_json(report)
        out.encode("ascii")  # must not raise
        self.assertIn("\\u00e9", out)

    def test_round_trips_through_json_loads(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        out = sl.canonical_json(report)
        self.assertEqual(json.loads(out), report)

    def test_deterministic_for_same_input(self):
        report, _ = sl.build_report([], NOW, 48, 72)
        out1 = sl.canonical_json(report)
        out2 = sl.canonical_json(report)
        self.assertEqual(out1, out2)


# ==========================================================================
# CLI (subprocess, exercising the real entry point)
# ==========================================================================

class TestCLI(unittest.TestCase):
    def test_fresh_fixture_exit_0(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self.assertEqual(obj["summary"]["total_findings"], 0)

    def test_stale_fixture_exit_1(self):
        proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        obj = json.loads(proc.stdout)
        self.assertGreater(obj["summary"]["total_findings"], 0)

    def test_missing_now_exit_2(self):
        proc = run_cli(["tasks_fresh.json"])
        self.assertEqual(proc.returncode, 2)

    def test_unparseable_now_exit_2(self):
        proc = run_cli(["tasks_fresh.json", "--now", "not-a-date"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--now", proc.stderr)

    def test_non_utc_now_exit_2(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00+05:30"])
        self.assertEqual(proc.returncode, 2)

    def test_nonexistent_input_file_exit_2(self):
        proc = run_cli(["/nonexistent/does/not/exist.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 2)

    def test_bad_json_file_exit_2(self):
        proc = run_cli(["not_json.txt", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 2)

    def test_json_object_instead_of_array_exit_2(self):
        proc = run_cli(["object_not_array.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 2)

    def test_missing_required_key_exit_2(self):
        proc = run_cli(["missing_key.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 2)

    def test_empty_array_exit_0(self):
        proc = run_cli(["empty.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self.assertEqual(obj["summary"]["total_tasks"], 0)

    def test_negative_accepted_window_exit_2(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z",
                         "--accepted-stale-hours", "-1"])
        self.assertEqual(proc.returncode, 2)

    def test_negative_submitted_window_exit_2(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z",
                         "--submitted-stale-hours", "-1"])
        self.assertEqual(proc.returncode, 2)

    def test_output_flag_writes_file(self):
        out_path = os.path.join(HERE, "_test_output_tmp.json")
        try:
            proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z", "-o", out_path])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")
            with open(out_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            self.assertTrue(content.endswith("\n"))
            json.loads(content)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_long_output_flag_form(self):
        out_path = os.path.join(HERE, "_test_output_tmp2.json")
        try:
            proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z", "--output", out_path])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(out_path))
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_reproducible_byte_identical_across_runs(self):
        out1 = os.path.join(HERE, "_repro1.json")
        out2 = os.path.join(HERE, "_repro2.json")
        try:
            p1 = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z", "-o", out1])
            p2 = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z", "-o", out2])
            self.assertEqual(p1.returncode, 1)
            self.assertEqual(p2.returncode, 1)
            with open(out1, "rb") as f1, open(out2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())
        finally:
            for p in (out1, out2):
                if os.path.exists(p):
                    os.remove(p)

    def test_custom_windows_reduce_finding_count(self):
        default_proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z"])
        wide_proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z",
                              "--accepted-stale-hours", "9999", "--submitted-stale-hours", "9999"])
        default_obj = json.loads(default_proc.stdout)
        wide_obj = json.loads(wide_proc.stdout)
        self.assertLess(wide_obj["summary"]["total_findings"], default_obj["summary"]["total_findings"])
        self.assertEqual(wide_proc.returncode, 1)

    def test_custom_windows_reflected_in_output(self):
        proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z",
                         "--accepted-stale-hours", "10", "--submitted-stale-hours", "20"])
        obj = json.loads(proc.stdout)
        self.assertEqual(obj["windows"]["accepted_stale_hours"], 10.0)
        self.assertEqual(obj["windows"]["submitted_stale_hours"], 20.0)

    def test_default_windows_in_output(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z"])
        obj = json.loads(proc.stdout)
        self.assertEqual(obj["windows"]["accepted_stale_hours"], 48)
        self.assertEqual(obj["windows"]["submitted_stale_hours"], 72)

    def test_stdout_has_no_extra_prints(self):
        proc = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z"])
        # stdout must be exactly one JSON document + newline, nothing else.
        self.assertEqual(proc.stdout.count("\n"), 1)

    def test_help_exits_zero(self):
        proc = run_cli(["--help"])
        self.assertEqual(proc.returncode, 0)

    def test_stale_fixture_findings_all_codes_present(self):
        proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z"])
        obj = json.loads(proc.stdout)
        all_findings = obj["findings"]["critical"] + obj["findings"]["warning"] + obj["findings"]["info"]
        codes = {f["code"] for f in all_findings}
        self.assertEqual(codes, set(sl.ALL_CODES))

    def test_stale_fixture_all_buckets_present(self):
        proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z"])
        obj = json.loads(proc.stdout)
        self.assertGreater(len(obj["findings"]["critical"]), 0)
        self.assertGreater(len(obj["findings"]["warning"]), 0)
        self.assertGreater(len(obj["findings"]["info"]), 0)

    def test_unknown_status_task_does_not_crash(self):
        # tasks_stale.json includes S-15 with status "cancelled" -- ensure the
        # whole run still succeeds rather than raising.
        proc = run_cli(["tasks_stale.json", "--now", "2026-08-02T00:00:00Z"])
        self.assertIn(proc.returncode, (0, 1))

    def test_relative_and_absolute_paths_both_work(self):
        proc_rel = run_cli(["tasks_fresh.json", "--now", "2026-08-02T00:00:00Z"])
        proc_abs = run_cli([FRESH_FIXTURE, "--now", "2026-08-02T00:00:00Z"])
        self.assertEqual(proc_rel.stdout, proc_abs.stdout)


# ==========================================================================
# Fixture integrity (tasks_fresh.json / tasks_stale.json)
# ==========================================================================

class TestFixtureIntegrity(unittest.TestCase):
    def test_fresh_fixture_is_valid_json_array(self):
        with open(FRESH_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_stale_fixture_is_valid_json_array(self):
        with open(STALE_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_fresh_fixture_produces_zero_findings_via_library(self):
        with open(FRESH_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        report, total = sl.build_report(data, sl.parse_utc_timestamp("2026-08-02T00:00:00Z"), 48, 72)
        self.assertEqual(total, 0)

    def test_stale_fixture_triggers_every_finding_code(self):
        with open(STALE_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        report, total = sl.build_report(data, sl.parse_utc_timestamp("2026-08-02T00:00:00Z"), 48, 72)
        all_findings = report["findings"]["critical"] + report["findings"]["warning"] + report["findings"]["info"]
        codes = {f["code"] for f in all_findings}
        self.assertEqual(codes, set(sl.ALL_CODES))

    def test_stale_fixture_triggers_every_bucket(self):
        with open(STALE_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        report, total = sl.build_report(data, sl.parse_utc_timestamp("2026-08-02T00:00:00Z"), 48, 72)
        for bucket in sl.ALL_BUCKETS:
            self.assertGreater(len(report["findings"][bucket]), 0, bucket)

    def test_stale_fixture_every_task_id_unique(self):
        with open(STALE_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        ids = [t["task_id"] for t in data]
        self.assertEqual(len(ids), len(set(ids)))

    def test_fresh_fixture_every_task_id_unique(self):
        with open(FRESH_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        ids = [t["task_id"] for t in data]
        self.assertEqual(len(ids), len(set(ids)))


# ==========================================================================
# No wall-clock reads in the report path
# ==========================================================================

class TestNoWallClockRead(unittest.TestCase):
    def test_source_has_no_datetime_now(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        # Strip the module docstring / comments' prose mentions by only
        # scanning for the actual call forms; these must not appear anywhere.
        self.assertNotIn("datetime.now(", src)
        self.assertNotIn(".utcnow(", src)
        self.assertNotIn("time.time(", src)

    def test_now_is_always_a_function_parameter(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def evaluate_task(task, now,", src)
        self.assertIn("def build_report(tasks, now,", src)

    def test_main_only_source_of_now_is_the_now_argument(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("now = parse_utc_timestamp(args.now)", src)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for link_integrity.py.

Run with:
    python3 -m unittest test_link_integrity -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import link_integrity as li

CLI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "link_integrity.py")


def run_cli(args, cwd=None):
    result = subprocess.run(
        [sys.executable, CLI_PATH] + args,
        capture_output=True, text=True, cwd=cwd,
    )
    return result.returncode, result.stdout, result.stderr


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def lc(task_id, state, at):
    return {"task_id": task_id, "state": state, "at": at}


def ev(submission_id, task_id, submitted_at, evidence_type="doc", value="v"):
    return {
        "submission_id": submission_id,
        "task_id": task_id,
        "evidence_type": evidence_type,
        "value": value,
        "submitted_at": submitted_at,
    }


def codes_of(violations):
    return sorted(v["code"] for v in violations)


class TimestampParsingTests(unittest.TestCase):
    def test_z_suffix_valid(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00Z")
        self.assertIsNone(err)
        self.assertIsNotNone(dt)

    def test_plus_zero_offset_valid(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00+00:00")
        self.assertIsNone(err)
        self.assertIsNotNone(dt)

    def test_z_and_plus_zero_equal_instant(self):
        dt1, _ = li.parse_utc_timestamp("2026-01-01T00:00:00Z")
        dt2, _ = li.parse_utc_timestamp("2026-01-01T00:00:00+00:00")
        self.assertEqual(dt1, dt2)

    def test_non_utc_offset_positive_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00+02:00")
        self.assertIsNone(dt)
        self.assertIsNotNone(err)

    def test_non_utc_offset_negative_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00-05:00")
        self.assertIsNone(dt)
        self.assertIsNotNone(err)

    def test_lowercase_z_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00z")
        self.assertIsNone(dt)

    def test_garbage_string_rejected(self):
        dt, err = li.parse_utc_timestamp("not-a-timestamp")
        self.assertIsNone(dt)
        self.assertIsNotNone(err)

    def test_empty_string_rejected(self):
        dt, err = li.parse_utc_timestamp("")
        self.assertIsNone(dt)

    def test_none_rejected(self):
        dt, err = li.parse_utc_timestamp(None)
        self.assertIsNone(dt)
        self.assertIn("not a string", err)

    def test_integer_rejected(self):
        dt, err = li.parse_utc_timestamp(1234567890)
        self.assertIsNone(dt)

    def test_missing_time_component_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01")
        self.assertIsNone(dt)

    def test_year_below_2000_rejected(self):
        dt, err = li.parse_utc_timestamp("1999-12-31T23:59:59Z")
        self.assertIsNone(dt)
        self.assertIn("year", err)

    def test_year_exactly_2000_accepted(self):
        dt, err = li.parse_utc_timestamp("2000-01-01T00:00:00Z")
        self.assertIsNotNone(dt)
        self.assertIsNone(err)

    def test_year_exactly_2100_accepted(self):
        dt, err = li.parse_utc_timestamp("2100-12-31T23:59:59Z")
        self.assertIsNotNone(dt)
        self.assertIsNone(err)

    def test_year_2101_rejected(self):
        dt, err = li.parse_utc_timestamp("2101-01-01T00:00:00Z")
        self.assertIsNone(dt)

    def test_year_9999_rejected(self):
        dt, err = li.parse_utc_timestamp("9999-01-01T00:00:00Z")
        self.assertIsNone(dt)

    def test_leap_second_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-06-30T23:59:60Z")
        self.assertIsNone(dt)

    def test_invalid_month_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-13-01T00:00:00Z")
        self.assertIsNone(dt)

    def test_invalid_day_feb30_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-02-30T00:00:00Z")
        self.assertIsNone(dt)

    def test_invalid_day_feb29_non_leap_rejected(self):
        dt, err = li.parse_utc_timestamp("2025-02-29T00:00:00Z")
        self.assertIsNone(dt)

    def test_valid_day_feb29_leap_year_accepted(self):
        dt, err = li.parse_utc_timestamp("2024-02-29T00:00:00Z")
        self.assertIsNotNone(dt)

    def test_hour_24_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T24:00:00Z")
        self.assertIsNone(dt)

    def test_fractional_seconds_accepted(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00.123456Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.microsecond, 123456)

    def test_fractional_seconds_short_padded(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00.5Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.microsecond, 500000)

    def test_missing_seconds_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00Z")
        self.assertIsNone(dt)

    def test_space_instead_of_t_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01 00:00:00Z")
        self.assertIsNone(dt)

    def test_trailing_garbage_rejected(self):
        dt, err = li.parse_utc_timestamp("2026-01-01T00:00:00Zxyz")
        self.assertIsNone(dt)

    def test_leading_garbage_rejected(self):
        dt, err = li.parse_utc_timestamp("xyz2026-01-01T00:00:00Z")
        self.assertIsNone(dt)


class StructuralValidationLifecycleTests(unittest.TestCase):
    def test_valid_minimal_record(self):
        out = li.validate_lifecycle_records([lc("t1", "created", "2026-01-01T00:00:00Z")])
        self.assertEqual(len(out), 1)

    def test_top_level_not_list_raises_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            lf = os.path.join(d, "lifecycle.json")
            ef = os.path.join(d, "evidence.json")
            write_json(lf, {"not": "a list"})
            write_json(ef, [])
            code, out, err = run_cli([lf, ef])
            self.assertEqual(code, 2)

    def test_record_not_dict(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records(["not-a-dict"])

    def test_missing_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([{"state": "created", "at": "2026-01-01T00:00:00Z"}])

    def test_missing_state(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([{"task_id": "t1", "at": "2026-01-01T00:00:00Z"}])

    def test_missing_at(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([{"task_id": "t1", "state": "created"}])

    def test_null_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc(None, "created", "2026-01-01T00:00:00Z")])

    def test_empty_string_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc("", "created", "2026-01-01T00:00:00Z")])

    def test_numeric_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc(123, "created", "2026-01-01T00:00:00Z")])

    def test_null_state(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc("t1", None, "2026-01-01T00:00:00Z")])

    def test_empty_state(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc("t1", "", "2026-01-01T00:00:00Z")])

    def test_at_not_string(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc("t1", "created", 12345)])

    def test_at_null(self):
        with self.assertRaises(li.InputError):
            li.validate_lifecycle_records([lc("t1", "created", None)])

    def test_at_bad_string_content_does_not_raise(self):
        # Unparseable content is allowed through structurally -> becomes
        # an IMPOSSIBLE_TIMESTAMP violation, not an InputError.
        out = li.validate_lifecycle_records([lc("t1", "created", "garbage")])
        self.assertEqual(out[0]["at"], "garbage")

    def test_index_recorded(self):
        out = li.validate_lifecycle_records([
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t2", "created", "2026-01-02T00:00:00Z"),
        ])
        self.assertEqual(out[0]["index"], 0)
        self.assertEqual(out[1]["index"], 1)

    def test_empty_list_ok(self):
        out = li.validate_lifecycle_records([])
        self.assertEqual(out, [])


class StructuralValidationEvidenceTests(unittest.TestCase):
    def test_valid_minimal_record(self):
        out = li.validate_evidence_records([ev("s1", "t1", "2026-01-01T00:00:00Z")])
        self.assertEqual(len(out), 1)

    def test_record_not_dict(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([42])

    def test_missing_submission_id(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([{"task_id": "t1", "submitted_at": "2026-01-01T00:00:00Z"}])

    def test_missing_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([{"submission_id": "s1", "submitted_at": "2026-01-01T00:00:00Z"}])

    def test_missing_submitted_at(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([{"submission_id": "s1", "task_id": "t1"}])

    def test_null_submission_id(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([ev(None, "t1", "2026-01-01T00:00:00Z")])

    def test_null_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([ev("s1", None, "2026-01-01T00:00:00Z")])

    def test_empty_task_id(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([ev("s1", "", "2026-01-01T00:00:00Z")])

    def test_submitted_at_not_string(self):
        with self.assertRaises(li.InputError):
            li.validate_evidence_records([ev("s1", "t1", 42)])

    def test_evidence_type_and_value_not_validated(self):
        # Out of scope: these fields are not type-checked at all.
        rec = {
            "submission_id": "s1", "task_id": "t1",
            "evidence_type": 12345, "value": None,
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        out = li.validate_evidence_records([rec])
        self.assertEqual(len(out), 1)

    def test_empty_list_ok(self):
        out = li.validate_evidence_records([])
        self.assertEqual(out, [])


class UnknownTaskReferenceTests(unittest.TestCase):
    def test_flags_unknown_task(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "ghost-task", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["UNKNOWN_TASK_REFERENCE"])
        self.assertEqual(v[0]["task_id"], "ghost-task")
        self.assertEqual(v[0]["submission_id"], "s1")

    def test_known_task_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_empty_lifecycle_flags_all_evidence(self):
        evidence = [ev("s1", "t1", "2026-01-01T01:00:00Z"), ev("s2", "t2", "2026-01-01T01:00:00Z")]
        v = li.check_links([], li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["UNKNOWN_TASK_REFERENCE", "UNKNOWN_TASK_REFERENCE"])

    def test_unknown_task_evidence_not_checked_for_time_violations(self):
        # Evidence for an unknown task should only produce
        # UNKNOWN_TASK_REFERENCE, not also BEFORE/AFTER checks (there is no
        # task info to compare against).
        evidence = [ev("s1", "ghost", "2026-01-01T00:00:00Z")]
        v = li.check_links([], li.validate_evidence_records(evidence))
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["code"], "UNKNOWN_TASK_REFERENCE")


class DuplicateSubmissionIdTests(unittest.TestCase):
    def test_duplicate_flagged_once_per_group(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [
            ev("dup", "t1", "2026-01-01T01:00:00Z"),
            ev("dup", "t1", "2026-01-01T02:00:00Z"),
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["DUPLICATE_SUBMISSION_ID"])
        self.assertEqual(v[0]["count"], 2)

    def test_triplicate_counted_correctly(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("dup", "t1", "2026-01-01T01:00:00Z") for _ in range(3)]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["count"], 3)

    def test_unique_submission_ids_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:00:00Z"), ev("s2", "t1", "2026-01-01T02:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_duplicate_across_two_tasks_lists_both_task_ids(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t2", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("dup", "t1", "2026-01-01T01:00:00Z"), ev("dup", "t2", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["task_ids"], ["t1", "t2"])

    def test_multiple_duplicate_groups(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [
            ev("dupA", "t1", "2026-01-01T01:00:00Z"),
            ev("dupA", "t1", "2026-01-01T02:00:00Z"),
            ev("dupB", "t1", "2026-01-01T03:00:00Z"),
            ev("dupB", "t1", "2026-01-01T04:00:00Z"),
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["DUPLICATE_SUBMISSION_ID", "DUPLICATE_SUBMISSION_ID"])

    def test_empty_evidence_no_duplicates(self):
        v = li.check_links(li.validate_lifecycle_records([lc("t1", "created", "2026-01-01T00:00:00Z")]), [])
        self.assertEqual(v, [])


class EvidenceBeforeTaskCreatedTests(unittest.TestCase):
    def test_before_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T12:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T11:59:59Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["EVIDENCE_BEFORE_TASK_CREATED"])

    def test_exact_same_instant_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T12:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T12:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_exact_same_instant_z_vs_plus_zero_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T12:00:00+00:00")]
        evidence = [ev("s1", "t1", "2026-01-01T12:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_after_creation_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T12:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T12:00:01Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_uses_earliest_lifecycle_event_not_first_in_list(self):
        # 'created' event appears second in the list but is chronologically first.
        lifecycle = [
            lc("t1", "submitted", "2026-01-02T00:00:00Z"),
            lc("t1", "created", "2026-01-01T00:00:00Z"),
        ]
        evidence = [ev("s1", "t1", "2026-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        # equals earliest (created), not before -> no violation despite being
        # before the 'submitted' event that appears first in raw order.
        self.assertEqual(v, [])

    def test_impossible_task_timestamp_skips_comparison(self):
        # If all of a task's lifecycle timestamps are impossible, we cannot
        # know when it was created, so no BEFORE check is performed.
        lifecycle = [lc("t1", "created", "garbage")]
        evidence = [ev("s1", "t1", "2026-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])

    def test_impossible_evidence_timestamp_skips_comparison(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "garbage")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])


class EvidenceAfterTerminalStateTests(unittest.TestCase):
    def test_after_rewarded_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "rewarded", "2026-01-01T01:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:00:01Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["EVIDENCE_AFTER_TERMINAL_STATE"])
        self.assertEqual(v[0]["terminal_state"], "rewarded")

    def test_after_refused_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "refused", "2026-01-01T01:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:00:01Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["EVIDENCE_AFTER_TERMINAL_STATE"])
        self.assertEqual(v[0]["terminal_state"], "refused")

    def test_exact_same_instant_as_terminal_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "rewarded", "2026-01-01T01:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_before_terminal_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "rewarded", "2026-01-01T01:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T00:30:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_no_terminal_state_never_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "2030-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_earliest_terminal_occurrence_used_when_multiple(self):
        # Two terminal-ish events (unusual, but we don't validate transition
        # order); the earliest terminal instant should govern.
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t1", "rewarded", "2026-01-01T02:00:00Z"),
            lc("t1", "refused", "2026-01-01T01:00:00Z"),
        ]
        evidence = [ev("s1", "t1", "2026-01-01T01:30:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["EVIDENCE_AFTER_TERMINAL_STATE"])
        self.assertEqual(v[0]["terminal_state"], "refused")
        self.assertEqual(v[0]["terminal_at"], "2026-01-01T01:00:00Z")

    def test_reward_then_later_anomalous_submitted_event_still_flags_evidence_after_first_terminal(self):
        # Edge case from spec: task reaches 'rewarded' then gets a LATER
        # 'submitted' event. We do not judge the lifecycle ordering itself
        # (out of scope), but evidence submitted after the *first* terminal
        # occurrence is still flagged, even though a later 'submitted' event
        # exists in the raw lifecycle stream.
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t1", "rewarded", "2026-01-01T01:00:00Z"),
            lc("t1", "submitted", "2026-01-01T02:00:00Z"),
        ]
        evidence = [ev("s1", "t1", "2026-01-01T01:30:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["EVIDENCE_AFTER_TERMINAL_STATE"])

    def test_reward_then_later_submitted_event_missing_evidence_still_flagged(self):
        # Same anomalous ordering, but with zero evidence at all: the task
        # does have a 'submitted' event (however oddly placed), so
        # MISSING_EVIDENCE_FOR_SUBMITTED_STATE must still fire.
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t1", "rewarded", "2026-01-01T01:00:00Z"),
            lc("t1", "submitted", "2026-01-01T02:00:00Z"),
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(codes_of(v), ["MISSING_EVIDENCE_FOR_SUBMITTED_STATE"])

    def test_impossible_terminal_timestamp_skips_after_check(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "rewarded", "garbage")]
        evidence = [ev("s1", "t1", "2030-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])


class MissingEvidenceForSubmittedStateTests(unittest.TestCase):
    def test_submitted_no_evidence_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "submitted", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(codes_of(v), ["MISSING_EVIDENCE_FOR_SUBMITTED_STATE"])
        self.assertEqual(v[0]["task_id"], "t1")

    def test_submitted_with_evidence_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "submitted", "2026-01-01T01:00:00Z")]
        evidence = [ev("s1", "t1", "2026-01-01T01:30:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_submitted_with_unrelated_task_evidence_still_flagged(self):
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "submitted", "2026-01-01T01:00:00Z"),
            lc("t2", "created", "2026-01-01T00:00:00Z"),
        ]
        evidence = [ev("s1", "t2", "2026-01-01T00:30:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["MISSING_EVIDENCE_FOR_SUBMITTED_STATE"])
        self.assertEqual(v[0]["task_id"], "t1")

    def test_no_submitted_state_no_evidence_not_flagged_orphan(self):
        # ORPHAN_LIFECYCLE_TASK is explicitly not a violation.
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(v, [])

    def test_orphan_with_only_rewarded_state_not_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t1", "rewarded", "2026-01-01T01:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(v, [])

    def test_submitted_state_at_uses_first_occurrence_by_index_order(self):
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t1", "submitted", "2026-01-01T05:00:00Z"),
            lc("t1", "submitted", "2026-01-01T01:00:00Z"),
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["submitted_state_at"], "2026-01-01T05:00:00Z")

    def test_multiple_tasks_each_missing_evidence(self):
        lifecycle = [
            lc("t1", "submitted", "2026-01-01T00:00:00Z"),
            lc("t2", "submitted", "2026-01-01T00:00:00Z"),
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(codes_of(v), ["MISSING_EVIDENCE_FOR_SUBMITTED_STATE", "MISSING_EVIDENCE_FOR_SUBMITTED_STATE"])


class ImpossibleTimestampTests(unittest.TestCase):
    def test_lifecycle_bad_timestamp_flagged(self):
        lifecycle = [lc("t1", "created", "garbage")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])
        self.assertEqual(v[0]["source"], "lifecycle")
        self.assertIsNone(v[0]["submission_id"])

    def test_evidence_bad_timestamp_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "garbage")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])
        self.assertEqual(v[0]["source"], "evidence")
        self.assertEqual(v[0]["submission_id"], "s1")

    def test_non_utc_offset_lifecycle_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00+03:00")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])

    def test_year_out_of_range_evidence_flagged(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence = [ev("s1", "t1", "1899-01-01T00:00:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(codes_of(v), ["IMPOSSIBLE_TIMESTAMP"])

    def test_multiple_bad_timestamps_all_flagged(self):
        lifecycle = [lc("t1", "created", "garbage1"), lc("t2", "created", "garbage2")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), [])
        self.assertEqual(len(v), 2)

    def test_both_exports_empty_no_violations(self):
        v = li.check_links([], [])
        self.assertEqual(v, [])


class CanonicalOutputAndReportTests(unittest.TestCase):
    def test_build_report_clean(self):
        report = li.build_report(
            [lc("t1", "created", "2026-01-01T00:00:00Z")],
            [ev("s1", "t1", "2026-01-01T00:00:01Z")],
        )
        self.assertTrue(report["summary"]["is_clean"])
        self.assertEqual(report["summary"]["violation_count"], 0)
        self.assertEqual(report["violations"], [])

    def test_build_report_dirty(self):
        report = li.build_report([], [ev("s1", "ghost", "2026-01-01T00:00:00Z")])
        self.assertFalse(report["summary"]["is_clean"])
        self.assertEqual(report["summary"]["violation_count"], 1)
        self.assertEqual(report["summary"]["counts_by_code"]["UNKNOWN_TASK_REFERENCE"], 1)

    def test_canonical_json_sorted_keys(self):
        obj = {"b": 1, "a": 2}
        text = li.to_canonical_json(obj)
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_canonical_json_compact_separators(self):
        obj = {"a": [1, 2, 3]}
        text = li.to_canonical_json(obj)
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_canonical_json_ensure_ascii(self):
        obj = {"a": "é"}
        text = li.to_canonical_json(obj)
        self.assertIn("\\u00e9", text)

    def test_canonical_json_single_trailing_newline(self):
        text = li.to_canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_violation_list_sorted_deterministically_regardless_of_input_order(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        evidence_a = [ev("dup", "t1", "2026-01-01T01:00:00Z"), ev("dup", "t1", "2026-01-01T02:00:00Z")]
        evidence_b = list(reversed(evidence_a))
        ra = li.build_report(lifecycle, evidence_a)
        rb = li.build_report(lifecycle, evidence_b)
        self.assertEqual(li.to_canonical_json(ra), li.to_canonical_json(rb))

    def test_report_has_schema_version(self):
        report = li.build_report([], [])
        self.assertEqual(report["schema_version"], "1.0")


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _path(self, name):
        return os.path.join(self.tmpdir.name, name)

    def test_clean_exit_code_0(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [lc("t1", "created", "2026-01-01T00:00:00Z")])
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["summary"]["is_clean"])

    def test_dirty_exit_code_1(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [])
        write_json(ef, [ev("s1", "ghost", "2026-01-01T00:00:00Z")])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 1)

    def test_missing_lifecycle_file_exit_code_2(self):
        ef = self._path("e.json")
        write_json(ef, [])
        code, out, err = run_cli([self._path("nope.json"), ef])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_missing_evidence_file_exit_code_2(self):
        lf = self._path("l.json")
        write_json(lf, [])
        code, out, err = run_cli([lf, self._path("nope.json")])
        self.assertEqual(code, 2)

    def test_missing_positional_arg_exit_code_2(self):
        lf = self._path("l.json")
        write_json(lf, [])
        code, out, err = run_cli([lf])
        self.assertEqual(code, 2)

    def test_no_args_exit_code_2(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_invalid_json_exit_code_2(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        with open(lf, "w") as fh:
            fh.write("{not valid json")
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 2)

    def test_top_level_object_instead_of_array_exit_code_2(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, {"task_id": "t1"})
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 2)

    def test_null_task_id_in_input_exit_code_2(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [{"task_id": None, "state": "created", "at": "2026-01-01T00:00:00Z"}])
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 2)

    def test_output_flag_writes_file(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        out_path = self._path("report.json")
        write_json(lf, [])
        write_json(ef, [])
        code, out, err = run_cli([lf, ef, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(os.path.exists(out_path))
        with open(out_path) as fh:
            content = fh.read()
        self.assertTrue(content.endswith("\n"))
        json.loads(content)

    def test_long_output_flag(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        out_path = self._path("report2.json")
        write_json(lf, [])
        write_json(ef, [])
        code, out, err = run_cli([lf, ef, "--output", out_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))

    def test_output_to_unwritable_dir_exit_code_2(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [])
        write_json(ef, [])
        bad_out = self._path("nonexistent_dir/report.json")
        code, out, err = run_cli([lf, ef, "-o", bad_out])
        self.assertEqual(code, 2)

    def test_stdout_output_matches_file_output(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        out_path = self._path("report.json")
        write_json(lf, [lc("t1", "created", "2026-01-01T00:00:00Z")])
        write_json(ef, [ev("s1", "t1", "2026-01-01T00:00:01Z")])
        code1, out1, _ = run_cli([lf, ef])
        code2, out2, _ = run_cli([lf, ef, "-o", out_path])
        with open(out_path) as fh:
            file_content = fh.read()
        self.assertEqual(out1, file_content)

    def test_repeated_runs_byte_identical(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t2", "created", "2026-01-02T00:00:00Z"),
        ])
        write_json(ef, [
            ev("dup", "t1", "2026-01-01T01:00:00Z"),
            ev("dup", "t1", "2026-01-01T02:00:00Z"),
            ev("s3", "ghost", "2026-01-01T01:00:00Z"),
        ])
        code1, out1, _ = run_cli([lf, ef])
        code2, out2, _ = run_cli([lf, ef])
        self.assertEqual(code1, code2)
        self.assertEqual(out1, out2)

    def test_empty_both_exports_exit_0(self):
        lf, ef = self._path("l.json"), self._path("e.json")
        write_json(lf, [])
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["violations"], [])

    def test_help_flag_exits_zero(self):
        code, out, err = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())

    def test_utf8_bom_prefixed_file_is_readable(self):
        # Regression test: files exported with a leading UTF-8 BOM (common
        # from Windows-originated tooling: Notepad, PowerShell Out-File,
        # Excel exports) must still be treated as valid JSON. Bug found and
        # fixed during development: files were opened as plain 'utf-8',
        # which raised a spurious JSONDecodeError on a BOM-prefixed file
        # even though the JSON content itself was perfectly valid.
        lf = self._path("bom_l.json")
        ef = self._path("bom_e.json")
        data = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        with open(lf, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))
        write_json(ef, [])
        code, out, err = run_cli([lf, ef])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["summary"]["is_clean"])


class FixtureFileTests(unittest.TestCase):
    """Exercises the actual shipped fixture files used for verification."""

    BASE = os.path.dirname(os.path.abspath(__file__))

    def _p(self, name):
        return os.path.join(self.BASE, name)

    def test_ok_fixtures_exist(self):
        self.assertTrue(os.path.exists(self._p("lifecycle_ok.json")))
        self.assertTrue(os.path.exists(self._p("evidence_ok.json")))

    def test_bad_fixtures_exist(self):
        self.assertTrue(os.path.exists(self._p("lifecycle_bad.json")))
        self.assertTrue(os.path.exists(self._p("evidence_bad.json")))

    def test_ok_fixtures_are_valid_json_arrays(self):
        with open(self._p("lifecycle_ok.json")) as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)
        with open(self._p("evidence_ok.json")) as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)

    def test_ok_fixtures_produce_exit_0(self):
        code, out, err = run_cli([self._p("lifecycle_ok.json"), self._p("evidence_ok.json")])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["summary"]["is_clean"])

    def test_bad_fixtures_produce_exit_1(self):
        code, out, err = run_cli([self._p("lifecycle_bad.json"), self._p("evidence_bad.json")])
        self.assertEqual(code, 1)

    def test_bad_fixtures_trigger_all_six_codes(self):
        code, out, err = run_cli([self._p("lifecycle_bad.json"), self._p("evidence_bad.json")])
        report = json.loads(out)
        found_codes = set(v["code"] for v in report["violations"])
        expected = {
            "UNKNOWN_TASK_REFERENCE",
            "DUPLICATE_SUBMISSION_ID",
            "EVIDENCE_BEFORE_TASK_CREATED",
            "EVIDENCE_AFTER_TERMINAL_STATE",
            "MISSING_EVIDENCE_FOR_SUBMITTED_STATE",
            "IMPOSSIBLE_TIMESTAMP",
        }
        self.assertEqual(found_codes, expected)

    def test_bad_fixtures_do_not_contain_orphan_code(self):
        code, out, err = run_cli([self._p("lifecycle_bad.json"), self._p("evidence_bad.json")])
        report = json.loads(out)
        found_codes = set(v["code"] for v in report["violations"])
        self.assertNotIn("ORPHAN_LIFECYCLE_TASK", found_codes)

    def test_bad_fixtures_repeated_runs_identical(self):
        args = [self._p("lifecycle_bad.json"), self._p("evidence_bad.json")]
        _, out1, _ = run_cli(args)
        _, out2, _ = run_cli(args)
        self.assertEqual(out1, out2)


class SortOrderTests(unittest.TestCase):
    def test_sort_key_handles_missing_task_id(self):
        v = {"code": "DUPLICATE_SUBMISSION_ID", "submission_id": "x"}
        key = li._violation_sort_key(v)
        self.assertEqual(key[1], "")

    def test_sort_key_handles_missing_submission_id(self):
        v = {"code": "MISSING_EVIDENCE_FOR_SUBMITTED_STATE", "task_id": "t1"}
        key = li._violation_sort_key(v)
        self.assertEqual(key[2], "")

    def test_sort_order_is_by_code_first(self):
        v1 = {"code": "AAA", "task_id": "zzz"}
        v2 = {"code": "BBB", "task_id": "aaa"}
        self.assertLess(li._violation_sort_key(v1), li._violation_sort_key(v2))

    def test_full_json_tiebreak_used_for_identical_code_task_submission(self):
        v1 = {"code": "X", "task_id": "t1", "submission_id": None, "extra": "a"}
        v2 = {"code": "X", "task_id": "t1", "submission_id": None, "extra": "b"}
        self.assertNotEqual(li._violation_sort_key(v1), li._violation_sort_key(v2))
        self.assertLess(li._violation_sort_key(v1), li._violation_sort_key(v2))

    def test_output_violations_actually_sorted(self):
        report = li.build_report(
            [lc("t1", "created", "2026-01-01T00:00:00Z"), lc("t2", "created", "2026-01-01T00:00:00Z")],
            [ev("s1", "ghostB", "2026-01-01T00:00:00Z"), ev("s2", "ghostA", "2026-01-01T00:00:00Z")],
        )
        keys = [li._violation_sort_key(v) for v in report["violations"]]
        self.assertEqual(keys, sorted(keys))


class MixedScenarioTests(unittest.TestCase):
    def test_multiple_violation_types_same_task(self):
        # A single task can trigger more than one code simultaneously.
        lifecycle = [
            lc("t1", "created", "2026-01-01T12:00:00Z"),
            lc("t1", "rewarded", "2026-01-01T13:00:00Z"),
        ]
        evidence = [
            ev("dup", "t1", "2026-01-01T14:00:00Z"),  # after terminal
            ev("dup", "t1", "2026-01-01T15:00:00Z"),  # duplicate + after terminal
        ]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        codes = codes_of(v)
        self.assertIn("DUPLICATE_SUBMISSION_ID", codes)
        self.assertIn("EVIDENCE_AFTER_TERMINAL_STATE", codes)

    def test_large_number_of_tasks_no_crash(self):
        lifecycle = [lc("t%d" % i, "created", "2026-01-01T00:00:00Z") for i in range(500)]
        evidence = [ev("s%d" % i, "t%d" % i, "2026-01-01T00:00:01Z") for i in range(500)]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_same_task_id_reused_multiple_lifecycle_entries_fine(self):
        lifecycle = [
            lc("t1", "created", "2026-01-01T00:00:00Z"),
            lc("t1", "reviewing", "2026-01-01T00:30:00Z"),
            lc("t1", "submitted", "2026-01-01T01:00:00Z"),
            lc("t1", "rewarded", "2026-01-01T02:00:00Z"),
        ]
        evidence = [ev("s1", "t1", "2026-01-01T01:15:00Z")]
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records(evidence))
        self.assertEqual(v, [])

    def test_evidence_type_and_value_fields_ignored_entirely(self):
        lifecycle = [lc("t1", "created", "2026-01-01T00:00:00Z")]
        rec = {
            "submission_id": "s1", "task_id": "t1",
            "evidence_type": "whatever-unvalidated-type",
            "value": {"nested": ["anything", 1, None]},
            "submitted_at": "2026-01-01T00:00:01Z",
        }
        v = li.check_links(li.validate_lifecycle_records(lifecycle), li.validate_evidence_records([rec]))
        self.assertEqual(v, [])

    def test_unknown_task_and_duplicate_combined(self):
        evidence = [
            ev("dup", "ghost", "2026-01-01T00:00:00Z"),
            ev("dup", "ghost", "2026-01-01T00:00:01Z"),
        ]
        v = li.check_links([], li.validate_evidence_records(evidence))
        codes = codes_of(v)
        self.assertEqual(codes, ["DUPLICATE_SUBMISSION_ID", "UNKNOWN_TASK_REFERENCE", "UNKNOWN_TASK_REFERENCE"])


if __name__ == "__main__":
    unittest.main()

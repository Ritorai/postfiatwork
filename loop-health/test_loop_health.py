"""test_loop_health.py -- stdlib-only unittest suite for loop_health.py.

Run with:  python3 -m unittest test_loop_health -v
"""

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

import loop_health as lh

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "loop_health.py")
HEALTHY_FIXTURE = os.path.join(HERE, "histories_healthy.json")
UNHEALTHY_FIXTURE = os.path.join(HERE, "histories_unhealthy.json")

NOW = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-08-03T00:00:00Z"


def run_cli(args, cwd=HERE):
    """Run the CLI as a subprocess exactly like a real user would."""
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def seconds_ago(s):
    return lh.iso_z(NOW - timedelta(seconds=s))


def hours_ago(h):
    return seconds_ago(h * 3600)


def mk_task(task_id="T-1", events=None):
    return {"task_id": task_id, "events": [] if events is None else events}


def ev(state, at, refusal_reason=None, **extra):
    d = {"state": state, "at": at}
    if refusal_reason is not None:
        d["refusal_reason"] = refusal_reason
    d.update(extra)
    return d


def codes_of(findings):
    return [f["code"] for f in findings]


def only(findings, code):
    return [f for f in findings if f["code"] == code]


# Small throwaway fixtures used only by TestCLI, created once for the whole
# module and removed at the end so the working tree stays clean.
_SIDE_FIXTURES = {
    "not_json.txt": "this is { not valid json",
    "object_not_array.json": json.dumps({"task_id": "x"}),
    "empty_array.json": "[]",
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
        result = lh.parse_utc_timestamp(raw)
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
            lh.parse_utc_timestamp(raw)
    return test


for _name, _raw in INVALID_TIMESTAMP_CASES.items():
    setattr(TestParseUtcTimestampInvalid, f"test_invalid_{_name}", _make_invalid_test(_raw))


# ==========================================================================
# iso_z
# ==========================================================================

class TestIsoZ(unittest.TestCase):
    def test_replaces_plus_zero_offset_with_z(self):
        dt = datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(lh.iso_z(dt), "2026-08-02T00:00:00+00:00".replace("+00:00", "Z"))

    def test_roundtrips_through_parse(self):
        dt = lh.parse_utc_timestamp("2026-08-02T13:45:30Z")
        self.assertEqual(lh.iso_z(dt), "2026-08-02T13:45:30Z")

    def test_preserves_microseconds(self):
        dt = lh.parse_utc_timestamp("2026-08-02T00:00:00.500000Z")
        self.assertIn("500000", lh.iso_z(dt))

    def test_ends_with_z_not_offset(self):
        dt = lh.parse_utc_timestamp("2026-08-02T00:00:00+00:00")
        self.assertTrue(lh.iso_z(dt).endswith("Z"))
        self.assertNotIn("+00:00", lh.iso_z(dt))


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
    "negative_zero": (-0, "0d 0h 0m"),
    "fractional_truncated_down": (90.9, "0d 0h 1m"),
    "seventy_two_hours_exactly": (72 * 3600, "3d 0h 0m"),
    "one_second": (1, "0d 0h 0m"),
}


def _make_format_age_test(seconds, expected):
    def test(self):
        self.assertEqual(lh.format_age(seconds), expected)
    return test


for _name, (_seconds, _expected) in FORMAT_AGE_CASES.items():
    setattr(TestFormatAge, f"test_{_name}", _make_format_age_test(_seconds, _expected))


# ==========================================================================
# process_task -- record-level MALFORMED_RECORD
# ==========================================================================

class TestProcessTaskRecordShape(unittest.TestCase):
    def _run(self, record):
        return lh.process_task(0, record, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)

    def test_record_not_a_dict_string(self):
        findings, rounds, reasons = self._run("not-a-dict")
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)
        self.assertEqual(reasons, [])
        self.assertEqual(findings[0]["task_id"], "<index:0>")

    def test_record_not_a_dict_list(self):
        findings, rounds, _ = self._run(["a", "b"])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_record_not_a_dict_number(self):
        findings, rounds, _ = self._run(42)
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_record_not_a_dict_none(self):
        findings, rounds, _ = self._run(None)
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_missing_task_id_key(self):
        findings, rounds, _ = self._run({"events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_task_id_is_none(self):
        findings, rounds, _ = self._run({"task_id": None, "events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_task_id_is_integer(self):
        findings, rounds, _ = self._run({"task_id": 123, "events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_task_id_is_empty_string(self):
        findings, rounds, _ = self._run({"task_id": "", "events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_task_id_is_list(self):
        findings, rounds, _ = self._run({"task_id": ["x"], "events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_missing_events_key(self):
        findings, rounds, _ = self._run({"task_id": "T-1"})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_events_not_a_list_string(self):
        findings, rounds, _ = self._run({"task_id": "T-1", "events": "nope"})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_events_not_a_list_dict(self):
        findings, rounds, _ = self._run({"task_id": "T-1", "events": {}})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_events_not_a_list_none(self):
        findings, rounds, _ = self._run({"task_id": "T-1", "events": None})
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertIsNone(rounds)

    def test_valid_task_id_unicode(self):
        findings, rounds, _ = self._run({"task_id": "任务-1", "events": []})
        self.assertEqual(codes_of(findings), [lh.CODE_EMPTY_HISTORY])
        self.assertEqual(rounds, 0)

    def test_malformed_record_finding_has_record_index(self):
        findings, _, _ = self._run("bad")
        self.assertIn("record_index", findings[0])
        self.assertEqual(findings[0]["record_index"], 0)


# ==========================================================================
# process_task -- EMPTY_HISTORY
# ==========================================================================

class TestProcessTaskEmptyHistory(unittest.TestCase):
    def test_zero_events_is_empty_history(self):
        findings, rounds, reasons = lh.process_task(
            0, mk_task("T-1", []), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(codes_of(findings), [lh.CODE_EMPTY_HISTORY])
        self.assertEqual(rounds, 0)
        self.assertEqual(reasons, [])

    def test_empty_history_finding_task_id(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-EMPTY", []), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(findings[0]["task_id"], "T-EMPTY")

    def test_empty_history_rounds_is_zero_not_a_finding_by_itself(self):
        # Zero rounds must never manifest as EXCESSIVE_RESUBMISSIONS.
        findings, rounds, _ = lh.process_task(
            0, mk_task("T-1", []), NOW, 0, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 0)
        self.assertNotIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))

    def test_nonempty_events_no_empty_history_finding(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("proposed", hours_ago(1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertNotIn(lh.CODE_EMPTY_HISTORY, codes_of(findings))


# ==========================================================================
# process_task -- event-level MALFORMED_RECORD
# ==========================================================================

class TestProcessTaskEventShape(unittest.TestCase):
    def _run(self, events):
        return lh.process_task(
            0, mk_task("T-1", events), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )

    def test_event_not_a_dict(self):
        findings, _, _ = self._run(["not-an-object"])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_missing_state_key(self):
        findings, _, _ = self._run([{"at": hours_ago(1)}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_state_is_empty_string(self):
        findings, _, _ = self._run([{"state": "", "at": hours_ago(1)}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_state_is_none(self):
        findings, _, _ = self._run([{"state": None, "at": hours_ago(1)}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_state_is_integer(self):
        findings, _, _ = self._run([{"state": 7, "at": hours_ago(1)}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_missing_at_key(self):
        findings, _, _ = self._run([{"state": "proposed"}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_at_is_integer(self):
        findings, _, _ = self._run([{"state": "proposed", "at": 20260802}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_event_at_is_none(self):
        findings, _, _ = self._run([{"state": "proposed", "at": None}])
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])

    def test_one_bad_one_good_event_only_bad_flagged(self):
        findings, rounds, _ = self._run(
            [{"state": "", "at": hours_ago(1)}, ev("proposed", hours_ago(2))]
        )
        self.assertEqual(codes_of(findings), [lh.CODE_MALFORMED_RECORD])
        self.assertEqual(rounds, 0)

    def test_malformed_event_has_event_index(self):
        findings, _, _ = self._run([ev("proposed", hours_ago(1)), {"state": ""}])
        bad = only(findings, lh.CODE_MALFORMED_RECORD)[0]
        self.assertEqual(bad["event_index"], 1)

    def test_all_events_malformed_yields_zero_rounds_no_latest_state(self):
        findings, rounds, _ = self._run([{"state": ""}, {"at": "x"}])
        self.assertEqual(rounds, 0)
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))


# ==========================================================================
# process_task -- INVALID_TIMESTAMP
# ==========================================================================

class TestProcessTaskInvalidTimestamp(unittest.TestCase):
    def _run(self, at_raw):
        return lh.process_task(
            0,
            mk_task("T-1", [ev("submitted", at_raw)]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )

    def test_garbage_string(self):
        findings, rounds, _ = self._run("not-a-timestamp")
        self.assertEqual(codes_of(findings), [lh.CODE_INVALID_TIMESTAMP])
        self.assertEqual(rounds, 0)

    def test_non_utc_offset(self):
        findings, _, _ = self._run("2026-08-02T00:00:00+05:30")
        self.assertEqual(codes_of(findings), [lh.CODE_INVALID_TIMESTAMP])

    def test_timezone_naive(self):
        findings, _, _ = self._run("2026-08-02T00:00:00")
        self.assertEqual(codes_of(findings), [lh.CODE_INVALID_TIMESTAMP])

    def test_empty_string(self):
        findings, _, _ = self._run("")
        self.assertEqual(codes_of(findings), [lh.CODE_INVALID_TIMESTAMP])

    def test_finding_has_at_raw(self):
        findings, _, _ = self._run("garbage")
        self.assertEqual(findings[0]["at_raw"], "garbage")

    def test_finding_has_event_index(self):
        findings, _, _ = self._run("garbage")
        self.assertEqual(findings[0]["event_index"], 0)

    def test_invalid_timestamp_excludes_event_from_sequence(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task(
                "T-1",
                [
                    ev("verification_requested", hours_ago(5)),
                    ev("submitted", "garbage"),
                    ev("submitted", hours_ago(1)),
                ],
            ),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        # verification_requested -> submitted(hours_ago(1)) is still adjacent
        # once the unparseable event is excluded from the sequence.
        self.assertEqual(rounds, 1)


# ==========================================================================
# process_task -- UNKNOWN_STATE
# ==========================================================================

class TestProcessTaskUnknownState(unittest.TestCase):
    def test_unrecognized_state_flagged(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("archived", hours_ago(1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(codes_of(findings), [lh.CODE_UNKNOWN_STATE])

    def test_unknown_state_finding_carries_state(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("archived", hours_ago(1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(findings[0]["state"], "archived")

    def test_all_seven_known_states_are_not_unknown(self):
        for state in lh.ALLOWED_STATES:
            findings, _, _ = lh.process_task(
                0,
                mk_task("T-1", [ev(state, hours_ago(1))]),
                NOW,
                lh.DEFAULT_MAX_ROUNDS,
                lh.DEFAULT_REVIEW_OVERDUE_HOURS,
            )
            self.assertNotIn(lh.CODE_UNKNOWN_STATE, codes_of(findings), state)

    def test_unknown_state_case_sensitive(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("Proposed", hours_ago(1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(codes_of(findings), [lh.CODE_UNKNOWN_STATE])

    def test_unknown_state_does_not_block_valid_timestamp_use(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task(
                "T-1",
                [ev("verification_requested", hours_ago(3)), ev("weird", hours_ago(1))],
            ),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        # weird is now the latest state; not overdue-eligible.
        self.assertEqual(rounds, 0)
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_unknown_state_breaks_resubmission_adjacency(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task(
                "T-1",
                [
                    ev("verification_requested", hours_ago(3)),
                    ev("weird_intermediate", hours_ago(2)),
                    ev("submitted", hours_ago(1)),
                ],
            ),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(rounds, 0)


# ==========================================================================
# process_task -- resubmission_rounds computation
# ==========================================================================

class TestResubmissionRounds(unittest.TestCase):
    def _rounds(self, states_hours_ago):
        events = [ev(state, hours_ago(h)) for state, h in states_hours_ago]
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        return rounds

    def test_no_events_of_interest_zero_rounds(self):
        self.assertEqual(self._rounds([("proposed", 10), ("accepted", 9)]), 0)

    def test_single_verification_to_submitted_is_one_round(self):
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("submitted", 8),
                ]
            ),
            1,
        )

    def test_two_rounds(self):
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("submitted", 8),
                    ("verification_requested", 7),
                    ("submitted", 6),
                ]
            ),
            2,
        )

    def test_verification_requested_with_no_following_submitted(self):
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("awaiting_review", 8),
                ]
            ),
            0,
        )

    def test_verification_requested_followed_by_awaiting_review_not_submitted(self):
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("awaiting_review", 8),
                    ("rewarded", 7),
                ]
            ),
            0,
        )

    def test_refused_then_resubmitted_does_not_count_as_a_round(self):
        # Documented, intentional narrow definition: only a DIRECT,
        # adjacent verification_requested -> submitted pair counts.
        # A refusal breaking the adjacency means the resubmission after
        # a refusal is NOT counted here (see README.md limitations).
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("refused", 8),
                    ("submitted", 7),
                ]
            ),
            0,
        )

    def test_submitted_then_verification_requested_then_submitted_twice_rejected_between(self):
        self.assertEqual(
            self._rounds(
                [
                    ("submitted", 10),
                    ("verification_requested", 9),
                    ("submitted", 8),
                    ("verification_requested", 7),
                    ("refused", 6),
                    ("submitted", 5),
                ]
            ),
            1,
        )

    def test_non_chronological_input_order_still_sorted_correctly(self):
        events = [
            ev("submitted", hours_ago(8)),
            ev("submitted", hours_ago(10)),
            ev("verification_requested", hours_ago(9)),
        ]
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 1)

    def test_identical_timestamps_tie_break_is_original_order(self):
        # Two events share the same "at". The one that appears LATER in
        # the input array is treated as chronologically later.
        events = [
            ev("verification_requested", hours_ago(5)),
            ev("proposed", hours_ago(5)),
            ev("submitted", hours_ago(5)),
        ]
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        # Original order at the tied timestamp: verification_requested,
        # proposed, submitted -- proposed sits between the two, so it is
        # NOT a direct verification_requested -> submitted adjacency.
        self.assertEqual(rounds, 0)

    def test_identical_timestamps_direct_adjacency_still_counts(self):
        events = [
            ev("proposed", hours_ago(5)),
            ev("verification_requested", hours_ago(5)),
            ev("submitted", hours_ago(5)),
        ]
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 1)

    def test_zero_events_zero_rounds(self):
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", []), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 0)

    def test_single_event_zero_rounds(self):
        _, rounds, _ = lh.process_task(
            0, mk_task("T-1", [ev("proposed", hours_ago(1))]), NOW, 1000, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 0)

    def test_many_rounds(self):
        seq = [("submitted", 20)]
        h = 19
        for _ in range(5):
            seq.append(("verification_requested", h))
            h -= 1
            seq.append(("submitted", h))
            h -= 1
        self.assertEqual(self._rounds(seq), 5)

    def test_reverse_transition_not_counted(self):
        # submitted -> verification_requested (chronologically forward) is
        # NOT the tracked direction; only verification_requested ->
        # submitted counts.
        self.assertEqual(
            self._rounds([("submitted", 10), ("verification_requested", 9)]), 0
        )


# ==========================================================================
# process_task -- EXCESSIVE_RESUBMISSIONS boundary
# ==========================================================================

class TestExcessiveResubmissions(unittest.TestCase):
    def _events_with_rounds(self, n):
        events = [ev("submitted", hours_ago(2 * n + 10))]
        h = 2 * n + 9
        for _ in range(n):
            events.append(ev("verification_requested", hours_ago(h)))
            h -= 1
            events.append(ev("submitted", hours_ago(h)))
            h -= 1
        return events

    def test_default_max_rounds_is_three(self):
        self.assertEqual(lh.DEFAULT_MAX_ROUNDS, 3)

    def test_exactly_at_default_max_rounds_no_finding(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task("T-1", self._events_with_rounds(3)),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(rounds, 3)
        self.assertNotIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))

    def test_one_more_than_default_max_rounds_triggers(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task("T-1", self._events_with_rounds(4)),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(rounds, 4)
        self.assertIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))

    def test_custom_max_rounds_zero_any_round_triggers(self):
        findings, rounds, _ = lh.process_task(
            0, mk_task("T-1", self._events_with_rounds(1)), NOW, 0, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 1)
        self.assertIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))

    def test_custom_max_rounds_zero_no_rounds_no_finding(self):
        findings, rounds, _ = lh.process_task(
            0, mk_task("T-1", [ev("proposed", hours_ago(1))]), NOW, 0, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertEqual(rounds, 0)
        self.assertNotIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))

    def test_finding_carries_rounds_and_max_rounds(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task("T-1", self._events_with_rounds(5)),
            NOW,
            2,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        f = only(findings, lh.CODE_EXCESSIVE_RESUBMISSIONS)[0]
        self.assertEqual(f["rounds"], 5)
        self.assertEqual(f["max_rounds"], 2)

    def test_large_max_rounds_never_triggers(self):
        findings, rounds, _ = lh.process_task(
            0,
            mk_task("T-1", self._events_with_rounds(5)),
            NOW,
            10_000,
            lh.DEFAULT_REVIEW_OVERDUE_HOURS,
        )
        self.assertEqual(rounds, 5)
        self.assertNotIn(lh.CODE_EXCESSIVE_RESUBMISSIONS, codes_of(findings))


# ==========================================================================
# process_task -- REVIEW_OVERDUE
# ==========================================================================

class TestReviewOverdue(unittest.TestCase):
    def test_default_review_overdue_hours_is_72(self):
        self.assertEqual(lh.DEFAULT_REVIEW_OVERDUE_HOURS, 72)

    def test_awaiting_review_exactly_at_threshold_no_breach(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("awaiting_review", hours_ago(72))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_awaiting_review_one_second_past_threshold_breaches(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("awaiting_review", seconds_ago(72 * 3600 + 1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_awaiting_review_one_second_before_threshold_no_breach(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("awaiting_review", seconds_ago(72 * 3600 - 1))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_submitted_exactly_at_threshold_no_breach(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", hours_ago(72))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_submitted_past_threshold_breaches(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", hours_ago(73))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        self.assertIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_proposed_never_triggers_review_overdue(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("proposed", hours_ago(1000))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_accepted_never_triggers_review_overdue(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("accepted", hours_ago(1000))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_verification_requested_never_triggers_review_overdue(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("verification_requested", hours_ago(1000))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_rewarded_never_triggers_review_overdue(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("rewarded", hours_ago(1000))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_refused_never_triggers_review_overdue(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("refused", hours_ago(1000), refusal_reason="x")]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_only_latest_state_matters_old_awaiting_review_then_rewarded(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task(
                "T-1",
                [ev("awaiting_review", hours_ago(1000)), ev("rewarded", hours_ago(1))],
            ),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            72,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_custom_threshold_zero_any_positive_age_breaches(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", seconds_ago(1))]), NOW, lh.DEFAULT_MAX_ROUNDS, 0
        )
        self.assertIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_custom_threshold_zero_exact_now_no_breach(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", NOW_ISO)]), NOW, lh.DEFAULT_MAX_ROUNDS, 0
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_very_large_threshold_suppresses_old_finding(self):
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("awaiting_review", hours_ago(10000))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            99999,
        )
        self.assertNotIn(lh.CODE_REVIEW_OVERDUE, codes_of(findings))

    def test_finding_carries_age_seconds_and_age_human(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", hours_ago(75))]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        f = only(findings, lh.CODE_REVIEW_OVERDUE)[0]
        self.assertEqual(f["age_seconds"], 75 * 3600)
        self.assertEqual(f["age_human"], "3d 3h 0m")

    def test_finding_carries_state_and_since(self):
        at = hours_ago(100)
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("awaiting_review", at)]), NOW, lh.DEFAULT_MAX_ROUNDS, 72
        )
        f = only(findings, lh.CODE_REVIEW_OVERDUE)[0]
        self.assertEqual(f["state"], "awaiting_review")
        self.assertEqual(f["since"], lh.iso_z(lh.parse_utc_timestamp(at)))

    def test_finding_carries_review_overdue_hours_used(self):
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", [ev("submitted", hours_ago(200))]), NOW, lh.DEFAULT_MAX_ROUNDS, 50
        )
        f = only(findings, lh.CODE_REVIEW_OVERDUE)[0]
        self.assertEqual(f["review_overdue_hours"], 50)

    def test_age_seconds_example_from_spec(self):
        # 3d 1h 0m == 262800 seconds
        findings, _, _ = lh.process_task(
            0,
            mk_task("T-1", [ev("submitted", seconds_ago(262800))]),
            NOW,
            lh.DEFAULT_MAX_ROUNDS,
            1,
        )
        f = only(findings, lh.CODE_REVIEW_OVERDUE)[0]
        self.assertEqual(f["age_seconds"], 262800)
        self.assertEqual(f["age_human"], "3d 1h 0m")


# ==========================================================================
# refusal_reason distribution
# ==========================================================================

class TestRefusalReasonExtraction(unittest.TestCase):
    def _reasons(self, events):
        _, _, reasons = lh.process_task(
            0, mk_task("T-1", events), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        return reasons

    def test_refused_with_reason_counted(self):
        self.assertEqual(
            self._reasons([ev("refused", hours_ago(1), refusal_reason="bad_quality")]),
            ["bad_quality"],
        )

    def test_refused_with_empty_string_reason_counted(self):
        self.assertEqual(self._reasons([ev("refused", hours_ago(1), refusal_reason="")]), [""])

    def test_refused_with_unicode_reason_counted(self):
        reason = "拒绝：数据不完整"
        self.assertEqual(self._reasons([ev("refused", hours_ago(1), refusal_reason=reason)]), [reason])

    def test_refused_missing_refusal_reason_key_not_counted(self):
        self.assertEqual(self._reasons([ev("refused", hours_ago(1))]), [])

    def test_refused_with_null_refusal_reason_not_counted(self):
        events = [{"state": "refused", "at": hours_ago(1), "refusal_reason": None}]
        self.assertEqual(self._reasons(events), [])

    def test_refusal_reason_on_non_refused_event_ignored(self):
        events = [ev("accepted", hours_ago(1), refusal_reason="should_be_ignored")]
        self.assertEqual(self._reasons(events), [])

    def test_refusal_reason_on_non_refused_event_not_flagged_malformed(self):
        events = [ev("accepted", hours_ago(1), refusal_reason="should_be_ignored")]
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertNotIn(lh.CODE_MALFORMED_RECORD, codes_of(findings))

    def test_non_string_refusal_reason_flagged_malformed(self):
        events = [{"state": "refused", "at": hours_ago(1), "refusal_reason": 123}]
        findings, _, reasons = lh.process_task(
            0, mk_task("T-1", events), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertIn(lh.CODE_MALFORMED_RECORD, codes_of(findings))
        self.assertEqual(reasons, [])

    def test_non_string_refusal_reason_list_flagged_malformed(self):
        events = [{"state": "refused", "at": hours_ago(1), "refusal_reason": ["x"]}]
        findings, _, _ = lh.process_task(
            0, mk_task("T-1", events), NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS
        )
        self.assertIn(lh.CODE_MALFORMED_RECORD, codes_of(findings))

    def test_multiple_refused_events_all_counted(self):
        events = [
            ev("refused", hours_ago(5), refusal_reason="a"),
            ev("submitted", hours_ago(4)),
            ev("refused", hours_ago(3), refusal_reason="b"),
        ]
        self.assertEqual(sorted(self._reasons(events)), ["a", "b"])

    def test_duplicate_reasons_all_counted_individually(self):
        events = [
            ev("refused", hours_ago(5), refusal_reason="dup"),
            ev("submitted", hours_ago(4)),
            ev("refused", hours_ago(3), refusal_reason="dup"),
        ]
        self.assertEqual(self._reasons(events), ["dup", "dup"])

    def test_refusal_reason_on_event_with_invalid_timestamp_still_counted(self):
        # Timing validity and refusal-reason bookkeeping are independent.
        events = [{"state": "refused", "at": "garbage", "refusal_reason": "still_counts"}]
        self.assertEqual(self._reasons(events), ["still_counts"])


# ==========================================================================
# build_report -- top-level shape
# ==========================================================================

class TestBuildReportShape(unittest.TestCase):
    def test_non_list_root_raises_input_error(self):
        with self.assertRaises(lh.InputError):
            lh.build_report({"task_id": "x"}, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)

    def test_string_root_raises_input_error(self):
        with self.assertRaises(lh.InputError):
            lh.build_report("nope", NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)

    def test_number_root_raises_input_error(self):
        with self.assertRaises(lh.InputError):
            lh.build_report(5, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)

    def test_empty_list_zero_tasks_zero_findings(self):
        report, total = lh.build_report([], NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(total, 0)
        self.assertEqual(report["summary"]["total_tasks"], 0)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["resubmission_rounds"], [])
        self.assertEqual(report["refusal_reason_distribution"], [])

    def test_generated_at_echoes_now(self):
        report, _ = lh.build_report([], NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(report["generated_at"], NOW_ISO)

    def test_options_echo_thresholds(self):
        report, _ = lh.build_report([], NOW, 5, 30)
        self.assertEqual(report["options"], {"max_rounds": 5, "review_overdue_hours": 30})

    def test_total_tasks_counts_malformed_records_too(self):
        data = ["bad", {"task_id": "T-1", "events": []}]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(report["summary"]["total_tasks"], 2)

    def test_counts_by_code_sums_to_total_findings(self):
        data = [
            {"task_id": "T-1", "events": []},
            {"task_id": "T-2", "events": [ev("archived", hours_ago(1))]},
        ]
        report, total = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(sum(report["summary"]["counts_by_code"].values()), total)

    def test_counts_by_code_has_all_six_codes_even_when_zero(self):
        report, _ = lh.build_report([], NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(set(report["summary"]["counts_by_code"].keys()), set(lh.ALL_CODES))

    def test_healthy_zero_rounds_not_a_finding_end_to_end(self):
        data = [{"task_id": "T-1", "events": [ev("proposed", hours_ago(1))]}]
        report, total = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(total, 0)
        self.assertEqual(report["resubmission_rounds"], [{"task_id": "T-1", "resubmission_rounds": 0}])

    def test_resubmission_rounds_excludes_structurally_malformed_records(self):
        data = ["bad", {"task_id": "T-1", "events": []}]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(len(report["resubmission_rounds"]), 1)
        self.assertEqual(report["resubmission_rounds"][0]["task_id"], "T-1")

    def test_resubmission_rounds_sorted_by_task_id(self):
        data = [
            {"task_id": "T-B", "events": []},
            {"task_id": "T-A", "events": []},
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        ids = [e["task_id"] for e in report["resubmission_rounds"]]
        self.assertEqual(ids, ["T-A", "T-B"])

    def test_refusal_distribution_sorted_by_count_desc_then_reason_asc(self):
        data = [
            {
                "task_id": "T-1",
                "events": [
                    ev("refused", hours_ago(5), refusal_reason="zzz"),
                    ev("refused", hours_ago(4), refusal_reason="aaa"),
                    ev("refused", hours_ago(3), refusal_reason="aaa"),
                ],
            }
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(
            report["refusal_reason_distribution"],
            [{"reason": "aaa", "count": 2}, {"reason": "zzz", "count": 1}],
        )

    def test_refusal_distribution_tie_break_alphabetical(self):
        data = [
            {
                "task_id": "T-1",
                "events": [
                    ev("refused", hours_ago(5), refusal_reason="beta"),
                    ev("refused", hours_ago(4), refusal_reason="alpha"),
                ],
            }
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(
            report["refusal_reason_distribution"],
            [{"reason": "alpha", "count": 1}, {"reason": "beta", "count": 1}],
        )

    def test_findings_sorted_by_task_id_then_code(self):
        data = [
            {"task_id": "T-B", "events": [ev("archived", hours_ago(1))]},
            {"task_id": "T-A", "events": []},
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        ids = [f["task_id"] for f in report["findings"]]
        self.assertEqual(ids, sorted(ids))

    def test_multiple_findings_same_task_sorted_by_code(self):
        data = [
            {
                "task_id": "T-1",
                "events": [{"state": "", "at": "garbage"}],
            }
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        codes = [f["code"] for f in report["findings"]]
        self.assertEqual(codes, sorted(codes))

    def test_index_placeholder_can_collide_with_a_real_task_id(self):
        # KNOWN LIMITATION (documented in README.md): the synthetic
        # identifier used for a structurally-malformed record at index N
        # is the literal string "<index:N>". If a *different*, well-formed
        # record happens to have that exact string as its real task_id,
        # their findings share the same visible "task_id" field. They
        # remain distinguishable programmatically: only the synthetic
        # placeholder's MALFORMED_RECORD finding carries "record_index".
        data = [
            "not-a-record",  # malformed record at index 0 -> ref "<index:0>"
            {"task_id": "<index:0>", "events": []},  # real task_id collides
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        matching = [f for f in report["findings"] if f["task_id"] == "<index:0>"]
        self.assertEqual(len(matching), 2)
        codes = sorted(f["code"] for f in matching)
        self.assertEqual(codes, [lh.CODE_EMPTY_HISTORY, lh.CODE_MALFORMED_RECORD])
        malformed = [f for f in matching if f["code"] == lh.CODE_MALFORMED_RECORD][0]
        empty_hist = [f for f in matching if f["code"] == lh.CODE_EMPTY_HISTORY][0]
        self.assertIn("record_index", malformed)
        self.assertNotIn("record_index", empty_hist)

    def test_duplicate_task_ids_both_appear(self):
        data = [
            {"task_id": "T-DUP", "events": []},
            {"task_id": "T-DUP", "events": []},
        ]
        report, _ = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(len(report["resubmission_rounds"]), 2)
        self.assertEqual(len(only(report["findings"], lh.CODE_EMPTY_HISTORY)), 2)


# ==========================================================================
# determinism
# ==========================================================================

class TestDeterminism(unittest.TestCase):
    def test_repeated_calls_produce_identical_canonical_json(self):
        data = [
            {"task_id": "T-1", "events": [ev("proposed", hours_ago(1))]},
            {"task_id": "T-2", "events": []},
            "bad-record",
            {"task_id": "T-3", "events": [ev("archived", hours_ago(2))]},
        ]
        out1 = lh.canonical_json(lh.build_report(data, NOW, 3, 72)[0])
        out2 = lh.canonical_json(lh.build_report(data, NOW, 3, 72)[0])
        self.assertEqual(out1, out2)

    def test_output_independent_of_dict_key_insertion_order(self):
        rec_a = {"task_id": "T-1", "events": [ev("proposed", hours_ago(1))]}
        rec_b = {"events": [ev("proposed", hours_ago(1))], "task_id": "T-1"}
        out_a = lh.canonical_json(lh.build_report([rec_a], NOW, 3, 72)[0])
        out_b = lh.canonical_json(lh.build_report([rec_b], NOW, 3, 72)[0])
        self.assertEqual(out_a, out_b)

    def test_findings_order_independent_of_input_task_order(self):
        data1 = [
            {"task_id": "T-A", "events": []},
            {"task_id": "T-B", "events": []},
        ]
        data2 = [
            {"task_id": "T-B", "events": []},
            {"task_id": "T-A", "events": []},
        ]
        out1 = lh.canonical_json(lh.build_report(data1, NOW, 3, 72)[0])
        out2_report, _ = lh.build_report(data2, NOW, 3, 72)
        # Compare findings ignoring total_tasks ordering-independent fields.
        out1_report, _ = lh.build_report(data1, NOW, 3, 72)
        self.assertEqual(out1_report["findings"], out2_report["findings"])


# ==========================================================================
# canonical_json
# ==========================================================================

class TestCanonicalJson(unittest.TestCase):
    def test_ends_with_single_trailing_newline(self):
        out = lh.canonical_json({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_no_spaces_after_separators(self):
        out = lh.canonical_json({"a": 1, "b": 2})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_keys_sorted(self):
        out = lh.canonical_json({"b": 1, "a": 2})
        self.assertLess(out.index('"a"'), out.index('"b"'))

    def test_ensure_ascii_escapes_unicode(self):
        out = lh.canonical_json({"reason": "拒绝"})
        self.assertNotIn("拒", out)
        self.assertIn("\\u", out)

    def test_roundtrips_through_json_loads(self):
        report, _ = lh.build_report(
            [{"task_id": "T-1", "events": [ev("proposed", hours_ago(1))]}], NOW, 3, 72
        )
        out = lh.canonical_json(report)
        self.assertEqual(json.loads(out), report)

    def test_is_valid_single_line_json(self):
        out = lh.canonical_json({"a": 1})
        self.assertEqual(out.count("\n"), 1)


# ==========================================================================
# CLI
# ==========================================================================

class TestCLI(unittest.TestCase):
    def test_missing_now_exits_2(self):
        result = run_cli([HEALTHY_FIXTURE])
        self.assertEqual(result.returncode, 2)

    def test_missing_input_file_arg_exits_2(self):
        result = run_cli(["--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_invalid_now_value_exits_2(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", "not-a-timestamp"])
        self.assertEqual(result.returncode, 2)

    def test_non_utc_now_value_exits_2(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00+05:30"])
        self.assertEqual(result.returncode, 2)

    def test_nonexistent_input_file_exits_2(self):
        result = run_cli(["/nonexistent/path/does-not-exist.json", "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_not_json_input_exits_2(self):
        result = run_cli([os.path.join(HERE, "not_json.txt"), "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_object_root_exits_2(self):
        result = run_cli([os.path.join(HERE, "object_not_array.json"), "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_negative_max_rounds_exits_2(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "--max-rounds", "-1"])
        self.assertEqual(result.returncode, 2)

    def test_negative_review_overdue_hours_exits_2(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "--review-overdue-hours", "-1"])
        self.assertEqual(result.returncode, 2)

    def test_empty_array_input_exits_0(self):
        result = run_cli([os.path.join(HERE, "empty_array.json"), "--now", NOW_ISO])
        self.assertEqual(result.returncode, 0)

    def test_healthy_fixture_exits_0(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(result.returncode, 0)

    def test_healthy_fixture_stdout_is_valid_json(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z"])
        report = json.loads(result.stdout)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["summary"]["total_findings"], 0)
        self.assertEqual(set(report["summary"]["counts_by_code"]), set(lh.ALL_CODES))

    def test_unhealthy_fixture_exits_1(self):
        result = run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(result.returncode, 1)

    def test_unhealthy_fixture_triggers_all_six_codes(self):
        result = run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z"])
        report = json.loads(result.stdout)
        codes_seen = {f["code"] for f in report["findings"]}
        self.assertEqual(codes_seen, set(lh.ALL_CODES))

    def test_output_flag_writes_file_not_stdout(self):
        out_path = os.path.join(HERE, "_test_cli_output.json")
        try:
            result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "-o", out_path])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as fh:
                json.load(fh)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_output_long_flag_equivalent(self):
        out_path = os.path.join(HERE, "_test_cli_output2.json")
        try:
            result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "--output", out_path])
            self.assertEqual(result.returncode, 0)
            self.assertTrue(os.path.exists(out_path))
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_review_overdue_hours_changes_verdict(self):
        default_result = run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z"])
        lenient_result = run_cli(
            [UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z", "--review-overdue-hours", "99999"]
        )
        default_report = json.loads(default_result.stdout)
        lenient_report = json.loads(lenient_result.stdout)
        self.assertGreater(
            default_report["summary"]["counts_by_code"]["REVIEW_OVERDUE"],
            lenient_report["summary"]["counts_by_code"]["REVIEW_OVERDUE"],
        )
        self.assertGreater(default_report["summary"]["total_findings"], lenient_report["summary"]["total_findings"])

    def test_max_rounds_changes_verdict(self):
        strict_result = run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z", "--max-rounds", "0"])
        lenient_result = run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z", "--max-rounds", "10000"])
        strict_report = json.loads(strict_result.stdout)
        lenient_report = json.loads(lenient_result.stdout)
        self.assertGreater(
            strict_report["summary"]["counts_by_code"]["EXCESSIVE_RESUBMISSIONS"],
            lenient_report["summary"]["counts_by_code"]["EXCESSIVE_RESUBMISSIONS"],
        )

    def test_repeated_runs_byte_identical_output_files(self):
        out1 = os.path.join(HERE, "_test_repro_1.json")
        out2 = os.path.join(HERE, "_test_repro_2.json")
        try:
            run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z", "-o", out1])
            run_cli([UNHEALTHY_FIXTURE, "--now", "2026-08-03T00:00:00Z", "-o", out2])
            with open(out1, "rb") as f1, open(out2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())
        finally:
            for p in (out1, out2):
                if os.path.exists(p):
                    os.remove(p)

    def test_stderr_message_on_missing_now(self):
        result = run_cli([HEALTHY_FIXTURE])
        self.assertNotEqual(result.stderr.strip(), "")

    def test_stderr_message_on_bad_input_file(self):
        result = run_cli(["/nope.json", "--now", NOW_ISO])
        self.assertIn("loop_health.py", result.stderr)

    def test_help_flag_exits_0(self):
        result = run_cli(["--help"])
        self.assertEqual(result.returncode, 0)

    def test_no_args_exits_2(self):
        result = run_cli([])
        self.assertEqual(result.returncode, 2)

    def test_max_rounds_accepts_only_integers(self):
        result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "--max-rounds", "abc"])
        self.assertEqual(result.returncode, 2)

    def test_review_overdue_hours_accepts_floats(self):
        # A fractional-hour threshold is valid usage (not an exit-2 usage
        # error), regardless of whether it happens to produce findings.
        result = run_cli([HEALTHY_FIXTURE, "--now", NOW_ISO, "--review-overdue-hours", "0.5"])
        self.assertIn(result.returncode, (0, 1))
        json.loads(result.stdout)


# ==========================================================================
# No wall-clock reads
# ==========================================================================

class TestNoWallClockRead(unittest.TestCase):
    def test_source_has_no_forbidden_wall_clock_calls(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        needle1 = "now" + "()"
        needle2 = "utc" + "now"
        needle3 = "time" + "." + "time"
        self.assertNotIn(needle1, source)
        self.assertNotIn(needle2, source)
        self.assertNotIn(needle3, source)

    def test_now_is_only_ever_the_injected_cli_value(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("args.now", source)

    def test_process_task_requires_now_argument(self):
        import inspect
        sig = inspect.signature(lh.process_task)
        self.assertIn("now", sig.parameters)

    def test_build_report_requires_now_argument(self):
        import inspect
        sig = inspect.signature(lh.build_report)
        self.assertIn("now", sig.parameters)


# ==========================================================================
# Fixture sanity checks
# ==========================================================================

class TestFixtures(unittest.TestCase):
    def test_healthy_fixture_is_valid_json_array(self):
        with open(HEALTHY_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_unhealthy_fixture_is_valid_json_array(self):
        with open(UNHEALTHY_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_healthy_fixture_produces_zero_findings_via_library(self):
        with open(HEALTHY_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        _, total = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertEqual(total, 0)

    def test_unhealthy_fixture_produces_findings_via_library(self):
        with open(UNHEALTHY_FIXTURE, encoding="utf-8") as fh:
            data = json.load(fh)
        _, total = lh.build_report(data, NOW, lh.DEFAULT_MAX_ROUNDS, lh.DEFAULT_REVIEW_OVERDUE_HOURS)
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()

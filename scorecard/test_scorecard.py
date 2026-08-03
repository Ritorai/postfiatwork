#!/usr/bin/env python3
"""test_scorecard.py -- unittest suite for scorecard.py.

Structured like the sibling suites (test_loop_health.py / test_staleness.py):
one TestCase subclass per concept, with dense table-driven coverage via
dynamically-generated test methods where useful. Run with:

    python3 -m unittest test_scorecard -v
"""

import json
import subprocess
import sys
import tempfile
import os
import unittest
from decimal import Decimal

import scorecard as m

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scorecard.py")


def run_cli(args, input_text=None):
    cmd = [sys.executable, SCRIPT] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, input=input_text)
    return proc.returncode, proc.stdout, proc.stderr


def write_temp_json(obj):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


NOW = m.parse_utc_timestamp("2026-08-03T00:00:00Z")


def rec(contributor="alice", task_id="T-1", events=None, evidence=None):
    r = {"contributor": contributor, "task_id": task_id}
    if events is not None:
        r["events"] = events
    if evidence is not None:
        r["evidence"] = evidence
    return r


def ev(state, at, **extra):
    d = {"state": state, "at": at}
    d.update(extra)
    return d


# --------------------------------------------------------------------------
# parse_utc_timestamp
# --------------------------------------------------------------------------


class TestParseUtcTimestampValid(unittest.TestCase):
    pass


_VALID_TIMESTAMPS = {
    "uppercase_z": "2026-08-02T00:00:00Z",
    "lowercase_z": "2026-08-02T00:00:00z",
    "plus_zero_offset": "2026-08-02T00:00:00+00:00",
    "minus_zero_offset": "2026-08-02T00:00:00-00:00",
    "microseconds_with_z": "2026-08-02T00:00:00.500000Z",
    "microseconds_with_offset": "2026-08-02T00:00:00.123456+00:00",
    "leap_day": "2024-02-29T12:00:00Z",
    "end_of_year": "2026-12-31T23:59:59Z",
    "leading_trailing_whitespace": "  2026-08-02T00:00:00Z  ",
    "space_separator_with_offset": "2026-08-02 00:00:00+00:00",
}


def _make_valid_test(raw):
    def test(self):
        dt = m.parse_utc_timestamp(raw)
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.utcoffset().total_seconds(), 0)
    return test


for _name, _raw in _VALID_TIMESTAMPS.items():
    setattr(TestParseUtcTimestampValid, f"test_valid_{_name}", _make_valid_test(_raw))


class TestParseUtcTimestampInvalid(unittest.TestCase):
    pass


_INVALID_TIMESTAMPS = {
    "empty_string": "",
    "whitespace_only": "   ",
    "garbage_text": "not-a-timestamp",
    "timezone_naive": "2026-08-02T00:00:00",
    "date_only_no_z": "2026-08-02",
    "positive_offset": "2026-08-02T00:00:00+05:30",
    "negative_offset": "2026-08-02T00:00:00-08:00",
    "z_with_embedded_offset": "2026-08-02T00:00:00+00:00Z",
    "bad_month": "2026-13-02T00:00:00Z",
    "bad_day": "2026-08-32T00:00:00Z",
    "bad_hour": "2026-08-02T25:00:00Z",
    "slash_date": "2026/08/02T00:00:00Z",
    "trailing_garbage": "2026-08-02T00:00:00Zgarbage",
}


def _make_invalid_test(raw):
    def test(self):
        with self.assertRaises(ValueError):
            m.parse_utc_timestamp(raw)
    return test


for _name, _raw in _INVALID_TIMESTAMPS.items():
    setattr(TestParseUtcTimestampInvalid, f"test_invalid_{_name}", _make_invalid_test(_raw))


_INVALID_TYPES = {
    "none_value": None,
    "integer_value": 1234,
    "float_value": 1.5,
    "boolean_value": True,
    "list_value": ["2026-08-02T00:00:00Z"],
    "dict_value": {"at": "2026-08-02T00:00:00Z"},
}


def _make_invalid_type_test(value):
    def test(self):
        with self.assertRaises(ValueError):
            m.parse_utc_timestamp(value)
    return test


for _name, _value in _INVALID_TYPES.items():
    setattr(TestParseUtcTimestampInvalid, f"test_invalid_type_{_name}", _make_invalid_type_test(_value))


# --------------------------------------------------------------------------
# iso_z
# --------------------------------------------------------------------------


class TestIsoZ(unittest.TestCase):
    def test_replaces_plus_zero_offset_with_z(self):
        dt = m.parse_utc_timestamp("2026-08-02T00:00:00+00:00")
        self.assertTrue(m.iso_z(dt).endswith("Z"))

    def test_ends_with_z_not_offset(self):
        dt = m.parse_utc_timestamp("2026-08-02T00:00:00Z")
        self.assertEqual(m.iso_z(dt), "2026-08-02T00:00:00Z")

    def test_roundtrips_through_parse(self):
        dt = m.parse_utc_timestamp("2026-08-02T05:06:07Z")
        again = m.parse_utc_timestamp(m.iso_z(dt))
        self.assertEqual(dt, again)

    def test_preserves_microseconds(self):
        dt = m.parse_utc_timestamp("2026-08-02T00:00:00.500000Z")
        self.assertIn(".500000", m.iso_z(dt))


# --------------------------------------------------------------------------
# canonical_json
# --------------------------------------------------------------------------


class TestCanonicalJson(unittest.TestCase):
    def _sample(self):
        report, _ = m.build_report([rec(events=[ev("proposed", "2026-08-01T00:00:00Z")])], NOW, 5)
        return report

    def test_ends_with_single_trailing_newline(self):
        out = m.canonical_json(self._sample())
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_is_valid_single_line_json(self):
        out = m.canonical_json(self._sample())
        self.assertEqual(out.count("\n"), 1)
        json.loads(out)

    def test_no_spaces_after_separators(self):
        # Structural separators must be exactly ",": no space anywhere a
        # JSON structural comma/colon appears. We can't naively substring-
        # search the whole blob for ", " because string VALUES (like the
        # disclaimer's prose) legitimately contain ", " -- that's content,
        # not a structural separator. Instead, assert self-consistency:
        # re-dumping the parsed object with the documented canonical
        # settings reproduces the exact same bytes.
        out = m.canonical_json(self._sample())
        obj = json.loads(out)
        expected = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        self.assertEqual(out, expected)

    def test_keys_sorted(self):
        out = m.canonical_json(self._sample())
        obj = json.loads(out)
        self.assertEqual(list(obj.keys()), sorted(obj.keys()))

    def test_ensure_ascii_escapes_unicode(self):
        report, _ = m.build_report(
            [rec(contributor="éé", events=[ev("proposed", "2026-08-01T00:00:00Z")])],
            NOW,
            5,
        )
        out = m.canonical_json(report)
        self.assertNotIn("é", out)
        self.assertIn("\\u00e9", out)

    def test_roundtrips_through_json_loads(self):
        out = m.canonical_json(self._sample())
        obj = json.loads(out)
        self.assertIsInstance(obj, dict)


# --------------------------------------------------------------------------
# make_rate
# --------------------------------------------------------------------------


class TestMakeRate(unittest.TestCase):
    def test_simple_half(self):
        r = m.make_rate(1, 2)
        self.assertEqual(r["value"], "0.500000")
        self.assertEqual(r["numerator"], 1)
        self.assertEqual(r["denominator"], 2)
        self.assertIsNone(r["note"])

    def test_exact_third_rounded(self):
        r = m.make_rate(1, 3)
        self.assertEqual(r["value"], "0.333333")

    def test_two_thirds_rounded(self):
        r = m.make_rate(2, 3)
        self.assertEqual(r["value"], "0.666667")

    def test_zero_numerator(self):
        r = m.make_rate(0, 5)
        self.assertEqual(r["value"], "0.000000")

    def test_full_ratio_is_one(self):
        r = m.make_rate(5, 5)
        self.assertEqual(r["value"], "1.000000")

    def test_zero_denominator_value_is_null(self):
        r = m.make_rate(0, 0)
        self.assertIsNone(r["value"])
        self.assertEqual(r["note"], "UNDEFINED_ZERO_DENOMINATOR")

    def test_zero_denominator_nonzero_numerator_still_null(self):
        # numerator can never exceed denominator in this domain, but the
        # helper must not divide by zero regardless of numerator value.
        r = m.make_rate(3, 0)
        self.assertIsNone(r["value"])

    def test_explicit_note_overrides_and_skips_division(self):
        r = m.make_rate(3, 10, note="INSUFFICIENT_DATA")
        self.assertIsNone(r["value"])
        self.assertEqual(r["note"], "INSUFFICIENT_DATA")
        self.assertEqual(r["numerator"], 3)
        self.assertEqual(r["denominator"], 10)

    def test_value_is_a_string_not_a_float(self):
        r = m.make_rate(1, 4)
        self.assertIsInstance(r["value"], str)

    def test_banker_rounding_half_even_example(self):
        # 0.0000005 exactly at the halfway point for 6dp rounds to even.
        exact = Decimal(1) / Decimal(2000000)
        r = m.make_rate(1, 2000000)
        # Just assert it doesn't crash and returns a 6dp string.
        self.assertRegex(r["value"], r"^\d+\.\d{6}$")

    def test_large_numbers(self):
        r = m.make_rate(999999, 1000000)
        self.assertEqual(r["value"], "0.999999")


# --------------------------------------------------------------------------
# process_record -- record-level shape
# --------------------------------------------------------------------------


class TestProcessRecordShape(unittest.TestCase):
    def test_record_not_a_dict(self):
        findings = []
        result = m.process_record(0, ["not", "a", "dict"], findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")
        self.assertEqual(findings[0]["task_id"], "<index:0>")

    def test_record_none(self):
        findings = []
        result = m.process_record(2, None, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_record_is_a_string(self):
        findings = []
        result = m.process_record(0, "oops", findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_missing_task_id(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_task_id_empty_string(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": "", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_task_id_none(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": None, "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_task_id_integer(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": 100, "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_missing_events_key(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": "T-1"}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_events_not_a_list(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": "T-1", "events": {}}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_events_is_none(self):
        findings = []
        result = m.process_record(0, {"contributor": "alice", "task_id": "T-1", "events": None}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_evidence_not_a_list(self):
        findings = []
        result = m.process_record(
            0, {"contributor": "alice", "task_id": "T-1", "events": [], "evidence": {}}, findings
        )
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_evidence_absent_is_ok_zero_items(self):
        findings = []
        result = m.process_record(
            0, {"contributor": "alice", "task_id": "T-1", "events": [ev("proposed", "2026-08-01T00:00:00Z")]}, findings
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence_types, [])
        self.assertEqual(findings, [])

    def test_valid_minimal_record_no_findings(self):
        findings = []
        result = m.process_record(
            0,
            {
                "contributor": "alice",
                "task_id": "T-1",
                "events": [ev("proposed", "2026-08-01T00:00:00Z")],
                "evidence": [],
            },
            findings,
        )
        self.assertIsNotNone(result)
        self.assertEqual(findings, [])

    def test_task_id_unicode_accepted(self):
        findings = []
        result = m.process_record(
            0,
            {"contributor": "alice", "task_id": "任务-1", "events": [ev("proposed", "2026-08-01T00:00:00Z")]},
            findings,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.task_id, "任务-1")


# --------------------------------------------------------------------------
# process_record -- contributor field
# --------------------------------------------------------------------------


class TestProcessRecordContributor(unittest.TestCase):
    def test_missing_contributor_key(self):
        findings = []
        result = m.process_record(0, {"task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_contributor_none(self):
        findings = []
        result = m.process_record(0, {"contributor": None, "task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_contributor_empty_string(self):
        findings = []
        result = m.process_record(0, {"contributor": "", "task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_contributor_whitespace_only(self):
        findings = []
        result = m.process_record(0, {"contributor": "   ", "task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_contributor_integer(self):
        findings = []
        result = m.process_record(0, {"contributor": 42, "task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_contributor_list(self):
        findings = []
        result = m.process_record(0, {"contributor": ["alice"], "task_id": "T-1", "events": []}, findings)
        self.assertIsNone(result)
        self.assertEqual(findings[0]["code"], "MISSING_CONTRIBUTOR")

    def test_missing_contributor_finding_uses_task_id(self):
        findings = []
        m.process_record(0, {"task_id": "T-99", "events": []}, findings)
        self.assertEqual(findings[0]["task_id"], "T-99")
        self.assertIsNone(findings[0]["contributor"])

    def test_missing_contributor_key_message_has_no_memory_address(self):
        # Regression test for a real bug caught during verification: when
        # the "contributor" key is entirely absent, the internal _MISSING
        # sentinel object's repr() embeds a per-process memory address
        # (e.g. "<object object at 0x7f...>"), which silently broke
        # byte-identical reproducibility across repeated runs of the same
        # input. The finding message must never contain that pattern.
        findings = []
        m.process_record(0, {"task_id": "T-99", "events": []}, findings)
        message = findings[0]["message"]
        self.assertNotIn("0x", message)
        self.assertIn("<absent>", message)

    def test_repeated_process_record_calls_produce_identical_messages(self):
        # Direct regression check: process_record on the SAME under-
        # specified input, called twice, must yield byte-identical finding
        # messages (not just equal codes) -- this is what the memory-
        # address bug violated.
        findings_a = []
        findings_b = []
        m.process_record(0, {"task_id": "T-99", "events": []}, findings_a)
        m.process_record(0, {"task_id": "T-99", "events": []}, findings_b)
        self.assertEqual(findings_a, findings_b)

    def test_contributor_unicode_accepted(self):
        findings = []
        result = m.process_record(
            0,
            {"contributor": "李四", "task_id": "T-1", "events": [ev("proposed", "2026-08-01T00:00:00Z")]},
            findings,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.contributor, "李四")

    def test_contributor_trailing_whitespace_preserved_not_stripped(self):
        findings = []
        result = m.process_record(
            0,
            {"contributor": "alice ", "task_id": "T-1", "events": [ev("proposed", "2026-08-01T00:00:00Z")]},
            findings,
        )
        self.assertEqual(result.contributor, "alice ")

    def test_case_variants_treated_as_distinct_contributors(self):
        data = [
            rec(contributor="Alice", task_id="T-1", events=[ev("proposed", "2026-08-01T00:00:00Z")]),
            rec(contributor="alice", task_id="T-2", events=[ev("proposed", "2026-08-01T00:00:00Z")]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        contributors = {sc["contributor"] for sc in report["scorecards"]}
        self.assertEqual(contributors, {"Alice", "alice"})

    def test_trailing_whitespace_variants_treated_as_distinct_contributors(self):
        data = [
            rec(contributor="alice", task_id="T-1", events=[ev("proposed", "2026-08-01T00:00:00Z")]),
            rec(contributor="alice ", task_id="T-2", events=[ev("proposed", "2026-08-01T00:00:00Z")]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        contributors = {sc["contributor"] for sc in report["scorecards"]}
        self.assertEqual(contributors, {"alice", "alice "})
        self.assertEqual(len(report["scorecards"]), 2)


# --------------------------------------------------------------------------
# process_record -- event-level shape
# --------------------------------------------------------------------------


class TestProcessRecordEvents(unittest.TestCase):
    def test_event_not_a_dict(self):
        findings = []
        result = m.process_record(0, rec(events=["not-a-dict"]), findings)
        self.assertIsNotNone(result)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")
        self.assertEqual(findings[0]["event_index"], 0)

    def test_event_missing_state(self):
        findings = []
        result = m.process_record(0, rec(events=[{"at": "2026-08-01T00:00:00Z"}]), findings)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_event_state_empty_string(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("", "2026-08-01T00:00:00Z")]), findings)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_event_state_not_a_string(self):
        findings = []
        result = m.process_record(0, rec(events=[ev(5, "2026-08-01T00:00:00Z")]), findings)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_event_missing_at(self):
        findings = []
        result = m.process_record(0, rec(events=[{"state": "proposed"}]), findings)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_event_at_not_a_string(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("proposed", 12345)]), findings)
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_event_at_unparseable(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("proposed", "not-a-date")]), findings)
        self.assertEqual(findings[0]["code"], "INVALID_TIMESTAMP")
        self.assertEqual(findings[0]["at_raw"], "not-a-date")

    def test_event_at_non_utc_offset(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("proposed", "2026-08-01T00:00:00+05:30")]), findings)
        self.assertEqual(findings[0]["code"], "INVALID_TIMESTAMP")

    def test_unknown_state_flagged(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("archived", "2026-08-01T00:00:00Z")]), findings)
        self.assertEqual(findings[0]["code"], "UNKNOWN_STATE")
        self.assertEqual(findings[0]["state"], "archived")

    def test_unknown_state_case_sensitive(self):
        findings = []
        result = m.process_record(0, rec(events=[ev("Proposed", "2026-08-01T00:00:00Z")]), findings)
        self.assertEqual(findings[0]["code"], "UNKNOWN_STATE")

    def test_all_seven_known_states_not_unknown(self):
        for state in m.ALLOWED_STATES:
            findings = []
            m.process_record(0, rec(events=[ev(state, "2026-08-01T00:00:00Z")]), findings)
            self.assertEqual(findings, [], f"state {state!r} should not be flagged")

    def test_empty_events_list_is_empty_history(self):
        findings = []
        result = m.process_record(0, rec(events=[]), findings)
        self.assertIsNotNone(result)
        self.assertEqual(findings[0]["code"], "EMPTY_HISTORY")
        self.assertEqual(result.terminal_state, None)

    def test_empty_history_still_attributed(self):
        findings = []
        result = m.process_record(0, rec(events=[]), findings)
        self.assertEqual(result.contributor, "alice")

    def test_one_bad_one_good_event_only_bad_flagged(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[{"nope": True}, ev("proposed", "2026-08-01T00:00:00Z")]),
            findings,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["event_index"], 0)

    def test_refusal_reason_non_string_flagged(self):
        findings = []
        result = m.process_record(
            0, rec(events=[ev("refused", "2026-08-01T00:00:00Z", refusal_reason=123)]), findings
        )
        self.assertEqual(findings[0]["code"], "MALFORMED_RECORD")

    def test_refusal_reason_string_ok(self):
        findings = []
        result = m.process_record(
            0, rec(events=[ev("refused", "2026-08-01T00:00:00Z", refusal_reason="bad")]), findings
        )
        self.assertEqual(findings, [])

    def test_refusal_reason_null_ok(self):
        findings = []
        result = m.process_record(
            0, rec(events=[ev("refused", "2026-08-01T00:00:00Z", refusal_reason=None)]), findings
        )
        self.assertEqual(findings, [])

    def test_invalid_timestamp_excludes_event_from_terminal_determination(self):
        findings = []
        result = m.process_record(
            0,
            rec(
                events=[
                    ev("submitted", "2026-08-01T00:00:00Z"),
                    ev("rewarded", "not-a-date"),
                ]
            ),
            findings,
        )
        # The rewarded event's timestamp is unparseable, so it cannot be
        # placed in time and cannot become the "latest" state.
        self.assertIsNone(result.terminal_state)


# --------------------------------------------------------------------------
# process_record -- evidence-level shape
# --------------------------------------------------------------------------


class TestProcessRecordEvidence(unittest.TestCase):
    def test_evidence_item_not_a_dict(self):
        findings = []
        result = m.process_record(0, rec(events=[], evidence=["nope"]), findings)
        malformed = [f for f in findings if f["code"] == "MALFORMED_RECORD" and "evidence_index" in f]
        self.assertEqual(len(malformed), 1)
        self.assertEqual(result.evidence_types, [])

    def test_evidence_item_missing_evidence_type(self):
        findings = []
        result = m.process_record(0, rec(events=[], evidence=[{}]), findings)
        malformed = [f for f in findings if f["code"] == "MALFORMED_RECORD" and "evidence_index" in f]
        self.assertEqual(len(malformed), 1)
        self.assertEqual(result.evidence_types, [])

    def test_evidence_type_not_a_string(self):
        findings = []
        result = m.process_record(0, rec(events=[], evidence=[{"evidence_type": 5}]), findings)
        malformed = [f for f in findings if f["code"] == "MALFORMED_RECORD" and "evidence_index" in f]
        self.assertEqual(len(malformed), 1)

    def test_evidence_type_empty_string_is_counted(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[ev("proposed", "2026-08-01T00:00:00Z")], evidence=[{"evidence_type": ""}]),
            findings,
        )
        self.assertEqual(result.evidence_types, [""])
        self.assertEqual(findings, [])

    def test_evidence_type_unicode_counted(self):
        findings = []
        result = m.process_record(
            0, rec(events=[], evidence=[{"evidence_type": "日志"}]), findings
        )
        self.assertEqual(result.evidence_types, ["日志"])

    def test_multiple_evidence_items_all_counted(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[], evidence=[{"evidence_type": "log"}, {"evidence_type": "screenshot"}, {"evidence_type": "log"}]),
            findings,
        )
        self.assertEqual(result.evidence_types, ["log", "screenshot", "log"])

    def test_no_evidence_at_all(self):
        findings = []
        result = m.process_record(
            0, rec(events=[ev("proposed", "2026-08-01T00:00:00Z")], evidence=[]), findings
        )
        self.assertEqual(result.evidence_types, [])
        self.assertEqual(findings, [])

    def test_partial_bad_evidence_good_still_counted(self):
        findings = []
        result = m.process_record(
            0,
            rec(
                events=[ev("proposed", "2026-08-01T00:00:00Z")],
                evidence=[{"bad": 1}, {"evidence_type": "log"}],
            ),
            findings,
        )
        self.assertEqual(result.evidence_types, ["log"])
        self.assertEqual(len(findings), 1)


# --------------------------------------------------------------------------
# Terminal-state determination
# --------------------------------------------------------------------------


class TestTerminalStateDetermination(unittest.TestCase):
    def test_latest_rewarded_is_terminal(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[ev("submitted", "2026-08-01T00:00:00Z"), ev("rewarded", "2026-08-01T01:00:00Z")]),
            findings,
        )
        self.assertEqual(result.terminal_state, "rewarded")

    def test_latest_refused_is_terminal(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[ev("submitted", "2026-08-01T00:00:00Z"), ev("refused", "2026-08-01T01:00:00Z")]),
            findings,
        )
        self.assertEqual(result.terminal_state, "refused")

    def test_latest_in_flight_state_not_terminal(self):
        findings = []
        result = m.process_record(
            0, rec(events=[ev("submitted", "2026-08-01T00:00:00Z")]), findings
        )
        self.assertIsNone(result.terminal_state)

    def test_rewarded_then_later_non_terminal_event_not_terminal(self):
        # Non-plausible history (rewarded then proposed again) -- this tool
        # does not validate state-machine plausibility (matches loop-health
        # convention); it just takes the latest chronological state.
        findings = []
        result = m.process_record(
            0,
            rec(events=[ev("rewarded", "2026-08-01T00:00:00Z"), ev("proposed", "2026-08-01T01:00:00Z")]),
            findings,
        )
        self.assertIsNone(result.terminal_state)

    def test_out_of_chronological_input_order_still_resolved_correctly(self):
        findings = []
        result = m.process_record(
            0,
            rec(events=[ev("rewarded", "2026-08-01T05:00:00Z"), ev("submitted", "2026-08-01T00:00:00Z")]),
            findings,
        )
        self.assertEqual(result.terminal_state, "rewarded")

    def test_no_events_no_terminal_state(self):
        findings = []
        result = m.process_record(0, rec(events=[]), findings)
        self.assertIsNone(result.terminal_state)

    def test_zero_terminal_tasks_completion_rate_is_null_not_crash(self):
        data = [rec(task_id="T-1", events=[ev("proposed", "2026-08-01T00:00:00Z")])]
        report, _ = m.build_report(data, NOW, 0)
        sc = report["scorecards"][0]
        self.assertIsNone(sc["completion_rate"]["value"])
        self.assertEqual(sc["completion_rate"]["note"], "UNDEFINED_ZERO_DENOMINATOR")
        self.assertIsNone(sc["refusal_rate"]["value"])
        self.assertIsNone(sc["average_verification_rounds"]["value"])


# --------------------------------------------------------------------------
# Verification rounds
# --------------------------------------------------------------------------


class TestVerificationRounds(unittest.TestCase):
    def _rounds(self, states_times):
        findings = []
        events = [ev(s, t) for s, t in states_times]
        result = m.process_record(0, rec(events=events), findings)
        return result.verification_rounds

    def test_zero_events_zero_rounds(self):
        self.assertEqual(self._rounds([]), 0)

    def test_single_pair_one_round(self):
        r = self._rounds(
            [
                ("verification_requested", "2026-08-01T00:00:00Z"),
                ("submitted", "2026-08-01T01:00:00Z"),
            ]
        )
        self.assertEqual(r, 1)

    def test_two_rounds(self):
        r = self._rounds(
            [
                ("verification_requested", "2026-08-01T00:00:00Z"),
                ("submitted", "2026-08-01T01:00:00Z"),
                ("verification_requested", "2026-08-01T02:00:00Z"),
                ("submitted", "2026-08-01T03:00:00Z"),
            ]
        )
        self.assertEqual(r, 2)

    def test_reverse_order_not_counted(self):
        r = self._rounds(
            [
                ("submitted", "2026-08-01T00:00:00Z"),
                ("verification_requested", "2026-08-01T01:00:00Z"),
            ]
        )
        self.assertEqual(r, 0)

    def test_refused_breaks_adjacency(self):
        r = self._rounds(
            [
                ("verification_requested", "2026-08-01T00:00:00Z"),
                ("refused", "2026-08-01T01:00:00Z"),
                ("submitted", "2026-08-01T02:00:00Z"),
            ]
        )
        self.assertEqual(r, 0)

    def test_non_chronological_input_order_still_sorted(self):
        r = self._rounds(
            [
                ("submitted", "2026-08-01T01:00:00Z"),
                ("verification_requested", "2026-08-01T00:00:00Z"),
            ]
        )
        self.assertEqual(r, 1)

    def test_identical_timestamps_tiebreak_by_input_order(self):
        r = self._rounds(
            [
                ("verification_requested", "2026-08-01T00:00:00Z"),
                ("submitted", "2026-08-01T00:00:00Z"),
            ]
        )
        self.assertEqual(r, 1)


# --------------------------------------------------------------------------
# build_report -- shape and determinism
# --------------------------------------------------------------------------


class TestBuildReportShape(unittest.TestCase):
    def test_non_list_root_raises(self):
        with self.assertRaises(m.InputError):
            m.build_report({}, NOW, 5)

    def test_string_root_raises(self):
        with self.assertRaises(m.InputError):
            m.build_report("nope", NOW, 5)

    def test_number_root_raises(self):
        with self.assertRaises(m.InputError):
            m.build_report(42, NOW, 5)

    def test_empty_list_zero_records_zero_contributors(self):
        report, n = m.build_report([], NOW, 5)
        self.assertEqual(report["summary"]["total_records"], 0)
        self.assertEqual(report["summary"]["total_contributors"], 0)
        self.assertEqual(n, 0)

    def test_top_level_keys(self):
        report, _ = m.build_report([], NOW, 5)
        expected = {"generated_at", "options", "disclaimer", "summary", "scorecards", "findings"}
        self.assertEqual(set(report.keys()), expected)

    def test_generated_at_echoes_now(self):
        report, _ = m.build_report([], NOW, 5)
        self.assertEqual(report["generated_at"], m.iso_z(NOW))

    def test_options_echoes_min_tasks(self):
        report, _ = m.build_report([], NOW, 7)
        self.assertEqual(report["options"]["min_tasks"], 7)

    def test_counts_by_code_has_all_six_codes(self):
        report, _ = m.build_report([], NOW, 5)
        self.assertEqual(set(report["summary"]["counts_by_code"].keys()), set(m.ALL_CODES))

    def test_scorecards_sorted_by_contributor_only(self):
        data = [
            rec(contributor="zed", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
            rec(contributor="amy", task_id="T-2", events=[ev("refused", "2026-08-01T00:00:00Z")]),
            rec(contributor="mid", task_id="T-3", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        ids = [sc["contributor"] for sc in report["scorecards"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, ["amy", "mid", "zed"])

    def test_duplicate_task_id_across_two_contributors_both_counted(self):
        data = [
            rec(contributor="alice", task_id="T-SHARED", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
            rec(contributor="bob", task_id="T-SHARED", events=[ev("refused", "2026-08-01T00:00:00Z")]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        self.assertEqual(report["summary"]["total_contributors"], 2)
        alice = next(sc for sc in report["scorecards"] if sc["contributor"] == "alice")
        bob = next(sc for sc in report["scorecards"] if sc["contributor"] == "bob")
        self.assertEqual(alice["rewarded_tasks"], 1)
        self.assertEqual(bob["refused_tasks"], 1)

    def test_unicode_contributor_name_round_trips(self):
        data = [rec(contributor="テスト", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")])]
        report, _ = m.build_report(data, NOW, 0)
        self.assertEqual(report["scorecards"][0]["contributor"], "テスト")

    def test_total_records_counts_unattributed_too(self):
        data = [{"contributor": None, "task_id": "T-1", "events": []}]
        report, _ = m.build_report(data, NOW, 0)
        self.assertEqual(report["summary"]["total_records"], 1)
        self.assertEqual(report["summary"]["total_contributors"], 0)

    def test_findings_deterministic_field_presence(self):
        report, _ = m.build_report([{"not": "valid"}], NOW, 5)
        f = report["findings"][0]
        self.assertIn("contributor", f)
        self.assertIn("task_id", f)
        self.assertIn("code", f)
        self.assertIn("message", f)


# --------------------------------------------------------------------------
# Metric denominators: completion_rate, average_verification_rounds, refusal_rate
# --------------------------------------------------------------------------


class TestMetricDenominators(unittest.TestCase):
    def _scorecard_for(self, events_list, min_tasks=0):
        data = [
            rec(contributor="alice", task_id=f"T-{i}", events=events)
            for i, events in enumerate(events_list)
        ]
        report, _ = m.build_report(data, NOW, min_tasks)
        return report["scorecards"][0]

    def test_completion_rate_excludes_in_flight_tasks(self):
        sc = self._scorecard_for(
            [
                [ev("rewarded", "2026-08-01T00:00:00Z")],
                [ev("refused", "2026-08-01T00:00:00Z")],
                [ev("submitted", "2026-08-01T00:00:00Z")],  # in-flight, excluded
            ]
        )
        self.assertEqual(sc["terminal_tasks"], 2)
        self.assertEqual(sc["completion_rate"]["denominator"], 2)
        self.assertEqual(sc["completion_rate"]["numerator"], 1)
        self.assertEqual(sc["completion_rate"]["value"], "0.500000")

    def test_refusal_rate_same_denominator_as_completion(self):
        sc = self._scorecard_for(
            [
                [ev("rewarded", "2026-08-01T00:00:00Z")],
                [ev("refused", "2026-08-01T00:00:00Z")],
            ]
        )
        self.assertEqual(sc["refusal_rate"]["denominator"], sc["completion_rate"]["denominator"])
        self.assertEqual(sc["refusal_rate"]["value"], "0.500000")

    def test_completion_plus_refusal_numerators_sum_to_terminal(self):
        sc = self._scorecard_for(
            [
                [ev("rewarded", "2026-08-01T00:00:00Z")],
                [ev("rewarded", "2026-08-01T00:00:00Z")],
                [ev("refused", "2026-08-01T00:00:00Z")],
            ]
        )
        total = sc["completion_rate"]["numerator"] + sc["refusal_rate"]["numerator"]
        self.assertEqual(total, sc["terminal_tasks"])

    def test_average_verification_rounds_denominator_is_terminal_tasks(self):
        sc = self._scorecard_for(
            [
                [
                    ev("verification_requested", "2026-08-01T00:00:00Z"),
                    ev("submitted", "2026-08-01T01:00:00Z"),
                    ev("rewarded", "2026-08-01T02:00:00Z"),
                ],
                [ev("submitted", "2026-08-01T00:00:00Z")],  # in-flight, excluded from denominator
            ]
        )
        self.assertEqual(sc["average_verification_rounds"]["denominator"], 1)
        self.assertEqual(sc["average_verification_rounds"]["numerator"], 1)
        self.assertEqual(sc["average_verification_rounds"]["value"], "1.000000")

    def test_average_verification_rounds_mean_across_multiple_terminal_tasks(self):
        sc = self._scorecard_for(
            [
                [
                    ev("verification_requested", "2026-08-01T00:00:00Z"),
                    ev("submitted", "2026-08-01T01:00:00Z"),
                    ev("rewarded", "2026-08-01T02:00:00Z"),
                ],
                [ev("rewarded", "2026-08-01T00:00:00Z")],
            ]
        )
        self.assertEqual(sc["average_verification_rounds"]["numerator"], 1)
        self.assertEqual(sc["average_verification_rounds"]["denominator"], 2)
        self.assertEqual(sc["average_verification_rounds"]["value"], "0.500000")

    def test_empty_history_task_not_terminal_zero_rounds(self):
        sc = self._scorecard_for([[]])
        self.assertEqual(sc["terminal_tasks"], 0)
        self.assertIsNone(sc["completion_rate"]["value"])


class TestEvidenceTypeMix(unittest.TestCase):
    def test_denominator_is_total_evidence_items(self):
        data = [
            rec(
                contributor="alice",
                task_id="T-1",
                events=[ev("rewarded", "2026-08-01T00:00:00Z")],
                evidence=[{"evidence_type": "log"}, {"evidence_type": "screenshot"}, {"evidence_type": "log"}],
            )
        ]
        report, _ = m.build_report(data, NOW, 0)
        mix = report["scorecards"][0]["evidence_type_mix"]
        self.assertEqual(mix["total_evidence_items"], 3)
        by_type = {e["evidence_type"]: e for e in mix["by_type"]}
        self.assertEqual(by_type["log"]["count"], 2)
        self.assertEqual(by_type["log"]["share"]["value"], "0.666667")
        self.assertEqual(by_type["screenshot"]["count"], 1)
        self.assertEqual(by_type["screenshot"]["share"]["value"], "0.333333")

    def test_contributor_with_no_evidence_at_all(self):
        data = [rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")])]
        report, _ = m.build_report(data, NOW, 0)
        mix = report["scorecards"][0]["evidence_type_mix"]
        self.assertEqual(mix["total_evidence_items"], 0)
        self.assertEqual(mix["by_type"], [])

    def test_evidence_mix_aggregates_across_multiple_tasks(self):
        data = [
            rec(contributor="alice", task_id="T-1", events=[], evidence=[{"evidence_type": "log"}]),
            rec(contributor="alice", task_id="T-2", events=[], evidence=[{"evidence_type": "log"}]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        mix = report["scorecards"][0]["evidence_type_mix"]
        self.assertEqual(mix["total_evidence_items"], 2)
        self.assertEqual(mix["by_type"][0]["count"], 2)

    def test_evidence_mix_by_type_sorted_by_type_name(self):
        data = [
            rec(
                contributor="alice",
                task_id="T-1",
                events=[],
                evidence=[{"evidence_type": "zeta"}, {"evidence_type": "alpha"}],
            )
        ]
        report, _ = m.build_report(data, NOW, 0)
        types = [e["evidence_type"] for e in report["scorecards"][0]["evidence_type_mix"]["by_type"]]
        self.assertEqual(types, ["alpha", "zeta"])

    def test_evidence_mix_not_gated_by_min_tasks(self):
        # Few tasks (below --min-tasks) but plenty of evidence: the mix
        # itself should still be fully populated and auditable -- only the
        # three task-count-denominated rates are nulled.
        data = [
            rec(
                contributor="alice",
                task_id="T-1",
                events=[ev("rewarded", "2026-08-01T00:00:00Z")],
                evidence=[{"evidence_type": "log"}] * 10,
            )
        ]
        report, _ = m.build_report(data, NOW, 100)
        sc = report["scorecards"][0]
        self.assertIsNone(sc["completion_rate"]["value"])
        self.assertEqual(sc["evidence_type_mix"]["total_evidence_items"], 10)
        self.assertEqual(sc["evidence_type_mix"]["by_type"][0]["share"]["value"], "1.000000")

    def test_evidence_type_empty_string_gets_its_own_bucket(self):
        data = [rec(contributor="alice", task_id="T-1", events=[], evidence=[{"evidence_type": ""}])]
        report, _ = m.build_report(data, NOW, 0)
        by_type = report["scorecards"][0]["evidence_type_mix"]["by_type"]
        self.assertEqual(len(by_type), 1)
        self.assertEqual(by_type[0]["evidence_type"], "")
        self.assertEqual(by_type[0]["count"], 1)


# --------------------------------------------------------------------------
# --min-tasks / INSUFFICIENT_DATA
# --------------------------------------------------------------------------


class TestInsufficientData(unittest.TestCase):
    def _n_tasks(self, n, min_tasks):
        events = [[ev("rewarded", "2026-08-01T00:00:00Z")] for _ in range(n)]
        data = [rec(contributor="alice", task_id=f"T-{i}", events=e) for i, e in enumerate(events)]
        report, exit_relevant = m.build_report(data, NOW, min_tasks)
        return report, exit_relevant

    def test_exactly_at_min_tasks_is_sufficient(self):
        report, _ = self._n_tasks(5, 5)
        sc = report["scorecards"][0]
        self.assertTrue(sc["min_tasks_met"])
        self.assertIsNotNone(sc["completion_rate"]["value"])

    def test_one_below_min_tasks_is_insufficient(self):
        report, _ = self._n_tasks(4, 5)
        sc = report["scorecards"][0]
        self.assertFalse(sc["min_tasks_met"])
        self.assertIsNone(sc["completion_rate"]["value"])
        self.assertEqual(sc["completion_rate"]["note"], "INSUFFICIENT_DATA")

    def test_one_above_min_tasks_is_sufficient(self):
        report, _ = self._n_tasks(6, 5)
        sc = report["scorecards"][0]
        self.assertTrue(sc["min_tasks_met"])

    def test_min_tasks_zero_always_sufficient(self):
        report, _ = self._n_tasks(0, 0)
        # zero tasks means the contributor wouldn't even appear; use 1 task instead
        report, _ = self._n_tasks(1, 0)
        sc = report["scorecards"][0]
        self.assertTrue(sc["min_tasks_met"])

    def test_insufficient_data_produces_finding(self):
        report, _ = self._n_tasks(2, 5)
        codes = [f["code"] for f in report["findings"]]
        self.assertIn("INSUFFICIENT_DATA", codes)

    def test_insufficient_data_alone_does_not_set_exit_relevant_count(self):
        report, exit_relevant = self._n_tasks(2, 5)
        self.assertEqual(exit_relevant, 0)

    def test_insufficient_data_combined_with_other_finding_still_counts_other(self):
        data = [
            rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
            {"contributor": "alice", "task_id": "T-2"},  # missing events -> MALFORMED_RECORD
        ]
        report, exit_relevant = m.build_report(data, NOW, 5)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("INSUFFICIENT_DATA", codes)
        self.assertIn("MALFORMED_RECORD", codes)
        self.assertGreater(exit_relevant, 0)

    def test_evidence_numerator_denominator_still_visible_when_insufficient(self):
        # Auditability: even when the rate VALUE is nulled, the raw counts
        # backing it remain visible in numerator/denominator.
        report, _ = self._n_tasks(2, 5)
        sc = report["scorecards"][0]
        self.assertEqual(sc["completion_rate"]["numerator"], 2)
        self.assertEqual(sc["completion_rate"]["denominator"], 2)


# --------------------------------------------------------------------------
# The ethical requirement: no rank / percentile / grade / composite score
# --------------------------------------------------------------------------


class TestEthicalRequirement(unittest.TestCase):
    def _report(self):
        data = [
            rec(contributor="zed", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
            rec(contributor="amy", task_id="T-2", events=[ev("refused", "2026-08-01T00:00:00Z")]),
        ]
        report, _ = m.build_report(data, NOW, 0)
        return report

    FORBIDDEN_SUBSTRINGS = ("rank", "percentile", "grade", "composite", "score", "tier", "leaderboard")

    def test_no_forbidden_keys_anywhere_in_output(self):
        report = self._report()
        out = m.canonical_json(report)
        obj = json.loads(out)

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    lower = k.lower()
                    if lower.startswith("not_a_"):
                        # Explicit negation fields (not_a_ranking,
                        # not_a_basis_for_penalization) NAME the absence of
                        # a ranking/penalty; they do not implement one.
                        walk(v)
                        continue
                    for bad in self.FORBIDDEN_SUBSTRINGS:
                        # "scorecard"/"scorecards" itself legitimately
                        # contains "score" as a substring -- that's the
                        # noun for "the whole report," not a ranking
                        # metric, so it is explicitly allowed.
                        if bad == "score" and "scorecard" in lower:
                            continue
                        self.assertNotIn(
                            bad, lower, f"forbidden key fragment {bad!r} found in key {k!r}"
                        )
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)

    def test_disclaimer_field_present_and_machine_readable(self):
        report = self._report()
        self.assertIn("disclaimer", report)
        self.assertIsInstance(report["disclaimer"], dict)
        self.assertIn("text", report["disclaimer"])
        self.assertIn("not_a_ranking", report["disclaimer"])
        self.assertTrue(report["disclaimer"]["not_a_ranking"])
        self.assertIn("not_a_basis_for_penalization", report["disclaimer"])
        self.assertTrue(report["disclaimer"]["not_a_basis_for_penalization"])

    def test_scorecards_ordered_by_contributor_id_not_by_any_metric(self):
        report = self._report()
        ids = [sc["contributor"] for sc in report["scorecards"]]
        self.assertEqual(ids, sorted(ids))
        # amy has a lower completion_rate (0) than zed (1) in this fixture,
        # yet amy sorts first -- proof ordering tracks id, not merit.
        self.assertEqual(ids[0], "amy")

    def test_no_top_level_single_score_field(self):
        report = self._report()
        for sc in report["scorecards"]:
            self.assertNotIn("score", {k.lower() for k in sc.keys()} - {"evidence_type_mix"})


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def _data(self):
        return [
            rec(contributor="bob", task_id="T-2", events=[ev("refused", "2026-08-01T00:00:00Z")]),
            rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
        ]

    def test_repeated_calls_identical_canonical_json(self):
        d = self._data()
        r1, _ = m.build_report(d, NOW, 0)
        r2, _ = m.build_report(d, NOW, 0)
        self.assertEqual(m.canonical_json(r1), m.canonical_json(r2))

    def test_output_independent_of_input_record_order(self):
        d1 = self._data()
        d2 = list(reversed(self._data()))
        r1, _ = m.build_report(d1, NOW, 0)
        r2, _ = m.build_report(d2, NOW, 0)
        self.assertEqual(
            [sc["contributor"] for sc in r1["scorecards"]],
            [sc["contributor"] for sc in r2["scorecards"]],
        )

    def test_findings_order_independent_of_dict_key_insertion_order(self):
        rec_a = {"task_id": "T-1", "contributor": "alice", "events": []}
        rec_b = {"contributor": "alice", "events": [], "task_id": "T-1"}
        r1, _ = m.build_report([rec_a], NOW, 0)
        r2, _ = m.build_report([rec_b], NOW, 0)
        self.assertEqual(m.canonical_json(r1), m.canonical_json(r2))


# --------------------------------------------------------------------------
# No wall-clock reads
# --------------------------------------------------------------------------


class TestNoWallClockRead(unittest.TestCase):
    def test_source_has_no_forbidden_wall_clock_calls(self):
        with open(os.path.join(HERE, "scorecard.py"), encoding="utf-8") as fh:
            src = fh.read()
        forbidden = ["now" + "()", "utc" + "now", "time" + "." + "time"]
        for token in forbidden:
            self.assertNotIn(token, src, f"forbidden wall-clock token {token!r} found in source")

    def test_build_report_requires_now_argument(self):
        import inspect

        sig = inspect.signature(m.build_report)
        self.assertIn("now", sig.parameters)

    def test_now_is_only_ever_the_injected_value(self):
        custom_now = m.parse_utc_timestamp("2030-01-01T00:00:00Z")
        report, _ = m.build_report([], custom_now, 5)
        self.assertEqual(report["generated_at"], "2030-01-01T00:00:00Z")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmpfiles = []

    def tearDown(self):
        for p in self.tmpfiles:
            try:
                os.remove(p)
            except OSError:
                pass

    def _write(self, obj):
        p = write_temp_json(obj)
        self.tmpfiles.append(p)
        return p

    def test_no_args_exits_2(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_missing_input_file_arg_exits_2(self):
        code, out, err = run_cli(["--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 2)

    def test_missing_now_exits_2(self):
        p = self._write([])
        code, out, err = run_cli([p])
        self.assertEqual(code, 2)
        self.assertIn("--now", err)

    def test_invalid_now_value_exits_2(self):
        p = self._write([])
        code, out, err = run_cli([p, "--now", "not-a-date"])
        self.assertEqual(code, 2)

    def test_non_utc_now_value_exits_2(self):
        p = self._write([])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00+05:30"])
        self.assertEqual(code, 2)

    def test_nonexistent_input_file_exits_2(self):
        code, out, err = run_cli(["/nonexistent/path/does-not-exist.json", "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_not_json_input_exits_2(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            fh.write("{not valid json")
        self.tmpfiles.append(path)
        code, out, err = run_cli([path, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 2)

    def test_object_root_exits_2(self):
        p = self._write({"not": "a list"})
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 2)

    def test_empty_array_input_exits_0(self):
        p = self._write([])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 0)
        json.loads(out)

    def test_negative_min_tasks_exits_2(self):
        p = self._write([])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--min-tasks", "-1"])
        self.assertEqual(code, 2)

    def test_min_tasks_accepts_only_integers(self):
        p = self._write([])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--min-tasks", "abc"])
        self.assertEqual(code, 2)

    def test_help_flag_exits_0(self):
        code, out, err = run_cli(["-h"])
        self.assertEqual(code, 0)

    def test_output_flag_writes_file_not_stdout(self):
        p = self._write([])
        fd, outpath = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.tmpfiles.append(outpath)
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "-o", outpath])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        with open(outpath) as fh:
            json.load(fh)

    def test_output_long_flag_equivalent(self):
        p = self._write([])
        fd, outpath = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.tmpfiles.append(outpath)
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--output", outpath])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_repeated_runs_byte_identical_output_files(self):
        data = [
            rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")]),
            {"contributor": "bob", "task_id": "T-2"},
        ]
        p = self._write(data)
        fd1, out1 = tempfile.mkstemp(suffix=".json")
        os.close(fd1)
        fd2, out2 = tempfile.mkstemp(suffix=".json")
        os.close(fd2)
        self.tmpfiles.extend([out1, out2])
        run_cli([p, "--now", "2026-08-03T00:00:00Z", "-o", out1])
        run_cli([p, "--now", "2026-08-03T00:00:00Z", "-o", out2])
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_min_tasks_flag_takes_effect_end_to_end(self):
        data = [rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")])]
        p = self._write(data)
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--min-tasks", "1"])
        obj = json.loads(out)
        self.assertTrue(obj["scorecards"][0]["min_tasks_met"])
        code2, out2, err2 = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--min-tasks", "2"])
        obj2 = json.loads(out2)
        self.assertFalse(obj2["scorecards"][0]["min_tasks_met"])

    def test_clean_data_only_insufficient_data_still_exit_0(self):
        data = [rec(contributor="alice", task_id="T-1", events=[ev("rewarded", "2026-08-01T00:00:00Z")])]
        p = self._write(data)
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z", "--min-tasks", "100"])
        self.assertEqual(code, 0)
        obj = json.loads(out)
        codes = {f["code"] for f in obj["findings"]}
        self.assertEqual(codes, {"INSUFFICIENT_DATA"})

    def test_malformed_record_exits_1(self):
        p = self._write([{"contributor": "alice"}])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 1)

    def test_missing_contributor_exits_1(self):
        p = self._write([{"task_id": "T-1", "events": []}])
        code, out, err = run_cli([p, "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 1)

    def test_stderr_message_on_bad_input_file(self):
        code, out, err = run_cli(["/nonexistent.json", "--now", "2026-08-03T00:00:00Z"])
        self.assertIn("scorecard.py", err)

    def test_stderr_message_on_missing_now(self):
        p = self._write([])
        code, out, err = run_cli([p])
        self.assertNotEqual(err.strip(), "")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class TestFixtures(unittest.TestCase):
    def _load(self, name):
        with open(os.path.join(HERE, name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_clean_fixture_is_valid_json_array(self):
        data = self._load("histories_clean.json")
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_flagged_fixture_is_valid_json_array(self):
        data = self._load("histories_flagged.json")
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_clean_fixture_produces_zero_non_informational_findings(self):
        data = self._load("histories_clean.json")
        report, exit_relevant = m.build_report(data, NOW, 5)
        self.assertEqual(exit_relevant, 0)

    def test_flagged_fixture_triggers_all_five_non_informational_codes(self):
        data = self._load("histories_flagged.json")
        report, exit_relevant = m.build_report(data, NOW, 5)
        codes = {f["code"] for f in report["findings"]}
        expected = {
            "MALFORMED_RECORD",
            "INVALID_TIMESTAMP",
            "UNKNOWN_STATE",
            "EMPTY_HISTORY",
            "MISSING_CONTRIBUTOR",
        }
        self.assertTrue(expected.issubset(codes), f"missing codes: {expected - codes}")
        self.assertGreater(exit_relevant, 0)

    def test_flagged_fixture_also_triggers_insufficient_data(self):
        data = self._load("histories_flagged.json")
        report, _ = m.build_report(data, NOW, 5)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("INSUFFICIENT_DATA", codes)

    def test_clean_fixture_cli_exits_0(self):
        code, out, err = run_cli([os.path.join(HERE, "histories_clean.json"), "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 0)

    def test_flagged_fixture_cli_exits_1(self):
        code, out, err = run_cli([os.path.join(HERE, "histories_flagged.json"), "--now", "2026-08-03T00:00:00Z"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""test_snapdiff.py -- unittest suite for snapdiff.py.

Two layers of tests:
  * White-box unit tests that import snapdiff.py directly and exercise
    its functions (canonical_dumps, jsonify, parse_reward_field,
    validate_shape, validate_and_index_tasks, diff_documents, ...).
  * Black-box CLI integration tests that invoke
    `python3 snapdiff.py ...` via subprocess, matching real end-to-end
    usage including the exact commands in captured_output.txt.

Run with:
    python3 -m unittest test_snapdiff -v
"""

import decimal
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

import snapdiff

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPDIFF_PY = os.path.join(HERE, "snapdiff.py")
SNAPSHOT_BEFORE = os.path.join(HERE, "snapshot_before.json")
SNAPSHOT_AFTER_SAME = os.path.join(HERE, "snapshot_after_same.json")
SNAPSHOT_AFTER_CHANGED = os.path.join(HERE, "snapshot_after_changed.json")


def run_cli(args, cwd=None):
    """Run snapdiff.py as a subprocess, return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, SNAPDIFF_PY] + args,
        cwd=cwd or HERE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TempDirMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def write_json(self, name, obj):
        return self.write(name, json.dumps(obj))


# ==========================================================================
# Canonical JSON / jsonify
# ==========================================================================


class TestCanonicalDumps(unittest.TestCase):
    def test_sorts_keys(self):
        out = snapdiff.canonical_dumps({"b": 1, "a": 2})
        self.assertEqual(out, '{"a":2,"b":1}\n')

    def test_no_extraneous_whitespace(self):
        out = snapdiff.canonical_dumps({"a": [1, 2, 3]})
        self.assertNotIn(" ", out.rstrip("\n"))

    def test_trailing_newline_exactly_one(self):
        out = snapdiff.canonical_dumps({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_ascii_escapes_non_ascii(self):
        out = snapdiff.canonical_dumps({"title": "café"})
        self.assertNotIn("é", out)
        self.assertIn("\\u00e9", out)

    def test_ascii_escapes_cjk(self):
        out = snapdiff.canonical_dumps({"title": "京都"})
        self.assertIn("\\u4eac", out)
        self.assertIn("\\u90fd", out)

    def test_empty_object(self):
        self.assertEqual(snapdiff.canonical_dumps({}), "{}\n")

    def test_nested_key_sorting(self):
        out = snapdiff.canonical_dumps({"z": {"y": 1, "x": 2}})
        self.assertEqual(out, '{"z":{"x":2,"y":1}}\n')

    def test_list_order_preserved_not_sorted(self):
        # sort_keys only sorts dict keys, never reorders list items.
        out = snapdiff.canonical_dumps({"a": [3, 1, 2]})
        self.assertEqual(out, '{"a":[3,1,2]}\n')

    def test_deterministic_repeated_calls(self):
        obj = {"a": 1, "b": [1, 2, {"c": 3, "d": 4}]}
        self.assertEqual(snapdiff.canonical_dumps(obj), snapdiff.canonical_dumps(obj))

    def test_key_order_independent(self):
        a = {"x": 1, "y": 2}
        b = {"y": 2, "x": 1}
        self.assertEqual(snapdiff.canonical_dumps(a), snapdiff.canonical_dumps(b))


class TestJsonify(unittest.TestCase):
    def test_decimal_to_string(self):
        self.assertEqual(snapdiff.jsonify(Decimal("1.50")), "1.50")

    def test_int_passthrough(self):
        self.assertEqual(snapdiff.jsonify(5), 5)

    def test_str_passthrough(self):
        self.assertEqual(snapdiff.jsonify("hello"), "hello")

    def test_none_passthrough(self):
        self.assertIsNone(snapdiff.jsonify(None))

    def test_bool_passthrough(self):
        self.assertIs(snapdiff.jsonify(True), True)
        self.assertIs(snapdiff.jsonify(False), False)

    def test_dict_recursion(self):
        self.assertEqual(snapdiff.jsonify({"a": Decimal("2.5")}), {"a": "2.5"})

    def test_list_recursion(self):
        self.assertEqual(snapdiff.jsonify([Decimal("1"), Decimal("2")]), ["1", "2"])

    def test_nested_mixed_structure(self):
        value = {"a": [Decimal("1.1"), {"b": Decimal("2.2")}], "c": "x"}
        expected = {"a": ["1.1", {"b": "2.2"}], "c": "x"}
        self.assertEqual(snapdiff.jsonify(value), expected)

    def test_jsonify_output_is_json_serializable(self):
        value = {"reward": Decimal("123456789012345678.123456789")}
        out = snapdiff.jsonify(value)
        # Decimal must become a string, preserving full precision that a
        # float cast would silently truncate.
        self.assertEqual(out, {"reward": "123456789012345678.123456789"})
        dumped = json.dumps(out)
        self.assertEqual(json.loads(dumped), out)

    def test_empty_dict_and_list(self):
        self.assertEqual(snapdiff.jsonify({}), {})
        self.assertEqual(snapdiff.jsonify([]), [])


class TestDecimalStr(unittest.TestCase):
    def test_plain_integer_value(self):
        self.assertEqual(snapdiff.decimal_str(Decimal("42")), "42")

    def test_plain_fraction(self):
        self.assertEqual(snapdiff.decimal_str(Decimal("1.50")), "1.50")

    def test_negative_value(self):
        self.assertEqual(snapdiff.decimal_str(Decimal("-3.25")), "-3.25")

    def test_zero(self):
        self.assertEqual(snapdiff.decimal_str(Decimal("0")), "0")

    def test_never_scientific_for_tiny_value(self):
        # This is the precision-delta bug found during development: a
        # naive str(Decimal(...)) on a subtraction result like this
        # renders "1E-9" (scientific notation) instead of plain
        # fixed-point digits. decimal_str must always render fixed-point.
        d = Decimal("123456789012345678.123456790") - Decimal("123456789012345678.123456789")
        self.assertEqual(str(d), "1E-9")  # pin down the underlying behaviour
        self.assertEqual(snapdiff.decimal_str(d), "0.000000001")

    def test_never_scientific_for_huge_value(self):
        d = Decimal("123456789012345678.123456789")
        self.assertNotIn("E", snapdiff.decimal_str(d))
        self.assertNotIn("e", snapdiff.decimal_str(d))


class TestFormatSignedDelta(unittest.TestCase):
    def test_positive_gets_plus(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("5")), "+5")

    def test_negative_keeps_minus(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("-5")), "-5")

    def test_zero_gets_plus(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("0")), "+0")

    def test_tiny_positive_fixed_point(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("0.000000001")), "+0.000000001")

    def test_tiny_negative_fixed_point(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("-0.000000001")), "-0.000000001")

    def test_large_positive(self):
        self.assertEqual(snapdiff.format_signed_delta(Decimal("250.25")), "+250.25")




# ==========================================================================
# parse_reward_field -- data-driven
# ==========================================================================

_VALID_REWARD_CASES = [
    ("none_is_no_reward", None, None),
    ("zero_int", 0, Decimal("0")),
    ("positive_int", 42, Decimal("42")),
    ("large_int", 10**20, Decimal(10**20)),
    ("negative_int_allowed", -5, Decimal("-5")),
    ("decimal_from_float_literal", Decimal("1.5"), Decimal("1.5")),
    ("decimal_high_precision", Decimal("123456789012345678.123456789"), Decimal("123456789012345678.123456789")),
    ("decimal_negative", Decimal("-2.5"), Decimal("-2.5")),
    ("decimal_zero", Decimal("0.0"), Decimal("0.0")),
    ("string_integer", "10", Decimal("10")),
    ("string_decimal", "10.50", Decimal("10.50")),
    ("string_negative", "-3.2", Decimal("-3.2")),
    ("string_high_precision", "123456789012345678.123456789", Decimal("123456789012345678.123456789")),
    ("string_leading_plus", "+5", Decimal("5")),
    ("string_scientific", "1E2", Decimal("1E2")),
]

_INVALID_REWARD_CASES = [
    ("bool_true", True),
    ("bool_false", False),
    ("empty_list", []),
    ("nonempty_list", [1, 2]),
    ("empty_dict", {}),
    ("nonempty_dict", {"a": 1}),
    ("string_not_a_number", "not-a-number"),
    ("string_empty", ""),
    ("string_nan", "NaN"),
    ("string_infinity", "Infinity"),
    ("string_neg_infinity", "-Infinity"),
    ("decimal_nan", Decimal("NaN")),
    ("decimal_infinity", Decimal("Infinity")),
    ("decimal_neg_infinity", Decimal("-Infinity")),
    ("bare_finite_float", 1.5),
    ("bare_nan_float", float("nan")),
    ("bare_inf_float", float("inf")),
]


def _make_valid_reward_test(raw, expected):
    def test(self):
        result = snapdiff.parse_reward_field(raw, "before", "T1")
        if expected is None:
            self.assertIsNone(result)
        else:
            self.assertEqual(result, expected)
            self.assertIsInstance(result, Decimal)

    return test


def _make_invalid_reward_test(raw):
    def test(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.parse_reward_field(raw, "before", "T1")
        self.assertEqual(ctx.exception.code, snapdiff.INVALID_REWARD)

    return test


class TestParseRewardField(unittest.TestCase):
    pass


for _name, _raw, _expected in _VALID_REWARD_CASES:
    setattr(TestParseRewardField, "test_valid_" + _name, _make_valid_reward_test(_raw, _expected))

for _name, _raw in _INVALID_REWARD_CASES:
    setattr(TestParseRewardField, "test_invalid_" + _name, _make_invalid_reward_test(_raw))


class TestParseRewardFieldExtra(unittest.TestCase):
    def test_string_reward_equals_number_reward_same_value(self):
        a = snapdiff.parse_reward_field("42", "before", "T1")
        b = snapdiff.parse_reward_field(42, "after", "T1")
        self.assertEqual(a, b)

    def test_string_reward_with_trailing_zero_equals_shorter_form(self):
        a = snapdiff.parse_reward_field("1000.50", "before", "T1")
        b = snapdiff.parse_reward_field("1000.5", "after", "T1")
        self.assertEqual(a, b)

    def test_precision_preserved_from_decimal_source(self):
        raw = Decimal("123456789012345678.123456789")
        result = snapdiff.parse_reward_field(raw, "before", "T1")
        self.assertEqual(str(result), "123456789012345678.123456789")

    def test_tiny_precision_difference_is_not_equal(self):
        a = snapdiff.parse_reward_field(Decimal("123456789012345678.123456789"), "before", "T1")
        b = snapdiff.parse_reward_field(Decimal("123456789012345678.123456790"), "after", "T1")
        self.assertNotEqual(a, b)


# ==========================================================================
# JSON document parsing: parse_float=Decimal, NaN/Infinity rejection
# ==========================================================================


class TestParseJsonDocument(unittest.TestCase):
    def test_parses_plain_object(self):
        doc = snapdiff.parse_json_document('{"tasks":[],"summary":{}}')
        self.assertEqual(doc, {"tasks": [], "summary": {}})

    def test_float_literal_becomes_decimal(self):
        doc = snapdiff.parse_json_document('{"x": 1.5}')
        self.assertIsInstance(doc["x"], Decimal)
        self.assertEqual(doc["x"], Decimal("1.5"))

    def test_high_precision_literal_preserved_exactly(self):
        doc = snapdiff.parse_json_document('{"x": 123456789012345678.123456789}')
        self.assertEqual(str(doc["x"]), "123456789012345678.123456789")

    def test_plain_naive_float_parse_would_lose_precision(self):
        # Pin down *why* parse_float=Decimal matters: plain float()
        # parsing of the same literal silently rounds it.
        naive = json.loads('{"x": 123456789012345678.123456789}')
        self.assertNotEqual(str(naive["x"]), "123456789012345678.123456789")

    def test_integer_literal_stays_int(self):
        doc = snapdiff.parse_json_document('{"x": 5}')
        self.assertIsInstance(doc["x"], int)

    def test_nan_literal_rejected(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.parse_json_document('{"x": NaN}')
        self.assertEqual(ctx.exception.code, snapdiff.INVALID_REWARD)

    def test_infinity_literal_rejected(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.parse_json_document('{"x": Infinity}')

    def test_neg_infinity_literal_rejected(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.parse_json_document('{"x": -Infinity}')

    def test_invalid_json_syntax_raises_decode_error(self):
        with self.assertRaises(json.JSONDecodeError):
            snapdiff.parse_json_document("{not json")

    def test_empty_string_raises_decode_error(self):
        with self.assertRaises(json.JSONDecodeError):
            snapdiff.parse_json_document("")




# ==========================================================================
# validate_shape
# ==========================================================================


class TestValidateShape(unittest.TestCase):
    def test_valid_minimal_document(self):
        tasks, summary = snapdiff.validate_shape({"tasks": []}, "before")
        self.assertEqual(tasks, [])
        self.assertEqual(summary, {})

    def test_valid_with_summary(self):
        tasks, summary = snapdiff.validate_shape({"tasks": [], "summary": {"a": 1}}, "before")
        self.assertEqual(summary, {"a": 1})

    def test_top_level_not_object_list(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_shape([1, 2, 3], "before")
        self.assertEqual(ctx.exception.code, snapdiff.MALFORMED_SNAPSHOT)

    def test_top_level_not_object_string(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape("oops", "before")

    def test_top_level_not_object_none(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape(None, "before")

    def test_missing_tasks_key(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_shape({"summary": {}}, "before")
        self.assertEqual(ctx.exception.code, snapdiff.MALFORMED_SNAPSHOT)

    def test_tasks_not_a_list(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape({"tasks": "nope"}, "before")

    def test_tasks_is_dict_not_list(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape({"tasks": {}}, "before")

    def test_summary_not_a_dict(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape({"tasks": [], "summary": []}, "before")

    def test_summary_is_string_not_dict(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_shape({"tasks": [], "summary": "x"}, "before")

    def test_error_message_includes_label(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_shape([1], "after")
        self.assertIn("after", ctx.exception.message)


# ==========================================================================
# validate_and_index_tasks
# ==========================================================================


class TestValidateAndIndexTasks(unittest.TestCase):
    def test_empty_list(self):
        index = snapdiff.validate_and_index_tasks([], "before")
        self.assertEqual(index, {})

    def test_single_valid_task(self):
        index = snapdiff.validate_and_index_tasks([{"task_id": "A", "reward": 5}], "before")
        self.assertEqual(set(index), {"A"})
        self.assertEqual(index["A"]["reward"], Decimal("5"))

    def test_task_record_not_a_dict(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks([None], "before")
        self.assertEqual(ctx.exception.code, snapdiff.MALFORMED_SNAPSHOT)

    def test_task_record_is_string_not_dict(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks(["oops"], "before")

    def test_task_record_is_list_not_dict(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks([[1, 2]], "before")

    def test_missing_task_id(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks([{"reward": 5}], "before")
        self.assertEqual(ctx.exception.code, snapdiff.MALFORMED_SNAPSHOT)

    def test_null_task_id(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks([{"task_id": None}], "before")

    def test_non_string_task_id(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks([{"task_id": 123}], "before")

    def test_empty_string_task_id(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks([{"task_id": ""}], "before")

    def test_duplicate_task_id_pair(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks(
                [{"task_id": "A"}, {"task_id": "A"}], "before"
            )
        self.assertEqual(ctx.exception.code, snapdiff.DUPLICATE_TASK_ID)

    def test_duplicate_task_id_triple(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks(
                [{"task_id": "A"}, {"task_id": "B"}, {"task_id": "A"}], "before"
            )
        self.assertEqual(ctx.exception.code, snapdiff.DUPLICATE_TASK_ID)

    def test_duplicate_error_message_mentions_label_and_id(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks([{"task_id": "DUP"}, {"task_id": "DUP"}], "after")
        self.assertIn("after", ctx.exception.message)
        self.assertIn("DUP", ctx.exception.message)

    def test_evidence_wrong_type_string(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks([{"task_id": "A", "evidence": "nope"}], "before")
        self.assertEqual(ctx.exception.code, snapdiff.MALFORMED_SNAPSHOT)

    def test_evidence_wrong_type_dict(self):
        with self.assertRaises(snapdiff.SnapshotError):
            snapdiff.validate_and_index_tasks([{"task_id": "A", "evidence": {}}], "before")

    def test_evidence_null_is_allowed(self):
        index = snapdiff.validate_and_index_tasks([{"task_id": "A", "evidence": None}], "before")
        self.assertEqual(index["A"]["record"]["evidence"], None)

    def test_evidence_missing_is_allowed(self):
        index = snapdiff.validate_and_index_tasks([{"task_id": "A"}], "before")
        self.assertNotIn("evidence", index["A"]["record"])

    def test_evidence_empty_list_is_allowed(self):
        index = snapdiff.validate_and_index_tasks([{"task_id": "A", "evidence": []}], "before")
        self.assertEqual(index["A"]["record"]["evidence"], [])

    def test_invalid_reward_propagates_with_task_id(self):
        with self.assertRaises(snapdiff.SnapshotError) as ctx:
            snapdiff.validate_and_index_tasks([{"task_id": "BADREWARD", "reward": True}], "before")
        self.assertEqual(ctx.exception.code, snapdiff.INVALID_REWARD)
        self.assertIn("BADREWARD", ctx.exception.message)

    def test_reward_missing_defaults_to_none(self):
        index = snapdiff.validate_and_index_tasks([{"task_id": "A"}], "before")
        self.assertIsNone(index["A"]["reward"])

    def test_index_preserves_full_record(self):
        record = {"task_id": "A", "title": "hi", "reward": 5, "custom_field": "x"}
        index = snapdiff.validate_and_index_tasks([record], "before")
        self.assertEqual(index["A"]["record"], record)

    def test_unicode_task_title_survives(self):
        record = {"task_id": "A", "title": "café — 京都"}
        index = snapdiff.validate_and_index_tasks([record], "before")
        self.assertEqual(index["A"]["record"]["title"], "café — 京都")




# ==========================================================================
# diff_documents -- helpers
# ==========================================================================


def _index(tasks, label="before"):
    return snapdiff.validate_and_index_tasks(tasks, label)


def _diff(before_tasks, after_tasks, before_summary=None, after_summary=None, ignore=None):
    before_idx = _index(before_tasks, "before")
    after_idx = _index(after_tasks, "after")
    return snapdiff.diff_documents(
        before_idx, after_idx, before_summary or {}, after_summary or {}, set(ignore or [])
    )


def _entries_of_type(entries, type_name):
    return [e for e in entries if e["type"] == type_name]


# ==========================================================================
# diff_documents -- TASK_ADDED / TASK_REMOVED
# ==========================================================================


class TestDiffTaskAddedRemoved(unittest.TestCase):
    def test_task_added(self):
        entries = _diff([], [{"task_id": "NEW", "reward": 5}])
        added = _entries_of_type(entries, snapdiff.TASK_ADDED)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["task_id"], "NEW")
        self.assertEqual(added[0]["task"]["reward"], "5")

    def test_task_removed(self):
        entries = _diff([{"task_id": "OLD", "reward": 5}], [])
        removed = _entries_of_type(entries, snapdiff.TASK_REMOVED)
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["task_id"], "OLD")

    def test_no_added_or_removed_when_ids_match(self):
        entries = _diff([{"task_id": "A"}], [{"task_id": "A"}])
        self.assertEqual(_entries_of_type(entries, snapdiff.TASK_ADDED), [])
        self.assertEqual(_entries_of_type(entries, snapdiff.TASK_REMOVED), [])

    def test_multiple_added_sorted_by_task_id(self):
        entries = _diff([], [{"task_id": "Z"}, {"task_id": "A"}, {"task_id": "M"}])
        added = _entries_of_type(entries, snapdiff.TASK_ADDED)
        self.assertEqual([e["task_id"] for e in added], ["A", "M", "Z"])

    def test_multiple_removed_sorted_by_task_id(self):
        entries = _diff([{"task_id": "Z"}, {"task_id": "A"}], [])
        removed = _entries_of_type(entries, snapdiff.TASK_REMOVED)
        self.assertEqual([e["task_id"] for e in removed], ["A", "Z"])

    def test_task_added_task_field_is_full_record_jsonified(self):
        record = {"task_id": "NEW", "reward": Decimal("1.5"), "title": "hi"}
        entries = _diff([], [record])
        added = _entries_of_type(entries, snapdiff.TASK_ADDED)[0]
        self.assertEqual(added["task"]["reward"], "1.5")
        self.assertEqual(added["task"]["title"], "hi")

    def test_empty_both_sides_no_added_removed(self):
        entries = _diff([], [])
        self.assertEqual(entries, [])


# ==========================================================================
# diff_documents -- STATUS_TRANSITION
# ==========================================================================


class TestDiffStatusTransition(unittest.TestCase):
    def test_status_change_detected(self):
        entries = _diff(
            [{"task_id": "A", "status": "outstanding"}],
            [{"task_id": "A", "status": "in_progress"}],
        )
        transitions = _entries_of_type(entries, snapdiff.STATUS_TRANSITION)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["from"], "outstanding")
        self.assertEqual(transitions[0]["to"], "in_progress")

    def test_status_unchanged_no_entry(self):
        entries = _diff(
            [{"task_id": "A", "status": "outstanding"}],
            [{"task_id": "A", "status": "outstanding"}],
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.STATUS_TRANSITION), [])

    def test_status_missing_to_present_is_a_transition(self):
        entries = _diff([{"task_id": "A"}], [{"task_id": "A", "status": "outstanding"}])
        transitions = _entries_of_type(entries, snapdiff.STATUS_TRANSITION)
        self.assertEqual(len(transitions), 1)
        self.assertIsNone(transitions[0]["from"])
        self.assertEqual(transitions[0]["to"], "outstanding")

    def test_status_ignored_via_ignore_flag(self):
        entries = _diff(
            [{"task_id": "A", "status": "outstanding"}],
            [{"task_id": "A", "status": "in_progress"}],
            ignore=["status"],
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.STATUS_TRANSITION), [])

    def test_status_not_leaked_into_field_changed(self):
        entries = _diff(
            [{"task_id": "A", "status": "outstanding"}],
            [{"task_id": "A", "status": "in_progress"}],
        )
        fields = [e["field"] for e in _entries_of_type(entries, snapdiff.FIELD_CHANGED)]
        self.assertNotIn("status", fields)


# ==========================================================================
# diff_documents -- REWARD_CHANGED
# ==========================================================================


class TestDiffRewardChanged(unittest.TestCase):
    def test_reward_increase(self):
        entries = _diff([{"task_id": "A", "reward": 500}], [{"task_id": "A", "reward": 750}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["from"], "500")
        self.assertEqual(changes[0]["to"], "750")
        self.assertEqual(changes[0]["delta"], "+250")

    def test_reward_decrease(self):
        entries = _diff([{"task_id": "A", "reward": 750}], [{"task_id": "A", "reward": 500}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(changes[0]["delta"], "-250")

    def test_reward_unchanged_no_entry(self):
        entries = _diff([{"task_id": "A", "reward": 100}], [{"task_id": "A", "reward": 100}])
        self.assertEqual(_entries_of_type(entries, snapdiff.REWARD_CHANGED), [])

    def test_reward_string_vs_number_same_value_no_entry(self):
        entries = _diff([{"task_id": "A", "reward": 42}], [{"task_id": "A", "reward": "42"}])
        self.assertEqual(_entries_of_type(entries, snapdiff.REWARD_CHANGED), [])

    def test_reward_string_vs_number_trailing_zero_same_value_no_entry(self):
        # Constructs the after-side value the way the real CLI would (via
        # parse_float=Decimal from JSON source text "1000.5"), so we build
        # indices directly rather than passing a bare Python float in.
        before_idx = _index([{"task_id": "A", "reward": "1000.50"}])
        after_idx = _index([{"task_id": "A", "reward": Decimal("1000.5")}])
        entries = snapdiff.diff_documents(before_idx, after_idx, {}, {}, set())
        self.assertEqual(_entries_of_type(entries, snapdiff.REWARD_CHANGED), [])

    def test_reward_null_to_value(self):
        entries = _diff([{"task_id": "A", "reward": None}], [{"task_id": "A", "reward": 50}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertIsNone(changes[0]["from"])
        self.assertEqual(changes[0]["to"], "50")
        self.assertIsNone(changes[0]["delta"])

    def test_reward_value_to_null(self):
        entries = _diff([{"task_id": "A", "reward": 50}], [{"task_id": "A", "reward": None}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(changes[0]["from"], "50")
        self.assertIsNone(changes[0]["to"])
        self.assertIsNone(changes[0]["delta"])

    def test_reward_missing_key_treated_as_null(self):
        entries = _diff([{"task_id": "A"}], [{"task_id": "A", "reward": 50}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertIsNone(changes[0]["from"])

    def test_reward_null_both_sides_no_entry(self):
        entries = _diff([{"task_id": "A", "reward": None}], [{"task_id": "A", "reward": None}])
        self.assertEqual(_entries_of_type(entries, snapdiff.REWARD_CHANGED), [])

    def test_precision_demo_detected_as_change(self):
        before_idx = _index([{"task_id": "A", "reward": Decimal("123456789012345678.123456789")}])
        after_idx = _index([{"task_id": "A", "reward": Decimal("123456789012345678.123456790")}])
        entries = snapdiff.diff_documents(before_idx, after_idx, {}, {}, set())
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["delta"], "+0.000000001")

    def test_precision_demo_would_be_invisible_to_float_diff(self):
        # The whole point of the precision requirement: a float-based
        # diff would see these two literals as numerically equal,
        # because float64 cannot distinguish them.
        a = float("123456789012345678.123456789")
        b = float("123456789012345678.123456790")
        self.assertEqual(a, b)  # pin down: floats really do collapse this

    def test_reward_ignored_via_ignore_flag(self):
        entries = _diff(
            [{"task_id": "A", "reward": 100}], [{"task_id": "A", "reward": 200}], ignore=["reward"]
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.REWARD_CHANGED), [])

    def test_reward_delta_exact_for_large_numbers(self):
        before_idx = _index([{"task_id": "A", "reward": Decimal("999999999999999999999999999")}])
        after_idx = _index([{"task_id": "A", "reward": Decimal("1000000000000000000000000000")}])
        entries = snapdiff.diff_documents(before_idx, after_idx, {}, {}, set())
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(changes[0]["delta"], "+1")

    def test_negative_reward_delta(self):
        entries = _diff([{"task_id": "A", "reward": 10}], [{"task_id": "A", "reward": 3}])
        changes = _entries_of_type(entries, snapdiff.REWARD_CHANGED)
        self.assertEqual(changes[0]["delta"], "-7")




# ==========================================================================
# diff_documents -- EVIDENCE_ADDED / EVIDENCE_REMOVED
# ==========================================================================


class TestDiffEvidence(unittest.TestCase):
    def test_evidence_added(self):
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "screenshot"}]}],
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "screenshot"}, {"id": "E2", "type": "link"}]}],
        )
        added = _entries_of_type(entries, snapdiff.EVIDENCE_ADDED)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["items"], [{"id": "E2", "type": "link"}])

    def test_evidence_removed(self):
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "log"}, {"id": "E2", "type": "doc"}]}],
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "log"}]}],
        )
        removed = _entries_of_type(entries, snapdiff.EVIDENCE_REMOVED)
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["items"], [{"id": "E2", "type": "doc"}])

    def test_evidence_reordered_not_a_change(self):
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "log"}, {"id": "E2", "type": "doc"}]}],
            [{"task_id": "A", "evidence": [{"id": "E2", "type": "doc"}, {"id": "E1", "type": "log"}]}],
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_ADDED), [])
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_REMOVED), [])

    def test_evidence_unchanged_no_entries(self):
        ev = [{"id": "E1", "type": "log"}]
        entries = _diff([{"task_id": "A", "evidence": ev}], [{"task_id": "A", "evidence": ev}])
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_ADDED), [])
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_REMOVED), [])

    def test_evidence_both_empty_no_entries(self):
        entries = _diff([{"task_id": "A", "evidence": []}], [{"task_id": "A", "evidence": []}])
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_ADDED), [])
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_REMOVED), [])

    def test_evidence_missing_treated_as_empty(self):
        entries = _diff([{"task_id": "A"}], [{"task_id": "A", "evidence": [{"id": "E1", "type": "x"}]}])
        added = _entries_of_type(entries, snapdiff.EVIDENCE_ADDED)
        self.assertEqual(len(added), 1)

    def test_evidence_type_change_same_id_is_remove_plus_add(self):
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "screenshot"}]}],
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "video"}]}],
        )
        added = _entries_of_type(entries, snapdiff.EVIDENCE_ADDED)
        removed = _entries_of_type(entries, snapdiff.EVIDENCE_REMOVED)
        self.assertEqual(added[0]["items"], [{"id": "E1", "type": "video"}])
        self.assertEqual(removed[0]["items"], [{"id": "E1", "type": "screenshot"}])

    def test_evidence_duplicate_item_collapses_as_set(self):
        # Documented limitation: evidence is compared as a SET of
        # distinct items, not a multiset. A duplicate disappearing while
        # one copy remains is not reported.
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "x"}, {"id": "E1", "type": "x"}]}],
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "x"}]}],
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_REMOVED), [])

    def test_evidence_items_sorted_deterministically(self):
        entries = _diff(
            [{"task_id": "A", "evidence": []}],
            [{"task_id": "A", "evidence": [{"id": "Z", "type": "x"}, {"id": "A", "type": "y"}]}],
        )
        added = _entries_of_type(entries, snapdiff.EVIDENCE_ADDED)[0]
        # sorted by canonical JSON of each item: {"id":"A",...} < {"id":"Z",...}
        self.assertEqual([item["id"] for item in added["items"]], ["A", "Z"])

    def test_evidence_ignored_via_ignore_flag(self):
        entries = _diff(
            [{"task_id": "A", "evidence": [{"id": "E1", "type": "x"}]}],
            [{"task_id": "A", "evidence": []}],
            ignore=["evidence"],
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.EVIDENCE_REMOVED), [])

    def test_evidence_non_dict_items_supported(self):
        entries = _diff(
            [{"task_id": "A", "evidence": ["plain-string-evidence"]}],
            [{"task_id": "A", "evidence": ["plain-string-evidence", "second"]}],
        )
        added = _entries_of_type(entries, snapdiff.EVIDENCE_ADDED)
        self.assertEqual(added[0]["items"], ["second"])


# ==========================================================================
# diff_documents -- FIELD_CHANGED
# ==========================================================================


class TestDiffFieldChanged(unittest.TestCase):
    def test_generic_field_change_detected(self):
        entries = _diff(
            [{"task_id": "A", "list": "outstanding"}], [{"task_id": "A", "list": "archived"}]
        )
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["field"], "list")
        self.assertEqual(changes[0]["from"], "outstanding")
        self.assertEqual(changes[0]["to"], "archived")

    def test_unicode_field_change_detected(self):
        entries = _diff(
            [{"task_id": "A", "title": "café"}], [{"task_id": "A", "title": "京都"}]
        )
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(changes[0]["from"], "café")
        self.assertEqual(changes[0]["to"], "京都")

    def test_unicode_title_unchanged_no_entry(self):
        entries = _diff([{"task_id": "A", "title": "café"}], [{"task_id": "A", "title": "café"}])
        self.assertEqual(_entries_of_type(entries, snapdiff.FIELD_CHANGED), [])

    def test_field_present_to_absent(self):
        entries = _diff([{"task_id": "A", "priority": "high"}], [{"task_id": "A"}])
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(changes[0]["field"], "priority")
        self.assertEqual(changes[0]["from"], "high")
        self.assertIsNone(changes[0]["to"])

    def test_field_absent_to_present(self):
        entries = _diff([{"task_id": "A"}], [{"task_id": "A", "priority": "high"}])
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(changes[0]["field"], "priority")
        self.assertIsNone(changes[0]["from"])
        self.assertEqual(changes[0]["to"], "high")

    def test_field_null_and_absent_treated_the_same(self):
        entries = _diff([{"task_id": "A", "x": None}], [{"task_id": "A"}])
        self.assertEqual(_entries_of_type(entries, snapdiff.FIELD_CHANGED), [])

    def test_no_field_changed_for_zero_diff_task(self):
        rec_a = {"task_id": "A", "title": "same", "list": "same", "created_at": "t", "deadline": "d"}
        rec_b = dict(rec_a)
        entries = _diff([rec_a], [rec_b])
        self.assertEqual(entries, [])

    def test_multiple_field_changes_all_reported(self):
        entries = _diff(
            [{"task_id": "A", "title": "old", "list": "outstanding"}],
            [{"task_id": "A", "title": "new", "list": "archived"}],
        )
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        fields = sorted(c["field"] for c in changes)
        self.assertEqual(fields, ["list", "title"])

    def test_field_change_ignored_via_ignore_flag(self):
        entries = _diff(
            [{"task_id": "A", "title": "old"}], [{"task_id": "A", "title": "new"}], ignore=["title"]
        )
        self.assertEqual(_entries_of_type(entries, snapdiff.FIELD_CHANGED), [])

    def test_ignore_nonexistent_field_is_a_harmless_noop(self):
        entries = _diff(
            [{"task_id": "A", "title": "old"}],
            [{"task_id": "A", "title": "new"}],
            ignore=["totally_bogus_field_name"],
        )
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(len(changes), 1)

    def test_nested_list_field_change_detected(self):
        entries = _diff(
            [{"task_id": "A", "tags": ["x", "y"]}], [{"task_id": "A", "tags": ["x", "z"]}]
        )
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(changes[0]["field"], "tags")

    def test_nested_list_field_reorder_is_a_change(self):
        # Unlike evidence, generic list-valued fields compare by exact
        # equality (order matters) -- only "evidence" gets set semantics.
        entries = _diff([{"task_id": "A", "tags": ["x", "y"]}], [{"task_id": "A", "tags": ["y", "x"]}])
        changes = _entries_of_type(entries, snapdiff.FIELD_CHANGED)
        self.assertEqual(len(changes), 1)


# ==========================================================================
# diff_documents -- SUMMARY_CHANGED
# ==========================================================================


class TestDiffSummaryChanged(unittest.TestCase):
    def test_summary_change_detected(self):
        entries = _diff([], [], before_summary={"a": 1}, after_summary={"a": 2})
        changes = _entries_of_type(entries, snapdiff.SUMMARY_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["from"], {"a": 1})
        self.assertEqual(changes[0]["to"], {"a": 2})
        self.assertIsNone(changes[0]["task_id"])

    def test_summary_unchanged_no_entry(self):
        entries = _diff([], [], before_summary={"a": 1}, after_summary={"a": 1})
        self.assertEqual(_entries_of_type(entries, snapdiff.SUMMARY_CHANGED), [])

    def test_summary_key_order_does_not_matter(self):
        entries = _diff([], [], before_summary={"a": 1, "b": 2}, after_summary={"b": 2, "a": 1})
        self.assertEqual(_entries_of_type(entries, snapdiff.SUMMARY_CHANGED), [])

    def test_summary_both_empty_no_entry(self):
        entries = _diff([], [], before_summary={}, after_summary={})
        self.assertEqual(_entries_of_type(entries, snapdiff.SUMMARY_CHANGED), [])

    def test_summary_ignored_via_ignore_flag(self):
        entries = _diff([], [], before_summary={"a": 1}, after_summary={"a": 2}, ignore=["summary"])
        self.assertEqual(_entries_of_type(entries, snapdiff.SUMMARY_CHANGED), [])

    def test_summary_new_bucket_added(self):
        entries = _diff([], [], before_summary={"a": 1}, after_summary={"a": 1, "b": 2})
        changes = _entries_of_type(entries, snapdiff.SUMMARY_CHANGED)
        self.assertEqual(len(changes), 1)


# ==========================================================================
# Ignore mechanics -- cross-cutting
# ==========================================================================


class TestIgnoreMechanics(unittest.TestCase):
    def test_ignore_task_id_is_noop(self):
        entries_without = _diff([{"task_id": "A"}], [{"task_id": "B"}])
        entries_with = _diff([{"task_id": "A"}], [{"task_id": "B"}], ignore=["task_id"])
        self.assertEqual(entries_without, entries_with)

    def test_multiple_ignore_flags_combine(self):
        entries = _diff(
            [{"task_id": "A", "status": "x", "reward": 1}],
            [{"task_id": "A", "status": "y", "reward": 2}],
            ignore=["status", "reward"],
        )
        self.assertEqual(entries, [])

    def test_ignoring_reduces_but_does_not_always_zero_change_count(self):
        before = [{"task_id": "A", "status": "x", "reward": 1, "title": "t1"}]
        after = [{"task_id": "A", "status": "y", "reward": 2, "title": "t2"}]
        full = _diff(before, after)
        partial = _diff(before, after, ignore=["status", "reward"])
        self.assertEqual(len(full), 3)
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial[0]["field"], "title")

    def test_ignoring_all_differing_fields_flips_to_no_changes(self):
        before = [{"task_id": "A", "status": "x", "reward": 1}]
        after = [{"task_id": "A", "status": "y", "reward": 2}]
        entries = _diff(before, after, ignore=["status", "reward"])
        self.assertEqual(entries, [])


# ==========================================================================
# Determinism / sort order
# ==========================================================================


class TestDeterminism(unittest.TestCase):
    def test_repeated_diff_calls_produce_identical_output(self):
        before = [{"task_id": "A", "status": "x"}, {"task_id": "B", "reward": 1}]
        after = [{"task_id": "A", "status": "y"}, {"task_id": "B", "reward": 2}]
        e1 = _diff(before, after)
        e2 = _diff(before, after)
        self.assertEqual(e1, e2)

    def test_input_task_order_does_not_affect_output_order(self):
        before1 = [{"task_id": "A", "status": "x"}, {"task_id": "B", "status": "x"}]
        before2 = [{"task_id": "B", "status": "x"}, {"task_id": "A", "status": "x"}]
        after = [{"task_id": "A", "status": "y"}, {"task_id": "B", "status": "y"}]
        e1 = _diff(before1, after)
        e2 = _diff(before2, after)
        self.assertEqual(e1, e2)

    def test_changes_sorted_by_type_first(self):
        before = [{"task_id": "A", "status": "x"}]
        after_extra = [{"task_id": "A", "status": "y"}, {"task_id": "B"}]
        entries = _diff(before, after_extra)
        types = [e["type"] for e in entries]
        self.assertEqual(types, sorted(types))

    def test_full_report_canonical_dumps_deterministic(self):
        before = [{"task_id": "A", "status": "x"}]
        after = [{"task_id": "A", "status": "y"}]
        entries = _diff(before, after)
        report = snapdiff.build_report(entries, 1, 1, set())
        out1 = snapdiff.canonical_dumps(report)
        out2 = snapdiff.canonical_dumps(report)
        self.assertEqual(out1, out2)

    def test_tiebreak_uses_full_entry_content(self):
        # Two STATUS_TRANSITION entries for different task_ids must sort
        # by task_id even though both share type "STATUS_TRANSITION".
        before = [{"task_id": "B", "status": "x"}, {"task_id": "A", "status": "x"}]
        after = [{"task_id": "B", "status": "y"}, {"task_id": "A", "status": "y"}]
        entries = _diff(before, after)
        task_ids = [e["task_id"] for e in entries]
        self.assertEqual(task_ids, sorted(task_ids))




# ==========================================================================
# build_report
# ==========================================================================


class TestBuildReport(unittest.TestCase):
    def test_no_changes_result(self):
        report = snapdiff.build_report([], 3, 3, set())
        self.assertEqual(report["result"], "identical")
        self.assertEqual(report["change_count"], 0)
        self.assertEqual(report["task_count_before"], 3)
        self.assertEqual(report["task_count_after"], 3)
        self.assertEqual(report["ignored_fields"], [])

    def test_with_changes_result(self):
        report = snapdiff.build_report([{"type": "TASK_ADDED"}], 1, 2, set())
        self.assertEqual(report["result"], "changed")
        self.assertEqual(report["change_count"], 1)

    def test_ignored_fields_sorted(self):
        report = snapdiff.build_report([], 0, 0, {"reward", "status", "evidence"})
        self.assertEqual(report["ignored_fields"], ["evidence", "reward", "status"])

    def test_report_contains_no_wallclock_or_path_fields(self):
        report = snapdiff.build_report([], 0, 0, set())
        text = json.dumps(report)
        for forbidden in ("time", "date", "host", "/tmp", "/home", "/sessions"):
            self.assertNotIn(forbidden, text.lower() if forbidden not in ("/tmp", "/home", "/sessions") else text)


# ==========================================================================
# CLI integration (subprocess, real end-to-end usage)
# ==========================================================================


class TestCliBasic(TempDirMixin, unittest.TestCase):
    def test_identical_snapshots_exit_0(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["change_count"], 0)
        self.assertEqual(report["result"], "identical")

    def test_changed_snapshots_exit_1(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertGreater(report["change_count"], 0)
        self.assertEqual(report["result"], "changed")

    def test_changed_snapshot_hits_every_category(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        report = json.loads(out)
        types_seen = {c["type"] for c in report["changes"]}
        expected = {
            "TASK_ADDED", "TASK_REMOVED", "STATUS_TRANSITION", "REWARD_CHANGED",
            "EVIDENCE_ADDED", "EVIDENCE_REMOVED", "FIELD_CHANGED", "SUMMARY_CHANGED",
        }
        self.assertEqual(types_seen, expected)

    def test_reward_type_flip_task_absent_from_changes(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        report = json.loads(out)
        task_ids = {c["task_id"] for c in report["changes"]}
        self.assertNotIn("TASK-REWARD-TYPEEQ", task_ids)

    def test_stable_task_absent_from_changes(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        report = json.loads(out)
        task_ids = {c["task_id"] for c in report["changes"]}
        self.assertNotIn("TASK-STABLE", task_ids)

    def test_reorder_only_task_absent_from_changes(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        report = json.loads(out)
        task_ids = {c["task_id"] for c in report["changes"]}
        self.assertNotIn("TASK-EVIDENCE-REORDER", task_ids)

    def test_output_flag_writes_file_not_stdout(self):
        out_path = self.write("r.json", "")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", out_path])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        with open(out_path, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertGreater(report["change_count"], 0)

    def test_output_flag_long_form(self):
        out_path = self.write("r.json", "")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME, "--output", out_path])
        self.assertEqual(code, 0)
        with open(out_path, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["change_count"], 0)

    def test_two_runs_byte_identical(self):
        p1 = self.write("r1.json", "")
        p2 = self.write("r2.json", "")
        run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", p1])
        run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", p2])
        with open(p1, "rb") as fh:
            b1 = fh.read()
        with open(p2, "rb") as fh:
            b2 = fh.read()
        self.assertEqual(b1, b2)
        self.assertEqual(hashlib.sha256(b1).hexdigest(), hashlib.sha256(b2).hexdigest())

    def test_no_absolute_paths_leak_into_output(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        self.assertNotIn("/tmp", out)
        self.assertNotIn("/home", out)
        self.assertNotIn("/sessions", out)
        self.assertNotIn(HERE, out)

    def test_output_ends_with_single_newline(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME])
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_ignore_reward_and_status_reduces_finding_count(self):
        code_full, out_full, _ = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        code_partial, out_partial, _ = run_cli(
            [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "--ignore", "reward", "--ignore", "status"]
        )
        full = json.loads(out_full)
        partial = json.loads(out_partial)
        self.assertLess(partial["change_count"], full["change_count"])
        self.assertEqual(code_full, 1)
        self.assertEqual(code_partial, 1)  # other categories still differ

    def test_ignore_flag_recorded_in_report(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "--ignore", "reward"])
        report = json.loads(out)
        self.assertEqual(report["ignored_fields"], ["reward"])

    def test_reversed_diff_is_exact_inverse(self):
        code_f, out_f, _ = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        code_r, out_r, _ = run_cli([SNAPSHOT_AFTER_CHANGED, SNAPSHOT_BEFORE])
        forward = json.loads(out_f)["changes"]
        reverse = json.loads(out_r)["changes"]
        self.assertEqual(len(forward), len(reverse))

        flip = {
            "TASK_ADDED": "TASK_REMOVED",
            "TASK_REMOVED": "TASK_ADDED",
            "EVIDENCE_ADDED": "EVIDENCE_REMOVED",
            "EVIDENCE_REMOVED": "EVIDENCE_ADDED",
        }

        def key(entry):
            return (entry["type"], entry["task_id"])

        reverse_by_key = {key(e): e for e in reverse}

        for entry in forward:
            expected_type = flip.get(entry["type"], entry["type"])
            rkey = (expected_type, entry["task_id"])
            self.assertIn(rkey, reverse_by_key, "no inverse entry for %r" % (entry,))
            rentry = reverse_by_key[rkey]

            if entry["type"] in ("STATUS_TRANSITION",):
                self.assertEqual(rentry["from"], entry["to"])
                self.assertEqual(rentry["to"], entry["from"])
            elif entry["type"] == "REWARD_CHANGED":
                self.assertEqual(rentry["from"], entry["to"])
                self.assertEqual(rentry["to"], entry["from"])
                if entry["delta"] is not None:
                    self.assertEqual(Decimal(rentry["delta"]), -Decimal(entry["delta"]))
                else:
                    self.assertIsNone(rentry["delta"])
            elif entry["type"] == "FIELD_CHANGED":
                self.assertEqual(rentry["from"], entry["to"])
                self.assertEqual(rentry["to"], entry["from"])
                self.assertEqual(rentry["field"], entry["field"])
            elif entry["type"] == "SUMMARY_CHANGED":
                self.assertEqual(rentry["from"], entry["to"])
                self.assertEqual(rentry["to"], entry["from"])
            elif entry["type"] in ("TASK_ADDED", "TASK_REMOVED"):
                self.assertEqual(rentry["task"], entry["task"])
            elif entry["type"] in ("EVIDENCE_ADDED", "EVIDENCE_REMOVED"):
                pass  # matched separately below

        # Evidence: forward ADDED items for a task must equal reverse
        # REMOVED items for that same task, and vice versa.
        def items_by(entries_list, type_name):
            return {e["task_id"]: e["items"] for e in entries_list if e["type"] == type_name}

        f_added = items_by(forward, "EVIDENCE_ADDED")
        f_removed = items_by(forward, "EVIDENCE_REMOVED")
        r_added = items_by(reverse, "EVIDENCE_ADDED")
        r_removed = items_by(reverse, "EVIDENCE_REMOVED")
        self.assertEqual(f_added, r_removed)
        self.assertEqual(f_removed, r_added)


class TestCliErrors(TempDirMixin, unittest.TestCase):
    def test_nonexistent_file_exit_2(self):
        code, out, err = run_cli(["/nonexistent-snapdiff-input.json", SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nonexistent", err.lower())

    def test_missing_required_arg_exit_2(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_no_args_at_all_exit_2(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)

    def test_help_exit_0(self):
        code, out, err = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())

    def test_invalid_json_syntax_exit_2(self):
        p = self.write("bad.json", "{not valid json")
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("invalid JSON", err)

    def test_top_level_not_object_exit_2(self):
        p = self.write_json("notdict.json", [1, 2, 3])
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("MALFORMED_SNAPSHOT", err)

    def test_missing_tasks_key_exit_2(self):
        p = self.write_json("notasks.json", {"summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("MALFORMED_SNAPSHOT", err)

    def test_tasks_wrong_type_exit_2(self):
        p = self.write_json("badtasks.json", {"tasks": "nope"})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_summary_wrong_type_exit_2(self):
        p = self.write_json("badsummary.json", {"tasks": [], "summary": []})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_task_not_object_exit_2(self):
        p = self.write_json("badrecord.json", {"tasks": [None], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("MALFORMED_SNAPSHOT", err)

    def test_missing_task_id_exit_2(self):
        p = self.write_json("notaskid.json", {"tasks": [{"reward": 1}], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_duplicate_task_id_in_before_exit_2(self):
        p = self.write_json(
            "dup.json", {"tasks": [{"task_id": "X"}, {"task_id": "X"}], "summary": {}}
        )
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("DUPLICATE_TASK_ID", err)

    def test_duplicate_task_id_in_after_exit_2(self):
        p = self.write_json(
            "dup.json", {"tasks": [{"task_id": "X"}, {"task_id": "X"}], "summary": {}}
        )
        code, out, err = run_cli([SNAPSHOT_BEFORE, p])
        self.assertEqual(code, 2)
        self.assertIn("DUPLICATE_TASK_ID", err)

    def test_invalid_reward_boolean_exit_2(self):
        p = self.write_json("badreward.json", {"tasks": [{"task_id": "X", "reward": True}], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_REWARD", err)

    def test_invalid_reward_string_exit_2(self):
        p = self.write_json(
            "badreward2.json", {"tasks": [{"task_id": "X", "reward": "not-a-number"}], "summary": {}}
        )
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_REWARD", err)

    def test_invalid_reward_nan_literal_exit_2(self):
        p = self.write("nanreward.json", '{"tasks":[{"task_id":"X","reward":NaN}],"summary":{}}')
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_REWARD", err)

    def test_invalid_reward_infinity_literal_exit_2(self):
        p = self.write("infreward.json", '{"tasks":[{"task_id":"X","reward":Infinity}],"summary":{}}')
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_invalid_reward_array_exit_2(self):
        p = self.write_json("arrreward.json", {"tasks": [{"task_id": "X", "reward": [1, 2]}], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_invalid_reward_object_exit_2(self):
        p = self.write_json("objreward.json", {"tasks": [{"task_id": "X", "reward": {"a": 1}}], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_evidence_wrong_type_exit_2(self):
        p = self.write_json("badev.json", {"tasks": [{"task_id": "X", "evidence": "nope"}], "summary": {}})
        code, out, err = run_cli([p, SNAPSHOT_BEFORE])
        self.assertEqual(code, 2)

    def test_output_write_failure_exit_2(self):
        # A directory path used as -o target cannot be opened for writing.
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME, "-o", self.tmpdir])
        self.assertEqual(code, 2)

    def test_error_message_on_stderr_not_stdout(self):
        code, out, err = run_cli(["/nonexistent-snapdiff-input.json", SNAPSHOT_BEFORE])
        self.assertEqual(out, "")
        self.assertNotEqual(err, "")


class TestCliEdgeCases(TempDirMixin, unittest.TestCase):
    def test_both_snapshots_empty_exit_0(self):
        p1 = self.write_json("e1.json", {"tasks": [], "summary": {}})
        p2 = self.write_json("e2.json", {"tasks": [], "summary": {}})
        code, out, err = run_cli([p1, p2])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["task_count_before"], 0)
        self.assertEqual(report["task_count_after"], 0)

    def test_diffing_file_against_itself_is_no_changes(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_BEFORE])
        self.assertEqual(code, 0)

    def test_task_present_both_zero_changes_specific_task(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        report = json.loads(out)
        # TASK-STABLE appears in both snapshots with identical content
        # and must produce exactly zero change entries for it.
        stable_entries = [c for c in report["changes"] if c.get("task_id") == "TASK-STABLE"]
        self.assertEqual(stable_entries, [])

    def test_null_reward_handled_without_crash(self):
        p1 = self.write_json("n1.json", {"tasks": [{"task_id": "A", "reward": None}], "summary": {}})
        p2 = self.write_json("n2.json", {"tasks": [{"task_id": "A", "reward": None}], "summary": {}})
        code, out, err = run_cli([p1, p2])
        self.assertEqual(code, 0)

    def test_unicode_task_titles_round_trip(self):
        p1 = self.write_json("u1.json", {"tasks": [{"task_id": "A", "title": "日本語タスク"}], "summary": {}})
        p2 = self.write_json("u2.json", {"tasks": [{"task_id": "A", "title": "日本語タスク2"}], "summary": {}})
        code, out, err = run_cli([p1, p2])
        self.assertEqual(code, 1)
        report = json.loads(out)
        change = report["changes"][0]
        self.assertEqual(change["to"], "日本語タスク2")

    def test_ignore_field_that_does_not_exist_anywhere(self):
        code, out, err = run_cli(
            [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME, "--ignore", "field_that_never_appears"]
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["ignored_fields"], ["field_that_never_appears"])

    def test_duplicate_ignore_flag_deduplicated_in_report(self):
        code, out, err = run_cli(
            [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_SAME, "--ignore", "reward", "--ignore", "reward"]
        )
        report = json.loads(out)
        self.assertEqual(report["ignored_fields"], ["reward"])

    def test_grep_no_tmp_or_sessions_paths_in_real_fixture_output(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        for token in ("/sessions", "/tmp", "/home"):
            self.assertNotIn(token, out)


# ==========================================================================
# Single-use options: -o/--output must not silently last-one-wins
# ==========================================================================
#
# argparse, contextlib and io are imported inside the tests that need
# them rather than at module scope. That is not style preference: this
# file's existing findings are recorded BY LINE NUMBER in committed
# repository-wide reports, and three more lines at the top would move
# every one of them for no reason connected to this change. Appending
# only at the end leaves every existing line where it was.
#
# Not all of the thirty tests below fail against the pre-fix source, and
# the evidence file does not claim they do. Seven of them -- a single
# -o, a single --output, no -o at all, --ignore repeated, --ignore
# repeated alongside one -o, and the usage line at a normal and at a
# narrow terminal width -- pin behaviour the pre-fix source already got
# right; they are regression guards for the repair, not proof of the
# defect. The other twenty-three fail against it.
# SINGLE_USE_FLAG_EVIDENCE.txt records the per-test verdict, not just
# the aggregate, because the aggregate is expanded by subTest (three
# methods x seven spellings) and so reads larger than the number of
# test methods involved.


# The spellings argparse accepts for a repeated -o/--output when the
# caller writes the option out in full, as (case id, argv fragment
# builder). Each must be refused with exit 2. Abbreviated long options
# are covered separately, by
# test_an_abbreviated_long_option_is_reported_by_its_full_spelling,
# because argparse resolves those to --output before the action runs
# and the reported spelling is therefore not the typed one.
REPEAT_FORMS = (
    ("short_then_short", lambda a, b: ["-o", a, "-o", b]),
    ("long_then_long", lambda a, b: ["--output", a, "--output", b]),
    ("long_then_short", lambda a, b: ["--output", a, "-o", b]),
    ("short_then_long", lambda a, b: ["-o", a, "--output", b]),
    ("equals_form", lambda a, b: ["--output=" + a, "--output=" + b]),
    ("attached_short_form", lambda a, b: ["-o" + a, "-o" + b]),
    ("separated_by_another_flag", lambda a, b: ["-o", a, "--ignore", "reward", "-o", b]),
)


def quoted(value):
    """How snapdiff.py quotes a value inside the repeated-option error."""
    return json.dumps(value, ensure_ascii=False)


class TestSingleUseOutputOption(TempDirMixin, unittest.TestCase):
    """-o/--output takes one value, so a second occurrence is refused.

    Before this guard argparse's default store action was last-one-wins:
    `-o a.json -o b.json` wrote b.json, never created a.json, and exited
    0 or 1 with nothing on stderr. A caller who believed the option
    accumulated got one of the two reports they asked for and no
    indication that the other had been dropped.
    """

    def repeat_argv(self, form, a, b):
        return [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED] + form(a, b)

    def test_every_repeat_spelling_exits_2(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        for case_id, form in REPEAT_FORMS:
            with self.subTest(case_id):
                code, out, err = run_cli(self.repeat_argv(form, a, b))
                self.assertEqual(code, 2, err)

    def test_every_repeat_spelling_names_the_option(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        for case_id, form in REPEAT_FORMS:
            with self.subTest(case_id):
                code, out, err = run_cli(self.repeat_argv(form, a, b))
                self.assertIn("-o/--output", err)

    def test_every_repeat_spelling_writes_neither_file(self):
        # The whole point: a refused invocation must not have produced
        # half of what was asked for.
        for case_id, form in REPEAT_FORMS:
            with self.subTest(case_id):
                a = os.path.join(self.tmpdir, case_id + "-first.json")
                b = os.path.join(self.tmpdir, case_id + "-second.json")
                run_cli(self.repeat_argv(form, a, b))
                self.assertFalse(os.path.exists(a))
                self.assertFalse(os.path.exists(b))

    def test_error_names_both_spellings_in_the_order_used(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        code, out, err = run_cli(
            [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "--output", a, "-o", b]
        )
        self.assertEqual(code, 2)
        # "--output <a>, then -o <b>" -- the spellings the caller typed,
        # not a canonical one, so the message points at real argv text.
        first_at = err.index("--output " + quoted(a))
        second_at = err.index("-o " + quoted(b))
        self.assertLess(first_at, second_at)

    def test_error_names_the_value_that_would_have_been_discarded(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a, "-o", b])
        self.assertIn(quoted(a), err)
        self.assertIn(quoted(b), err)

    def test_repetition_with_identical_values_is_still_refused(self):
        a = os.path.join(self.tmpdir, "same.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a, "-o", a])
        self.assertEqual(code, 2, err)
        self.assertFalse(os.path.exists(a))

    def test_three_occurrences_are_refused_at_the_second(self):
        a = os.path.join(self.tmpdir, "1.json")
        b = os.path.join(self.tmpdir, "2.json")
        c = os.path.join(self.tmpdir, "3.json")
        code, out, err = run_cli(
            [SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a, "-o", b, "-o", c]
        )
        self.assertEqual(code, 2, err)
        self.assertIn(quoted(b), err)
        self.assertNotIn(quoted(c), err)

    def test_error_goes_to_stderr_and_stdout_stays_empty(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a, "-o", b])
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_exit_code_is_the_documented_usage_error_code(self):
        a = os.path.join(self.tmpdir, "first.json")
        b = os.path.join(self.tmpdir, "second.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a, "-o", b])
        self.assertEqual(code, snapdiff.EXIT_INVALID_INPUT)

    # --- the valid cases the guard must not break ---------------------

    def test_single_short_form_still_writes_the_report(self):
        a = os.path.join(self.tmpdir, "only.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "-o", a])
        self.assertEqual(code, 1, err)
        self.assertEqual(out, "")
        self.assertTrue(os.path.exists(a))

    def test_single_long_form_still_writes_the_report(self):
        a = os.path.join(self.tmpdir, "only.json")
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED, "--output", a])
        self.assertEqual(code, 1, err)
        self.assertTrue(os.path.exists(a))

    def test_omitting_the_option_still_writes_to_stdout(self):
        code, out, err = run_cli([SNAPSHOT_BEFORE, SNAPSHOT_AFTER_CHANGED])
        self.assertEqual(code, 1, err)
        self.assertTrue(out.startswith("{"))

    def test_ignore_is_still_repeatable(self):
        code, out, err = run_cli(
            [
                SNAPSHOT_BEFORE,
                SNAPSHOT_AFTER_CHANGED,
                "--ignore",
                "reward",
                "--ignore",
                "status",
                "--ignore",
                "summary",
            ]
        )
        self.assertEqual(code, 1, err)
        report = json.loads(out)
        self.assertEqual(report["ignored_fields"], ["reward", "status", "summary"])

    def test_ignore_repeated_alongside_a_single_output_is_accepted(self):
        a = os.path.join(self.tmpdir, "only.json")
        code, out, err = run_cli(
            [
                SNAPSHOT_BEFORE,
                SNAPSHOT_AFTER_CHANGED,
                "--ignore",
                "reward",
                "-o",
                a,
                "--ignore",
                "status",
            ]
        )
        self.assertEqual(code, 1, err)
        with open(a, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["ignored_fields"], ["reward", "status"])

    def test_usage_line_is_unchanged_by_the_guard(self):
        # The committed transcript quotes this line verbatim; the guard
        # is an action swap, not a new option, so it must not move.
        #
        # argparse wraps usage to the terminal width, and COLUMNS is set
        # by plenty of CI harnesses, so the raw line is compared with its
        # whitespace collapsed. That still pins the option set, their
        # order and their metavars -- everything the guard could have
        # disturbed -- without making the suite pass or fail on the width
        # of whatever ran it.
        code, out, err = run_cli([SNAPSHOT_BEFORE])
        usage = err.split("snapdiff.py: error:")[0]
        self.assertEqual(
            " ".join(usage.split()),
            "usage: snapdiff.py [-h] [-o OUTPUT] [--ignore FIELD] before after",
        )

    def test_the_usage_line_is_pinned_at_a_narrow_terminal_too(self):
        # The guard against the previous test quietly becoming
        # width-dependent again.
        env = dict(os.environ, COLUMNS="40")
        proc = subprocess.run(
            [sys.executable, SNAPDIFF_PY, SNAPSHOT_BEFORE],
            cwd=HERE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        usage = proc.stderr.split("snapdiff.py: error:")[0]
        self.assertIn("\n", usage.strip())          # it really did wrap
        self.assertEqual(
            " ".join(usage.split()),
            "usage: snapdiff.py [-h] [-o OUTPUT] [--ignore FIELD] before after",
        )


class TestSingleUseAction(unittest.TestCase):
    """The action itself, and the structural rule that keeps it applied."""

    # argparse exposes no public accessor for a parser's actions;
    # _actions is the attribute every argparse-introspecting tool uses.
    def optional_actions(self):
        parser = snapdiff.build_parser()
        return [a for a in parser._actions if a.option_strings]

    def test_output_uses_the_single_use_action(self):
        by_dest = {a.dest: a for a in self.optional_actions()}
        self.assertIsInstance(by_dest["output"], snapdiff.SingleUse)

    def test_ignore_is_declared_repeatable(self):
        import argparse

        by_dest = {a.dest: a for a in self.optional_actions()}
        self.assertIsInstance(by_dest["ignore"], argparse._AppendAction)

    def test_every_optional_action_is_repeatable_or_single_use(self):
        # This is the guard against the defect coming back through a
        # *new* option: anything that stores a value must either say it
        # repeats, or be SingleUse. A bare store action fails here.
        import argparse

        repeatable = (
            argparse._AppendAction,
            argparse._AppendConstAction,
            argparse._CountAction,
        )
        extend = getattr(argparse, "_ExtendAction", None)
        if extend is not None:
            repeatable = repeatable + (extend,)
        for action in self.optional_actions():
            with self.subTest("/".join(action.option_strings)):
                if action.nargs == 0:
                    # --help and friends store nothing; repeating them
                    # cannot lose a value.
                    continue
                self.assertIsInstance(action, (snapdiff.SingleUse,) + repeatable)

    def test_the_helper_removes_the_marker_from_the_namespace(self):
        parser = snapdiff.build_parser()
        args = snapdiff.strip_single_use_markers(
            parser, parser.parse_args(["b.json", "a.json", "-o", "out.json"])
        )
        self.assertEqual(sorted(vars(args)), ["after", "before", "ignore", "output"])

    def test_the_helper_is_a_no_op_when_the_option_is_absent(self):
        parser = snapdiff.build_parser()
        args = snapdiff.strip_single_use_markers(
            parser, parser.parse_args(["b.json", "a.json"])
        )
        self.assertEqual(sorted(vars(args)), ["after", "before", "ignore", "output"])

    def test_the_helper_also_works_after_parse_known_args(self):
        parser = snapdiff.build_parser()
        args, extra = parser.parse_known_args(["b.json", "a.json", "-o", "x", "--zz"])
        self.assertEqual(extra, ["--zz"])
        args = snapdiff.strip_single_use_markers(parser, args)
        self.assertEqual(sorted(vars(args)), ["after", "before", "ignore", "output"])

    def test_the_marker_is_present_until_the_helper_removes_it(self):
        # The documented cost of the per-parse marker living on the
        # namespace. Named, so any caller can strip it; run() does.
        parser = snapdiff.build_parser()
        action = {a.dest: a for a in parser._actions}["output"]
        raw = parser.parse_args(["b.json", "a.json", "-o", "one.json"])
        self.assertEqual(action.marker_attribute(), "_single_use__output")
        self.assertEqual(getattr(raw, action.marker_attribute()), "-o")

    def test_build_parser_returns_a_plain_argument_parser(self):
        # Not an ArgumentParser subclass, on purpose: doc-validator's
        # CLI discovery looks for a variable assigned from a call
        # literally named ArgumentParser, and a subclass would take
        # every option in this file out of its cross-tool report.
        import argparse

        self.assertIs(type(snapdiff.build_parser()), argparse.ArgumentParser)

    # --- the marker is per-parse, not per-parser ----------------------

    def test_one_namespace_object_reused_across_parses_is_not_a_repeat(self):
        # Each parse_args call sees exactly one -o. Keeping the marker on
        # the action instead of the namespace refused the second one and
        # quoted a value that was never on that command line. Stripping
        # between parses is what run() does and what any caller reusing
        # a namespace has to do.
        import argparse

        parser = snapdiff.build_parser()
        shared = argparse.Namespace()
        first = snapdiff.strip_single_use_markers(
            parser, parser.parse_args(["b.json", "a.json", "-o", "one.json"], shared)
        )
        self.assertEqual(first.output, "one.json")
        second = snapdiff.strip_single_use_markers(
            parser, parser.parse_args(["b.json", "a.json", "-o", "two.json"], shared)
        )
        self.assertEqual(second.output, "two.json")

    def test_parse_known_args_then_parse_args_on_one_namespace(self):
        import argparse

        parser = snapdiff.build_parser()
        shared = argparse.Namespace()
        args, _ = parser.parse_known_args(
            ["b.json", "a.json", "-o", "one.json", "--zz"], shared
        )
        snapdiff.strip_single_use_markers(parser, args)
        args = parser.parse_args(["b.json", "a.json", "-o", "two.json"], shared)
        self.assertEqual(args.output, "two.json")

    def test_a_reused_namespace_that_was_not_stripped_reports_honestly(self):
        # The failure mode of forgetting the helper is a refusal, not a
        # silent overwrite. Stated as a test so it is a known shape
        # rather than a surprise.
        import argparse
        import contextlib
        import io

        parser = snapdiff.build_parser()
        shared = argparse.Namespace()
        parser.parse_args(["b.json", "a.json", "-o", "one.json"], shared)
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit) as caught:
                parser.parse_args(["b.json", "a.json", "-o", "two.json"], shared)
        self.assertEqual(caught.exception.code, 2)

    def test_one_parser_shared_across_threads_never_accepts_a_repeat(self):
        # The failure this guards against is not a crash: with the marker
        # on the action, concurrent parses overwrote each other's marker
        # and a genuine `-o A -o B` came back silently accepted -- the
        # exact defect this class exists to remove.
        import contextlib
        import io
        import threading

        parser = snapdiff.build_parser()
        accepted = []
        lock = threading.Lock()

        def hammer(spelling, tag):
            for _ in range(120):
                try:
                    args = parser.parse_args(
                        ["b.json", "a.json", spelling, tag, spelling, tag + "2"]
                    )
                except SystemExit:
                    continue
                with lock:
                    accepted.append((spelling, tag, args.output))

        pairs = [("-o", "SHORT"), ("--output", "LONG")] * 4
        threads = [threading.Thread(target=hammer, args=p) for p in pairs]
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(accepted, [])
        # And no refusal quotes another thread's argument.
        for line in captured.getvalue().splitlines():
            with self.subTest(line):
                self.assertFalse("SHORT" in line and "LONG" in line)

    def test_one_parser_shared_across_threads_accepts_every_single_use(self):
        import threading

        parser = snapdiff.build_parser()
        results = []
        lock = threading.Lock()

        def hammer(tag):
            for index in range(120):
                value = "%s-%d.json" % (tag, index)
                args = parser.parse_args(["b.json", "a.json", "-o", value])
                with lock:
                    results.append((value, args.output))

        threads = [threading.Thread(target=hammer, args=("t%d" % i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8 * 120)
        self.assertEqual([r for r in results if r[0] != r[1]], [])

    def test_a_reused_parser_still_refuses_a_repeat_on_a_later_parse(self):
        import contextlib
        import io

        parser = snapdiff.build_parser()
        parser.parse_args(["b.json", "a.json", "--output", "one.json"])
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit) as caught:
                parser.parse_args(["b.json", "a.json", "-o", "x.json", "-o", "y.json"])
        self.assertEqual(caught.exception.code, 2)
        # The spelling reported is the one used in THIS parse, not the
        # "--output" left behind by the previous one.
        self.assertIn('-o "x.json", then -o "y.json"', captured.getvalue())

    def test_a_reused_parser_still_accepts_a_single_occurrence(self):
        parser = snapdiff.build_parser()
        parser.parse_args(["b.json", "a.json", "-o", "one.json"])
        args = parser.parse_args(["b.json", "a.json", "-o", "two.json"])
        self.assertEqual(args.output, "two.json")

    # The construction check builds the action directly instead of going
    # through parser.add_argument. That is the same constructor call
    # add_argument would make with this keyword, and it keeps another
    # `action=<a name>` call site out of doc-validator/optioncheck.py's
    # repository-wide scan, which can only resolve literal `action=`
    # strings and files every other form as an unsupported dynamic usage.
    def test_nargs_zero_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            snapdiff.SingleUse(["--thing"], "thing", nargs=0)

    # --- presets: a value already in the namespace is not an occurrence -

    def test_a_pre_populated_namespace_does_not_count_as_an_occurrence(self):
        import argparse

        parser = snapdiff.build_parser()
        preset = argparse.Namespace(output="PRESET")
        args = parser.parse_args(["b.json", "a.json", "-o", "real.json"], preset)
        self.assertEqual(args.output, "real.json")

    def test_set_defaults_does_not_count_as_an_occurrence(self):
        parser = snapdiff.build_parser()
        parser.set_defaults(output="PRESET")
        args = parser.parse_args(["b.json", "a.json", "-o", "real.json"])
        self.assertEqual(args.output, "real.json")

    def test_a_preset_does_not_leak_into_the_repeat_message(self):
        # The value-based probe this replaced reported the preset as the
        # first occurrence and printed the word None where a spelling
        # belongs.
        import argparse
        import contextlib
        import io

        parser = snapdiff.build_parser()
        preset = argparse.Namespace(output="PRESET")
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit) as caught:
                parser.parse_args(["b.json", "a.json", "-o", "x.json", "-o", "y.json"],
                                  preset)
        self.assertEqual(caught.exception.code, 2)
        message = captured.getvalue()
        self.assertIn('-o "x.json", then -o "y.json"', message)
        self.assertNotIn("None", message)
        self.assertNotIn("PRESET", message)

    def test_an_option_with_a_non_none_default_still_refuses_repeats(self):
        import argparse
        import contextlib
        import io

        parser = argparse.ArgumentParser(prog="t")
        parser.add_argument("--thing", action=snapdiff.SingleUse, default="fallback")
        self.assertEqual(parser.parse_args([]).thing, "fallback")
        self.assertEqual(parser.parse_args(["--thing", "one"]).thing, "one")
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--thing", "one", "--thing", "two"])
        self.assertIn('--thing "one", then --thing "two"', captured.getvalue())

    def test_an_abbreviated_long_option_is_reported_by_its_full_spelling(self):
        # argparse resolves --outp to --output before any action runs, so
        # this is the one case where the message does not echo the
        # caller's literal text. Pinned so the README can say so.
        import contextlib
        import io

        parser = snapdiff.build_parser()
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit):
                parser.parse_args(["b.json", "a.json", "--outp", "x", "--outp", "y"])
        self.assertIn('--output "x", then --output "y"', captured.getvalue())

    def test_a_non_ascii_value_stays_readable_in_the_message(self):
        import contextlib
        import io

        # Non-ASCII in BOTH positions: the discarded value and the new
        # one are quoted by separate json.dumps calls, and each needs
        # ensure_ascii=False.
        parser = snapdiff.build_parser()
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit):
                parser.parse_args(["b.json", "a.json", "-o", "caf\u00e9.json",
                                   "-o", "\u4eac\u90fd.json"])
        message = captured.getvalue()
        self.assertIn("caf\u00e9.json", message)
        self.assertIn("\u4eac\u90fd.json", message)
        self.assertNotIn("\\u", message)

    def test_the_action_is_reusable_on_another_parser(self):
        # Nothing in SingleUse is specific to -o/--output.
        import argparse
        import contextlib
        import io

        parser = argparse.ArgumentParser(prog="t")
        parser.add_argument("-c", "--config", action=snapdiff.SingleUse)
        self.assertEqual(parser.parse_args(["-c", "one"]).config, "one")
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit):
                parser.parse_args(["-c", "one", "-c", "two"])
        self.assertIn("-c/--config", captured.getvalue())


if __name__ == "__main__":
    unittest.main()

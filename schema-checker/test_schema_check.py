#!/usr/bin/env python3
"""Unit tests for schema_check.py. Standard library only."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import schema_check as sc

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "schema_check.py")
SCHEMA = os.path.join(HERE, "schema.json")
VALID = os.path.join(HERE, "payloads_valid.json")
INVALID = os.path.join(HERE, "payloads_invalid.json")
MALFORMED = os.path.join(HERE, "schema_malformed.json")


def check(root_node, value):
    """Validate *value* against a one-node schema and return the violation list."""
    return sc.validate_payload(value, {"root": root_node})


def codes(entries):
    return [entry["code"] for entry in entries]


def pointers(entries):
    return [entry["pointer"] for entry in entries]


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


# ==========================================================================
# JSON pointer semantics (RFC 6901)
# ==========================================================================

class TestJsonPointer(unittest.TestCase):

    def test_root_pointer_is_empty_string(self):
        self.assertEqual(check({"type": "string"}, 5)[0]["pointer"], "")

    def test_child_pointer_appends_token(self):
        self.assertEqual(sc.child_pointer("", "a"), "/a")
        self.assertEqual(sc.child_pointer("/a", "b"), "/a/b")

    def test_array_index_pointer(self):
        node = {"type": "array", "items": {"type": "integer"}}
        self.assertEqual(pointers(check(node, [1, "x", 3])), ["/1"])

    def test_tilde_is_escaped_as_tilde_zero(self):
        node = {"type": "object", "properties": {"a~b": {"type": "integer"}},
                "additional_properties": False}
        self.assertEqual(pointers(check(node, {"a~b": "no"})), ["/a~0b"])

    def test_slash_is_escaped_as_tilde_one(self):
        node = {"type": "object", "required": ["a/b"]}
        self.assertEqual(pointers(check(node, {})), ["/a~1b"])

    def test_deeply_nested_pointer(self):
        node = {"type": "object", "properties": {
            "records": {"type": "array", "items": {"type": "object", "properties": {
                "cid": {"type": "string"}}}}}}
        payload = {"records": [{"cid": "a"}, {"cid": "b"}, {"cid": 3}]}
        self.assertEqual(pointers(check(node, payload)), ["/records/2/cid"])


# ==========================================================================
# type constraint
# ==========================================================================

class TestTypeConstraint(unittest.TestCase):

    def test_string_accepts_string(self):
        self.assertEqual(check({"type": "string"}, "hi"), [])

    def test_string_rejects_integer(self):
        self.assertEqual(codes(check({"type": "string"}, 1)), ["TYPE_MISMATCH"])

    def test_integer_rejects_float(self):
        self.assertEqual(codes(check({"type": "integer"}, 1.5)), ["TYPE_MISMATCH"])

    def test_number_accepts_integer_and_float(self):
        self.assertEqual(check({"type": "number"}, 3), [])
        self.assertEqual(check({"type": "number"}, 3.5), [])

    def test_boolean_is_not_an_integer(self):
        self.assertEqual(codes(check({"type": "integer"}, True)), ["TYPE_MISMATCH"])

    def test_integer_is_not_a_boolean(self):
        self.assertEqual(codes(check({"type": "boolean"}, 1)), ["TYPE_MISMATCH"])

    def test_null_type(self):
        self.assertEqual(check({"type": "null"}, None), [])
        self.assertEqual(codes(check({"type": "null"}, 0)), ["TYPE_MISMATCH"])

    def test_any_type_accepts_everything(self):
        for value in ("s", 1, 1.5, True, None, [], {}):
            self.assertEqual(check({"type": "any"}, value), [], repr(value))

    def test_union_type_accepts_either_member(self):
        node = {"type": ["string", "null"]}
        self.assertEqual(check(node, "x"), [])
        self.assertEqual(check(node, None), [])
        self.assertEqual(codes(check(node, 7)), ["TYPE_MISMATCH"])

    def test_type_mismatch_suppresses_deeper_checks(self):
        node = {"type": "string", "pattern": "^a$", "min_length": 9}
        self.assertEqual(codes(check(node, 12)), ["TYPE_MISMATCH"])


# ==========================================================================
# required keys / additional properties
# ==========================================================================

class TestObjectConstraints(unittest.TestCase):

    def test_missing_required_key(self):
        node = {"type": "object", "required": ["a"]}
        result = check(node, {})
        self.assertEqual(codes(result), ["MISSING_REQUIRED"])
        self.assertEqual(result[0]["pointer"], "/a")

    def test_multiple_missing_required_keys_all_reported(self):
        node = {"type": "object", "required": ["a", "b", "c"]}
        self.assertEqual(pointers(check(node, {"b": 1})), ["/a", "/c"])

    def test_present_required_key_passes(self):
        self.assertEqual(check({"type": "object", "required": ["a"]}, {"a": None}), [])

    def test_null_value_still_satisfies_required(self):
        node = {"type": "object", "required": ["a"], "properties": {"a": {"type": "null"}}}
        self.assertEqual(check(node, {"a": None}), [])

    def test_additional_properties_false_rejects_extra_keys(self):
        node = {"type": "object", "additional_properties": False,
                "properties": {"a": {"type": "integer"}}}
        result = check(node, {"a": 1, "zzz": 2, "bbb": 3})
        self.assertEqual(codes(result), ["UNEXPECTED_KEY", "UNEXPECTED_KEY"])
        self.assertEqual(pointers(result), ["/bbb", "/zzz"])

    def test_additional_properties_true_allows_extra_keys(self):
        node = {"type": "object", "additional_properties": True,
                "properties": {"a": {"type": "integer"}}}
        self.assertEqual(check(node, {"a": 1, "extra": "ok"}), [])

    def test_additional_properties_defaults_to_permissive(self):
        node = {"type": "object", "properties": {"a": {"type": "integer"}}}
        self.assertEqual(check(node, {"a": 1, "extra": "ok"}), [])

    def test_optional_declared_property_is_checked_when_present(self):
        node = {"type": "object", "properties": {"a": {"type": "integer"}}}
        self.assertEqual(codes(check(node, {"a": "x"})), ["TYPE_MISMATCH"])


# ==========================================================================
# pattern
# ==========================================================================

class TestPatternConstraint(unittest.TestCase):

    def test_pattern_match_passes(self):
        self.assertEqual(check({"type": "string", "pattern": "^[a-f0-9]{8}$"}, "a1b2c3d4"), [])

    def test_pattern_mismatch_reported(self):
        result = check({"type": "string", "pattern": "^[a-f0-9]{8}$"}, "ZZZ")
        self.assertEqual(codes(result), ["PATTERN_MISMATCH"])

    def test_pattern_is_unanchored_search(self):
        # documented judgement call: re.search, so an unanchored pattern matches a substring
        self.assertEqual(check({"type": "string", "pattern": "b"}, "abc"), [])

    def test_pattern_ignored_for_non_string_union_member(self):
        node = {"type": ["string", "null"], "pattern": "^a$"}
        self.assertEqual(check(node, None), [])
        self.assertEqual(codes(check(node, "b")), ["PATTERN_MISMATCH"])


# ==========================================================================
# enum
# ==========================================================================

class TestEnumConstraint(unittest.TestCase):

    def test_enum_member_passes(self):
        self.assertEqual(check({"type": "string", "enum": ["log", "ui"]}, "ui"), [])

    def test_enum_non_member_reported(self):
        self.assertEqual(codes(check({"type": "string", "enum": ["log"]}, "vid")),
                         ["ENUM_MISMATCH"])

    def test_enum_distinguishes_true_from_one(self):
        # canonical-JSON comparison, so True must not satisfy an enum of [1]
        self.assertEqual(codes(check({"type": "any", "enum": [1]}, True)), ["ENUM_MISMATCH"])
        self.assertEqual(check({"type": "any", "enum": [True]}, True), [])

    def test_enum_supports_structured_values(self):
        node = {"type": "any", "enum": [{"a": 1, "b": 2}]}
        self.assertEqual(check(node, {"b": 2, "a": 1}), [])
        self.assertEqual(codes(check(node, {"a": 1})), ["ENUM_MISMATCH"])


# ==========================================================================
# numeric bounds
# ==========================================================================

class TestNumericConstraints(unittest.TestCase):

    def test_minimum_is_inclusive(self):
        self.assertEqual(check({"type": "number", "minimum": 0}, 0), [])

    def test_below_minimum_reported(self):
        self.assertEqual(codes(check({"type": "number", "minimum": 0}, -0.2)), ["MINIMUM"])

    def test_maximum_is_inclusive(self):
        self.assertEqual(check({"type": "number", "maximum": 1}, 1), [])

    def test_above_maximum_reported(self):
        self.assertEqual(codes(check({"type": "integer", "maximum": 5}, 9)), ["MAXIMUM"])


# ==========================================================================
# string lengths
# ==========================================================================

class TestStringLengthConstraints(unittest.TestCase):

    def test_min_length_reported(self):
        self.assertEqual(codes(check({"type": "string", "min_length": 5}, "/x")), ["MIN_LENGTH"])

    def test_max_length_reported(self):
        self.assertEqual(codes(check({"type": "string", "max_length": 3}, "abcd")), ["MAX_LENGTH"])

    def test_length_bounds_inclusive(self):
        self.assertEqual(check({"type": "string", "min_length": 3, "max_length": 3}, "abc"), [])


# ==========================================================================
# arrays
# ==========================================================================

class TestArrayConstraints(unittest.TestCase):

    def test_min_items_reported(self):
        self.assertEqual(codes(check({"type": "array", "min_items": 1}, [])), ["MIN_ITEMS"])

    def test_max_items_reported(self):
        node = {"type": "array", "max_items": 2}
        self.assertEqual(codes(check(node, [1, 2, 3])), ["MAX_ITEMS"])

    def test_unique_items_reports_each_duplicate_with_index_pointer(self):
        node = {"type": "array", "unique_items": True}
        result = check(node, ["a", "a", "b", "a"])
        self.assertEqual(codes(result), ["DUPLICATE_ITEMS", "DUPLICATE_ITEMS"])
        self.assertEqual(pointers(result), ["/1", "/3"])

    def test_unique_items_uses_structural_equality(self):
        node = {"type": "array", "unique_items": True}
        self.assertEqual(codes(check(node, [{"a": 1, "b": 2}, {"b": 2, "a": 1}])),
                         ["DUPLICATE_ITEMS"])

    def test_unique_items_false_allows_duplicates(self):
        self.assertEqual(check({"type": "array", "unique_items": False}, [1, 1]), [])

    def test_items_rule_applied_to_every_element(self):
        node = {"type": "array", "items": {"type": "string", "min_length": 2}}
        self.assertEqual(pointers(check(node, ["ok", "a", "fine", "b"])), ["/1", "/3"])

    def test_array_of_objects_nested_pointers(self):
        node = {"type": "array", "items": {
            "type": "object", "required": ["cid"],
            "properties": {"cid": {"type": "string", "pattern": "^[0-9]+$"}}}}
        result = check(node, [{"cid": "1"}, {}, {"cid": "x"}])
        self.assertEqual(pointers(result), ["/1/cid", "/2/cid"])
        self.assertEqual(codes(result), ["MISSING_REQUIRED", "PATTERN_MISMATCH"])


# ==========================================================================
# nested objects
# ==========================================================================

class TestNestedObjects(unittest.TestCase):

    def test_three_level_nesting(self):
        node = {"type": "object", "properties": {"a": {"type": "object", "properties": {
            "b": {"type": "object", "properties": {"c": {"type": "integer"}}}}}}}
        self.assertEqual(pointers(check(node, {"a": {"b": {"c": "no"}}})), ["/a/b/c"])

    def test_nested_object_inside_array_inside_object(self):
        node = {"type": "object", "properties": {"records": {
            "type": "array", "items": {"type": "object", "properties": {
                "source": {"type": "object", "required": ["uri"]}}}}}}
        payload = {"records": [{"source": {"uri": "x"}}, {"source": {}}]}
        self.assertEqual(pointers(check(node, payload)), ["/records/1/source/uri"])

    def test_nested_additional_properties(self):
        node = {"type": "object", "properties": {"meta": {
            "type": "object", "additional_properties": False,
            "properties": {"region": {"type": "string"}}}}}
        self.assertEqual(pointers(check(node, {"meta": {"region": "eu", "junk": 1}})),
                         ["/meta/junk"])


# ==========================================================================
# multiple simultaneous violations
# ==========================================================================

class TestMultipleViolations(unittest.TestCase):

    def test_several_constraints_on_one_scalar(self):
        node = {"type": "string", "min_length": 5, "max_length": 20,
                "pattern": "^[0-9]+$", "enum": ["12345"]}
        result = check(node, "ab")
        self.assertEqual(codes(result), ["ENUM_MISMATCH", "MIN_LENGTH", "PATTERN_MISMATCH"])

    def test_several_constraints_on_one_array(self):
        node = {"type": "array", "min_items": 5, "unique_items": True,
                "items": {"type": "integer"}}
        result = check(node, [1, 1, "x"])
        self.assertEqual(sorted(set(codes(result))),
                         ["DUPLICATE_ITEMS", "MIN_ITEMS", "TYPE_MISMATCH"])

    def test_one_record_with_four_simultaneous_violations(self):
        with open(SCHEMA) as handle:
            schema = json.load(handle)
        with open(INVALID) as handle:
            payload = json.load(handle)
        result = sc.validate_payload(payload, schema)
        record2 = [v for v in result if v["pointer"].startswith("/records/2/")]
        self.assertEqual(len(record2), 4)
        self.assertEqual(sorted(codes(record2)),
                         ["ENUM_MISMATCH", "MISSING_REQUIRED", "TYPE_MISMATCH", "UNEXPECTED_KEY"])

    def test_invalid_fixture_exercises_every_violation_code(self):
        with open(SCHEMA) as handle:
            schema = json.load(handle)
        with open(INVALID) as handle:
            payload = json.load(handle)
        found = set(codes(sc.validate_payload(payload, schema)))
        expected = {"TYPE_MISMATCH", "MISSING_REQUIRED", "UNEXPECTED_KEY", "PATTERN_MISMATCH",
                    "ENUM_MISMATCH", "MINIMUM", "MAXIMUM", "MIN_LENGTH", "MAX_LENGTH",
                    "MIN_ITEMS", "MAX_ITEMS", "DUPLICATE_ITEMS"}
        self.assertEqual(found, expected)

    def test_different_records_violate_different_kinds(self):
        with open(SCHEMA) as handle:
            schema = json.load(handle)
        with open(INVALID) as handle:
            payload = json.load(handle)
        result = sc.validate_payload(payload, schema)
        offenders = {v["pointer"].split("/")[2] for v in result
                     if v["pointer"].startswith("/records/")}
        self.assertGreaterEqual(len(offenders), 7)


# ==========================================================================
# malformed schemas
# ==========================================================================

class TestMalformedSchemas(unittest.TestCase):

    def test_schema_document_not_an_object(self):
        errs = sc.validate_schema_document([1, 2])
        self.assertEqual(codes(errs), ["SCHEMA_NOT_OBJECT"])

    def test_schema_missing_root(self):
        self.assertIn("SCHEMA_MISSING_ROOT", codes(sc.validate_schema_document({})))

    def test_schema_unknown_top_level_keyword(self):
        errs = sc.validate_schema_document({"root": {"type": "any"}, "flavour": 1})
        self.assertEqual(codes(errs), ["SCHEMA_UNKNOWN_KEYWORD"])
        self.assertEqual(pointers(errs), ["/flavour"])

    def test_schema_node_not_an_object(self):
        errs = sc.validate_schema_document({"root": "string"})
        self.assertEqual(codes(errs), ["SCHEMA_NODE_NOT_OBJECT"])

    def test_schema_node_missing_type(self):
        errs = sc.validate_schema_document({"root": {"description": "x"}})
        self.assertEqual(codes(errs), ["SCHEMA_MISSING_TYPE"])

    def test_schema_unknown_type_name(self):
        errs = sc.validate_schema_document({"root": {"type": "strings"}})
        self.assertEqual(codes(errs), ["SCHEMA_UNKNOWN_TYPE"])
        self.assertEqual(pointers(errs), ["/root/type"])

    def test_schema_bad_type_keyword_shape(self):
        errs = sc.validate_schema_document({"root": {"type": 7}})
        self.assertEqual(codes(errs), ["SCHEMA_BAD_KEYWORD_TYPE"])

    def test_schema_empty_type_list(self):
        codes_found = codes(sc.validate_schema_document({"root": {"type": []}}))
        self.assertIn("SCHEMA_BAD_KEYWORD_TYPE", codes_found)

    def test_schema_bad_regex(self):
        errs = sc.validate_schema_document({"root": {"type": "string", "pattern": "^[a-z"}})
        self.assertEqual(codes(errs), ["SCHEMA_BAD_REGEX"])

    def test_schema_empty_enum(self):
        errs = sc.validate_schema_document({"root": {"type": "string", "enum": []}})
        self.assertEqual(codes(errs), ["SCHEMA_EMPTY_ENUM"])

    def test_schema_unknown_keyword_in_node(self):
        errs = sc.validate_schema_document({"root": {"type": "string", "colour": "red"}})
        self.assertEqual(codes(errs), ["SCHEMA_UNKNOWN_KEYWORD"])
        self.assertEqual(pointers(errs), ["/root/colour"])

    def test_schema_keyword_not_applicable_to_type(self):
        errs = sc.validate_schema_document({"root": {"type": "number", "pattern": "^x$"}})
        self.assertEqual(codes(errs), ["SCHEMA_KEYWORD_NOT_APPLICABLE"])

    def test_schema_bad_bounds(self):
        errs = sc.validate_schema_document(
            {"root": {"type": "number", "minimum": 10, "maximum": 1}})
        self.assertEqual(codes(errs), ["SCHEMA_BAD_BOUNDS"])

    def test_schema_negative_min_items(self):
        errs = sc.validate_schema_document({"root": {"type": "array", "min_items": -1}})
        self.assertEqual(codes(errs), ["SCHEMA_BAD_KEYWORD_TYPE"])

    def test_schema_non_boolean_additional_properties(self):
        errs = sc.validate_schema_document(
            {"root": {"type": "object", "additional_properties": "no"}})
        self.assertEqual(codes(errs), ["SCHEMA_BAD_KEYWORD_TYPE"])

    def test_schema_required_entry_not_a_string(self):
        errs = sc.validate_schema_document({"root": {"type": "object", "required": ["a", 7]}})
        self.assertEqual(pointers(errs), ["/root/required/1"])

    def test_schema_errors_are_found_in_nested_nodes(self):
        errs = sc.validate_schema_document({"root": {"type": "array", "items": {
            "type": "object", "properties": {"a": {"type": "nope"}}}}})
        self.assertEqual(pointers(errs), ["/root/items/properties/a/type"])

    def test_malformed_fixture_reports_many_distinct_errors(self):
        with open(MALFORMED) as handle:
            schema = json.load(handle)
        errs = sc.validate_schema_document(schema)
        self.assertGreaterEqual(len(errs), 10)
        self.assertGreaterEqual(len(set(codes(errs))), 6)

    def test_good_fixture_schema_has_no_schema_errors(self):
        with open(SCHEMA) as handle:
            schema = json.load(handle)
        self.assertEqual(sc.validate_schema_document(schema), [])


# ==========================================================================
# determinism and report shape
# ==========================================================================

class TestDeterminism(unittest.TestCase):

    def test_canonical_encoding_is_sorted_and_compact(self):
        self.assertEqual(sc.canonical({"b": 1, "a": [1, 2]}), '{"a":[1,2],"b":1}')

    def test_canonical_encoding_is_ascii_only(self):
        text = sc.canonical({"k": "café"})
        self.assertEqual(text, '{"k":"caf\\u00e9"}')
        text.encode("ascii")

    def test_render_appends_exactly_one_trailing_newline(self):
        text = sc.render(sc.empty_report("s", "p"))
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(text.count("\n"), 1)

    def test_violations_sorted_by_pointer_then_code(self):
        report = sc.build_report(SCHEMA, INVALID)
        keys = [(v["pointer"], v["code"], v["message"]) for v in report["violations"]]
        self.assertEqual(keys, sorted(keys))

    def test_repeat_runs_are_byte_identical(self):
        first = sc.render(sc.build_report(SCHEMA, INVALID))
        second = sc.render(sc.build_report(SCHEMA, INVALID))
        self.assertEqual(first, second)

    def test_summary_counts_match_violation_list(self):
        report = sc.build_report(SCHEMA, INVALID)
        self.assertEqual(sum(report["summary"].values()), report["violation_count"])
        self.assertEqual(report["violation_count"], len(report["violations"]))

    def test_report_has_stable_key_set(self):
        report = sc.build_report(SCHEMA, VALID)
        self.assertEqual(sorted(report), [
            "exit_code", "io_errors", "ok", "payload_source", "schema_errors",
            "schema_source", "status", "summary", "tool_version",
            "violation_count", "violations"])

    def test_key_order_insensitive_input_gives_same_report(self):
        with open(INVALID) as handle:
            payload = json.load(handle)
        with open(SCHEMA) as handle:
            schema = json.load(handle)
        reordered = json.loads(json.dumps(payload, sort_keys=True))
        self.assertEqual(sc.canonical(sc.validate_payload(payload, schema)),
                         sc.canonical(sc.validate_payload(reordered, schema)))


# ==========================================================================
# report / exit-code logic at the API level
# ==========================================================================

class TestBuildReport(unittest.TestCase):

    def test_valid_batch_exit_zero(self):
        report = sc.build_report(SCHEMA, VALID)
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "conform")

    def test_invalid_batch_exit_one(self):
        report = sc.build_report(SCHEMA, INVALID)
        self.assertEqual(report["exit_code"], 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "violations")
        self.assertGreater(report["violation_count"], 0)

    def test_malformed_schema_exit_two(self):
        report = sc.build_report(MALFORMED, VALID)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["status"], "error")
        self.assertGreater(len(report["schema_errors"]), 0)
        self.assertEqual(report["violations"], [])

    def test_missing_file_exit_two(self):
        report = sc.build_report(SCHEMA, os.path.join(HERE, "no_such_file.json"))
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(codes(report["io_errors"]), ["IO_ERROR"])

    def test_unparseable_json_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as handle:
                handle.write("{not json")
            report = sc.build_report(SCHEMA, bad)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(codes(report["io_errors"]), ["JSON_PARSE_ERROR"])

    def test_both_files_unreadable_reports_both(self):
        report = sc.build_report("/nope/a.json", "/nope/b.json")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(len(report["io_errors"]), 2)

    def test_schema_errors_take_priority_over_payload_violations(self):
        report = sc.build_report(MALFORMED, INVALID)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["violations"], [])


# ==========================================================================
# CLI subprocess behaviour
# ==========================================================================

class TestCli(unittest.TestCase):

    def test_cli_valid_exit_zero(self):
        rc, out, _ = run_cli(SCHEMA, VALID)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "conform")

    def test_cli_invalid_exit_one(self):
        rc, out, _ = run_cli(SCHEMA, INVALID)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["status"], "violations")

    def test_cli_malformed_schema_exit_two(self):
        rc, out, _ = run_cli(MALFORMED, VALID)
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_cli_missing_file_exit_two(self):
        rc, out, _ = run_cli(SCHEMA, os.path.join(HERE, "definitely_absent.json"))
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["io_errors"][0]["code"], "IO_ERROR")

    def test_cli_missing_arguments_exit_two(self):
        rc, _, _ = run_cli(SCHEMA)
        self.assertEqual(rc, 2)

    def test_cli_out_flag_writes_canonical_file_and_keeps_stdout_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "report.json")
            rc, out, err = run_cli(SCHEMA, INVALID, "-o", target)
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")
            self.assertIn("status=violations", err)
            with open(target, "rb") as handle:
                blob = handle.read()
        self.assertTrue(blob.endswith(b"}\n"))
        self.assertEqual(blob.count(b"\n"), 1)

    def test_cli_two_runs_produce_identical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.json")
            b = os.path.join(tmp, "b.json")
            run_cli(SCHEMA, INVALID, "-o", a)
            run_cli(SCHEMA, INVALID, "-o", b)
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read())


if __name__ == "__main__":
    unittest.main()

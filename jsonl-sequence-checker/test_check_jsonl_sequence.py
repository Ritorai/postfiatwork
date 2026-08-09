"""Tests for the JSONL sequence checker.

Run with:  python3 -m unittest test_check_jsonl_sequence -v

Standard library only. The CLI is always invoked through sys.executable, so
nothing here needs an executable bit and every committed file stays 100644.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import check_jsonl_sequence as C

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_jsonl_sequence.py")

ACCEPTED = 0
REJECTED = 1
USAGE = 2


def lines(*records):
    """Build record lines from Python objects, one JSON value per line."""
    return [json.dumps(r) for r in records]


def rec(seq, **extra):
    out = {"sequence": seq}
    out.update(extra)
    return out


def codes(report):
    return [f["code"] for f in report["findings"]]


class CheckerMixin(object):

    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONUNBUFFERED", None)
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, env=env)

    def write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def fixture(self, name):
        return os.path.join(HERE, name)


# ---------------------------------------------------------------------------
# The accepting case, and only the accepting case
# ---------------------------------------------------------------------------

class TestAcceptance(CheckerMixin, unittest.TestCase):

    def test_one_through_n_in_order_is_accepted(self):
        for n in (1, 2, 3, 10, 50):
            report = C.check(lines(*[rec(i) for i in range(1, n + 1)]))
            self.assertEqual(report["status"], "accepted", n)
            self.assertEqual(report["findings"], [], n)
            self.assertEqual(report["records"], n)

    def test_extra_keys_are_none_of_this_tools_business(self):
        report = C.check(lines(rec(1, task_id="t", note="anything"),
                               rec(2, unrelated={"deep": [1, 2, 3]})))
        self.assertEqual(report["status"], "accepted")

    def test_a_single_record_numbered_one_is_accepted(self):
        self.assertEqual(C.check(lines(rec(1)))["status"], "accepted")

    def test_a_single_record_numbered_zero_is_not(self):
        self.assertEqual(C.check(lines(rec(0)))["status"], "rejected")

    def test_negative_zero_is_zero_and_still_rejected(self):
        self.assertEqual(C.check(lines(rec(-0)))["status"], "rejected")


# ---------------------------------------------------------------------------
# Each rejection class, named separately because the brief asks for each to be
# visible rather than lumped into one "invalid" verdict
# ---------------------------------------------------------------------------

class TestRejections(CheckerMixin, unittest.TestCase):

    def test_empty_file_is_rejected_not_vacuously_accepted(self):
        report = C.check([])
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(codes(report), ["EMPTY_INPUT"])
        self.assertEqual(report["records"], 0)

    def test_a_file_of_one_newline_holds_no_records(self):
        path = self.write("\n")
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, REJECTED)
        self.assertIn("EMPTY_INPUT", proc.stdout)

    def test_a_gap_is_reported_with_the_value_that_is_absent(self):
        report = C.check(lines(rec(1), rec(2), rec(4)))
        self.assertIn("SEQUENCE_MISSING", codes(report))
        missing = [f for f in report["findings"] if f["code"] == "SEQUENCE_MISSING"]
        self.assertEqual([f["value"] for f in missing], [3])

    def test_a_duplicate_names_every_line_it_appears_on(self):
        report = C.check(lines(rec(1), rec(2), rec(2)))
        dup = [f for f in report["findings"] if f["code"] == "SEQUENCE_DUPLICATE"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["value"], 2)
        self.assertEqual(dup[0]["lines"], [2, 3])

    def test_a_swap_is_out_of_order_and_nothing_else(self):
        report = C.check(lines(rec(1), rec(3), rec(2)))
        self.assertEqual(codes(report), ["SEQUENCE_OUT_OF_ORDER"])
        self.assertEqual(report["findings"][0]["expected"], 2)
        self.assertEqual(report["findings"][0]["found"], 3)
        self.assertEqual(report["findings"][0]["line"], 2)

    def test_a_swap_reports_no_duplicate_and_no_missing(self):
        """The set is intact; only the order is wrong. Saying anything else
        would tell a reader to go looking for a value that is right there."""
        report = C.check(lines(rec(1), rec(3), rec(2)))
        self.assertNotIn("SEQUENCE_DUPLICATE", codes(report))
        self.assertNotIn("SEQUENCE_MISSING", codes(report))

    def test_malformed_json_names_the_line(self):
        report = C.check(['{"sequence": 1}', '{"sequence": 2', '{"sequence": 3}'])
        bad = [f for f in report["findings"] if f["code"] == "MALFORMED_JSON"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["line"], 2)

    def test_a_non_object_record_is_not_an_object(self):
        for value in ("[1,2]", '"a string"', "42", "null", "true"):
            report = C.check(['{"sequence": 1}', value])
            self.assertIn("RECORD_NOT_OBJECT", codes(report), value)

    def test_a_record_without_the_key_is_named(self):
        report = C.check(lines(rec(1), {"task_id": "t"}))
        bad = [f for f in report["findings"] if f["code"] == "MISSING_SEQUENCE"]
        self.assertEqual([f["line"] for f in bad], [2])

    def test_out_of_range_values_are_flagged_against_the_record_count(self):
        report = C.check(lines(rec(1), rec(99)))
        bad = [f for f in report["findings"] if f["code"] == "SEQUENCE_OUT_OF_RANGE"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["value"], 99)
        self.assertEqual(bad[0]["expected_range"], [1, 2])

    def test_blank_lines_are_malformed_not_skipped(self):
        """A checker that skips blank lines cannot tell a file with a hole in
        it from a file without one."""
        path = self.write('{"sequence": 1}\n\n{"sequence": 2}\n')
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, REJECTED)
        report = json.loads(proc.stdout)
        self.assertIn("MALFORMED_JSON", codes(report))
        self.assertEqual(report["records"], 3)


# ---------------------------------------------------------------------------
# The bool trap. This is the reason SEQUENCE_IS_BOOLEAN exists.
# ---------------------------------------------------------------------------

class TestBooleansAreNotIntegers(CheckerMixin, unittest.TestCase):

    def test_python_would_have_let_this_through(self):
        """Not a test of this tool -- a test of the premise behind it.

        If this ever fails, `bool` stopped subclassing `int` and the separate
        code is no longer earning its place.
        """
        self.assertTrue(isinstance(True, int))
        self.assertEqual(True, 1)
        self.assertEqual(False, 0)

    def test_true_on_the_first_line_does_not_pass_as_one(self):
        report = C.check(lines(rec(True), rec(2)))
        self.assertEqual(report["status"], "rejected")
        self.assertIn("SEQUENCE_IS_BOOLEAN", codes(report))

    def test_false_does_not_pass_as_zero_either(self):
        report = C.check(lines(rec(False)))
        self.assertIn("SEQUENCE_IS_BOOLEAN", codes(report))

    def test_a_boolean_is_not_counted_as_a_sequenced_record(self):
        report = C.check(lines(rec(True), rec(2)))
        self.assertEqual(report["records"], 2)
        self.assertEqual(report["sequenced_records"], 1)

    def test_a_boolean_line_is_reported_as_boolean_not_as_wrong_type(self):
        report = C.check(lines(rec(True)))
        self.assertNotIn("SEQUENCE_NOT_INTEGER", codes(report))

    def test_floats_and_strings_are_wrong_type_not_boolean(self):
        for value, kind in ((2.0, "float"), ("2", "string"), (None, "null"),
                            ([2], "array"), ({"n": 2}, "object")):
            report = C.check(lines(rec(1), rec(value)))
            bad = [f for f in report["findings"]
                   if f["code"] == "SEQUENCE_NOT_INTEGER"]
            self.assertEqual(len(bad), 1, value)
            self.assertEqual(bad[0]["found_type"], kind)

    def test_an_integer_valued_float_is_still_a_float(self):
        """2.0 == 2 in Python. JSON says one is a number with a fraction part
        and the other is not, and this tool follows JSON."""
        report = C.check(lines(rec(1), rec(2.0)))
        self.assertEqual(report["status"], "rejected")


# ---------------------------------------------------------------------------
# CLI behaviour and exit codes
# ---------------------------------------------------------------------------

class TestCli(CheckerMixin, unittest.TestCase):

    def test_valid_fixture_exits_zero(self):
        proc = self.run_cli(self.fixture("sequence_valid.jsonl"))
        self.assertEqual(proc.returncode, ACCEPTED, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "accepted")

    def test_invalid_fixture_exits_one(self):
        proc = self.run_cli(self.fixture("sequence_invalid.jsonl"))
        self.assertEqual(proc.returncode, REJECTED)
        self.assertEqual(json.loads(proc.stdout)["status"], "rejected")

    def test_missing_file_is_a_usage_error_not_a_rejection(self):
        proc = self.run_cli(os.path.join(HERE, "no_such_file.jsonl"))
        self.assertEqual(proc.returncode, USAGE)
        self.assertIn("INVALID_INPUT", proc.stderr)

    def test_a_directory_is_a_usage_error(self):
        proc = self.run_cli(HERE)
        self.assertEqual(proc.returncode, USAGE)
        self.assertIn("INVALID_INPUT", proc.stderr)

    def test_non_utf8_bytes_are_a_usage_error(self):
        path = os.path.join(tempfile.mkdtemp(prefix="jsc_"), "bad.jsonl")
        self.addCleanup(os.unlink, path)
        with open(path, "wb") as fh:
            fh.write(b'{"sequence": 1, "n": "\xff\xfe"}\n')
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, USAGE)
        self.assertIn("UTF-8", proc.stderr)

    def test_no_arguments_exits_two(self):
        self.assertEqual(self.run_cli().returncode, USAGE)


# ---------------------------------------------------------------------------
# Determinism -- the report must not depend on where or when it ran
# ---------------------------------------------------------------------------

class TestDeterminism(CheckerMixin, unittest.TestCase):

    def test_two_runs_produce_identical_bytes(self):
        first = self.run_cli(self.fixture("sequence_invalid.jsonl")).stdout
        second = self.run_cli(self.fixture("sequence_invalid.jsonl")).stdout
        self.assertEqual(first, second)

    def test_the_report_carries_no_path_or_directory(self):
        proc = self.run_cli(self.fixture("sequence_invalid.jsonl"))
        for probe in (HERE, tempfile.gettempdir(), os.getcwd(),
                      "sequence_invalid"):
            self.assertNotIn(probe, proc.stdout)

    #: The exact bytes the checker must produce for a two-record file whose
    #: sequences are 2 then 1. Written out by hand rather than captured from
    #: the tool, so this test compares the tool against a fixed expectation
    #: instead of against itself.
    SWAPPED_PAIR_REPORT = (
        '{"counts_by_code":{"SEQUENCE_OUT_OF_ORDER":1},'
        '"findings":[{"code":"SEQUENCE_OUT_OF_ORDER","expected":1,'
        '"found":2,"line":1}],'
        '"findings_total":1,"records":2,"report_version":"1.0",'
        '"sequenced_records":2,"status":"rejected"}\n'
    )

    #: Three findings on three different lines plus one whole-file finding,
    #: written out by hand. The one-finding expectation above cannot see
    #: ordering; this one can, so deleting the sort in check() turns it red.
    MULTI_FINDING_REPORT = (
        "{\"counts_by_code\":{\"SEQUENCE_DUPLICATE\":1,\"SEQUENCE_IS_BOOLEAN\":"
        "1,\"SEQUENCE_MISSING\":2,\"SEQUENCE_OUT_OF_ORDER\":1},\"findings\":[{\""
        "code\":\"SEQUENCE_IS_BOOLEAN\",\"found\":\"true\",\"line\":1},{\"code\":\"SE"
        "QUENCE_DUPLICATE\",\"line\":2,\"lines\":[2,3],\"value\":3},{\"code\":\"SEQ"
        "UENCE_OUT_OF_ORDER\",\"expected\":1,\"found\":3,\"line\":2},{\"code\":\"SE"
        "QUENCE_MISSING\",\"line\":null,\"value\":1},{\"code\":\"SEQUENCE_MISSING"
        "\",\"line\":null,\"value\":2}],\"findings_total\":5,\"records\":3,\"report"
        "_version\":\"1.0\",\"sequenced_records\":2,\"status\":\"rejected\"}"
        "\n"
    )

    def test_a_multi_finding_report_is_these_exact_bytes_in_this_order(self):
        report = C.check(lines(rec(True), rec(3), rec(3)))
        self.assertEqual(C.canonical(report), self.MULTI_FINDING_REPORT)

    def test_the_report_is_these_exact_bytes(self):
        report = C.check(lines({"sequence": 2, "task_id": "t"},
                               {"task_id": "t", "sequence": 1}))
        self.assertEqual(C.canonical(report), self.SWAPPED_PAIR_REPORT)

    def test_key_order_inside_a_record_cannot_change_those_bytes(self):
        """Same two records, keys written the other way round in the file."""
        report = C.check(lines({"task_id": "t", "sequence": 2},
                               {"sequence": 1, "task_id": "t"}))
        self.assertEqual(C.canonical(report), self.SWAPPED_PAIR_REPORT)

    def test_canonical_output_is_sorted_compact_ascii_with_one_newline(self):
        text = C.canonical({"b": 1, "a": [2, 3], "u": "\u00e9"})
        self.assertEqual(text, '{"a":[2,3],"b":1,"u":"\\u00e9"}\n')


# ---------------------------------------------------------------------------
# The fixtures and the documentation have to keep agreeing with the tool
# ---------------------------------------------------------------------------

class TestFixturesAndDocsStayHonest(CheckerMixin, unittest.TestCase):

    #: fixture -> (exit code, codes that must appear in its report)
    EXPECTED = {
        "sequence_boolean.jsonl": (REJECTED, [
            "SEQUENCE_IS_BOOLEAN",
            "SEQUENCE_MISSING",
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_duplicate.jsonl": (REJECTED, [
            "SEQUENCE_DUPLICATE",
            "SEQUENCE_MISSING",
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_empty.jsonl": (REJECTED, [
            "EMPTY_INPUT",
        ]),
        "sequence_gap.jsonl": (REJECTED, [
            "SEQUENCE_MISSING",
            "SEQUENCE_OUT_OF_ORDER",
            "SEQUENCE_OUT_OF_RANGE",
        ]),
        "sequence_invalid.jsonl": (REJECTED, [
            "MALFORMED_JSON",
            "MISSING_SEQUENCE",
            "RECORD_NOT_OBJECT",
            "SEQUENCE_DUPLICATE",
            "SEQUENCE_IS_BOOLEAN",
            "SEQUENCE_MISSING",
            "SEQUENCE_NOT_INTEGER",
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_malformed.jsonl": (REJECTED, [
            "MALFORMED_JSON",
            "SEQUENCE_MISSING",
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_missing_field.jsonl": (REJECTED, [
            "MISSING_SEQUENCE",
            "SEQUENCE_MISSING",
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_reordered.jsonl": (REJECTED, [
            "SEQUENCE_OUT_OF_ORDER",
        ]),
        "sequence_valid.jsonl": (ACCEPTED, []),
    }

    def test_every_shipped_fixture_behaves_as_documented(self):
        for name, (exit_code, expected) in sorted(self.EXPECTED.items()):
            proc = self.run_cli(self.fixture(name))
            self.assertEqual(proc.returncode, exit_code, name)
            got = set(codes(json.loads(proc.stdout)))
            self.assertEqual(got, set(expected), name)

    def test_n_is_the_line_count_not_the_parseable_record_count(self):
        """The section README.md devotes to defending this, made load-bearing.

        Counting only parseable records would shrink the expected range every
        time a line broke, so deleting a bad line could turn a file with a
        hole in it into a file that passes.
        """
        proc = self.run_cli(self.fixture("sequence_malformed.jsonl"))
        report = json.loads(proc.stdout)
        self.assertEqual(report["records"], 3)
        self.assertEqual(report["sequenced_records"], 2)
        missing = [f for f in report["findings"]
                   if f["code"] == "SEQUENCE_MISSING"]
        self.assertEqual([f["value"] for f in missing], [2])

    def test_every_fixture_named_here_exists_and_every_one_shipped_is_named(self):
        on_disk = {n for n in sorted(os.listdir(HERE)) if n.endswith(".jsonl")}
        self.assertEqual(on_disk, set(self.EXPECTED))

    def test_the_fixtures_between_them_exercise_every_code(self):
        seen = set()
        for name in self.EXPECTED:
            proc = self.run_cli(self.fixture(name))
            seen.update(codes(json.loads(proc.stdout)))
        self.assertEqual(seen, set(C.CODES),
                         "codes never produced by any fixture: %s"
                         % sorted(set(C.CODES) - seen))

    def test_the_readme_documents_exactly_the_codes_the_tool_can_emit(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        rows = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", readme, re.M))
        self.assertEqual(rows, set(C.CODES))

    def test_the_readme_quotes_the_exit_codes_the_tool_actually_uses(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for value, label in ((C.EXIT_ACCEPTED, "accepted"),
                             (C.EXIT_REJECTED, "rejected"),
                             (C.EXIT_USAGE, "usage error")):
            self.assertIn("`%d` | %s" % (value, label), readme)

    def test_the_tool_imports_only_the_standard_library(self):
        import ast
        with open(SCRIPT, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(sorted(found), ["argparse", "json", "sys"])

    def test_no_shipped_file_carries_a_shebang_or_an_executable_bit(self):
        """Every file lands at mode 100644 through GitHub's web upload, so a
        shebang would be decoration that shebang-mode counts as a finding."""
        for name in sorted(os.listdir(HERE)):
            path = os.path.join(HERE, name)
            if not os.path.isfile(path) or name.endswith(".pyc"):
                continue
            with open(path, "rb") as fh:
                self.assertNotEqual(fh.read(2), b"#!", name)
            self.assertFalse(os.stat(path).st_mode & 0o111,
                             "%s carries an executable bit" % name)


if __name__ == "__main__":
    unittest.main()

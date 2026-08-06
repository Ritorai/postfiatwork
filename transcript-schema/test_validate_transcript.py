#!/usr/bin/env python3
"""Test suite for validate_transcript.py and make_fixtures.py. Stdlib-only.

Run with:
    python3 -m unittest test_validate_transcript -v
or, from the repository root:
    python3 -m unittest discover -s transcript-schema -v

No test hardcodes a repository-wide count (number of tool directories,
number of drift findings, etc.) -- see README.md "A note on repo-wide
counts". Every test either uses a generated fixture (make_fixtures.py) or
derives an expectation from data it produced itself in the same test.
"""

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import validate_transcript as vt  # noqa: E402
import make_fixtures as mf  # noqa: E402

PY = sys.executable or "python3"
VALIDATE_PY = os.path.join(HERE, "validate_transcript.py")
DEFAULT_SCHEMA = os.path.join(HERE, "schema.json")


def run_cli(args, cwd=None):
    proc = subprocess.run([PY, VALIDATE_PY] + args, capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr


# ==========================================================================
# 1. Pattern-level tests: every regex is read from schema.json, not
#    hardcoded in validate_transcript.py. These exercise the *loaded*
#    compiled patterns, the same objects the validator itself uses.
# ==========================================================================

_SCHEMA = vt.load_schema(DEFAULT_SCHEMA)
_PATTERNS = vt.compile_patterns(_SCHEMA)

# (pattern_name, sample_line, expected_match, use_search)
_PATTERN_CASES = [
    ("header", "=== $ python3 -m unittest test_x ===", True, False),
    ("header", "===$ python3 -m unittest test_x ===", False, False),
    ("header", "=== $ cmd ===extra", False, False),
    ("header", "  === $ cmd ===", False, False),
    ("header", "=== $ cmd === ", True, False),
    ("header", "=== $  cmd ===", True, False),
    ("header", "=== $ ===", False, False),
    ("header", "=== $ python3 x.py; echo done ===", True, False),
    ("header", "", False, False),
    ("header", "not a header at all", False, False),
    # header_lookalike is intentionally a superset of the strict header
    # (the validator, not the regex, excludes lines that already matched
    # 'header' before checking this one -- see validate_transcript_text).
    ("header_lookalike", "===$ cmd===", True, False),
    ("header_lookalike", "=== $ cmd ===", True, False),
    ("header_lookalike", "====================", False, False),
    ("header_lookalike", "== $ cmd ==", True, False),
    ("header_lookalike", "plain output line", False, False),
    ("header_lookalike", "=$cmd=", False, False),
    ("exit", "exit=0", True, False),
    ("exit", "exit=-9", True, False),
    ("exit", "exit= 0", False, False),
    ("exit", "Exit=0", False, False),
    ("exit", "exit=1.5", False, False),
    ("exit", "  exit=2  ", True, False),
    ("exit", "exit=+1", False, False),
    ("exit", "exitcode=0", False, False),
    ("exit", "exit=0extra", False, False),
    ("exit_lookalike", "Exit=0", True, False),
    ("exit_lookalike", "exit: 0", True, False),
    ("exit_lookalike", "exit=0", True, False),
    ("exit_lookalike", "EXIT", True, False),
    ("exit_lookalike", "exiting cleanly now", False, False),
    ("exit_lookalike", "Exit code interpretation follows:", False, False),
    ("exit_lookalike", "the exit status was zero", False, False),
    ("ran", "Ran 12 tests in 0.5s", True, False),
    ("ran", "Ran 1 test in 0.5s", True, False),
    ("ran", "ran 12 tests in 0.5s", False, False),
    ("ran", "Ran 12 tests", False, False),
    ("ran", "Ran twelve tests in 0.5s", False, False),
    ("ran_lookalike", "ran 12 tests in 0.5s", True, False),
    ("ran_lookalike", "Ran twelve tests", True, False),
    ("ran_lookalike", "Ran 12 tests in 0.5s", True, False),
    ("ran_lookalike", "Total: 12 tests ran", False, False),
    ("verdict", "OK", True, False),
    ("verdict", "OK ", True, False),
    ("verdict", "FAILED (failures=1)", True, False),
    ("verdict", "okay", False, False),
    ("verdict", "not OK", False, False),
    ("verdict", "FAILED", True, False),
    ("test_command", "python3 -m unittest test_x", True, True),
    ("test_command", "python3 -m unittest -v test_x", True, True),
    ("test_command", "python3 thing.py", False, True),
    ("test_command", "myunittestrunner.py", False, True),
    ("test_command", "python3 -m unittest", True, True),
]


def _make_pattern_test(idx, name, line, expected, use_search):
    def test(self):
        pat = _PATTERNS[name]
        matched = bool(pat.search(line)) if use_search else bool(pat.match(line))
        self.assertEqual(matched, expected,
                          "pattern %r against %r: expected %r got %r"
                          % (name, line, expected, matched))
    test.__name__ = "test_pattern_%02d_%s" % (idx, name)
    return test


class TestPatternsFromSchema(unittest.TestCase):
    """Every case below exercises validate_transcript.compile_patterns()'s
    output for the pattern named in schema.json -- not a duplicate regex
    written in this test file."""


for _idx, (_name, _line, _expected, _search) in enumerate(_PATTERN_CASES):
    _t = _make_pattern_test(_idx, _name, _line, _expected, _search)
    setattr(TestPatternsFromSchema, _t.__name__, _t)


# ==========================================================================
# 2. Fixture-driven validation tests: one generated tree, many assertions.
# ==========================================================================

# name -> (expected_status, frozenset(expected codes))
FIXTURE_EXPECTATIONS = {
    "valid_minimal.txt": ("valid", frozenset()),
    "valid_test_record.txt": ("valid", frozenset()),
    "valid_out_of_order_records.txt": ("valid", frozenset()),
    "valid_negative_exit.txt": ("valid", frozenset()),
    "valid_multiple_test_records.txt": ("valid", frozenset()),
    "valid_crlf.txt": ("valid", frozenset()),
    "valid_bom.txt": ("valid", frozenset()),
    "valid_unicode.txt": ("valid", frozenset()),
    "valid_preamble_prose.txt": ("valid", frozenset()),
    "invalid_empty.txt": ("invalid", frozenset({"TRANSCRIPT_HAS_NO_COMMAND_RECORDS"})),
    "invalid_preamble_only.txt": ("invalid", frozenset({"TRANSCRIPT_HAS_NO_COMMAND_RECORDS"})),
    "invalid_missing_exit.txt": ("invalid", frozenset({"TRANSCRIPT_RECORD_HAS_NO_EXIT"})),
    "invalid_duplicate_exit.txt": ("valid", frozenset({"TRANSCRIPT_RECORD_DUPLICATE_EXIT"})),
    "invalid_malformed_exit_case.txt": ("invalid", frozenset(
        {"TRANSCRIPT_RECORD_EXIT_MALFORMED", "TRANSCRIPT_RECORD_HAS_NO_EXIT"})),
    "invalid_malformed_exit_float.txt": ("invalid", frozenset(
        {"TRANSCRIPT_RECORD_EXIT_MALFORMED", "TRANSCRIPT_RECORD_HAS_NO_EXIT"})),
    "invalid_header_only_malformed.txt": ("invalid", frozenset(
        {"TRANSCRIPT_HAS_NO_COMMAND_RECORDS", "TRANSCRIPT_HEADER_MALFORMED"})),
    "invalid_header_lookalike_in_body.txt": ("invalid", frozenset({"TRANSCRIPT_HEADER_MALFORMED"})),
    "invalid_missing_ran_line.txt": ("invalid", frozenset({"TRANSCRIPT_RECORD_MISSING_RAN_LINE"})),
    "invalid_missing_verdict.txt": ("invalid", frozenset({"TRANSCRIPT_RECORD_MISSING_VERDICT"})),
    "invalid_missing_both_ran_and_verdict.txt": ("invalid", frozenset(
        {"TRANSCRIPT_RECORD_MISSING_RAN_LINE", "TRANSCRIPT_RECORD_MISSING_VERDICT"})),
    "invalid_ran_line_malformed.txt": ("invalid", frozenset(
        {"TRANSCRIPT_RECORD_MISSING_RAN_LINE", "TRANSCRIPT_RECORD_RAN_LINE_MALFORMED"})),
    "invalid_verdict_before_ran.txt": ("invalid", frozenset({"TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN"})),
    "invalid_test_failure.txt": ("invalid", frozenset({"TRANSCRIPT_SHOWS_TEST_FAILURE"})),
    "invalid_test_failure_in_preamble.txt": ("invalid", frozenset({"TRANSCRIPT_SHOWS_TEST_FAILURE"})),
    "invalid_exit_twice_same_value.txt": ("valid", frozenset({"TRANSCRIPT_RECORD_DUPLICATE_EXIT"})),
    "invalid_multi_record_one_bad.txt": ("invalid", frozenset({"TRANSCRIPT_RECORD_HAS_NO_EXIT"})),
}


def _make_fixture_test(fixture_name, expected_status, expected_codes):
    def test(self):
        path = os.path.join(self.fixtures_dir, fixture_name)
        findings, stats, readable = vt.validate_file(path, self.schema, self.patterns, fixture_name)
        self.assertTrue(readable, "%s should be readable" % fixture_name)
        got_codes = frozenset(f["code"] for f in findings)
        self.assertEqual(got_codes, expected_codes,
                          "%s: expected codes %r, got %r (findings=%r)"
                          % (fixture_name, expected_codes, got_codes, findings))
        status = "invalid" if vt.any_error_severity(findings) else "valid"
        self.assertEqual(status, expected_status, "%s status mismatch" % fixture_name)
        for f in findings:
            self.assertIn("file", f)
            self.assertIn("line", f)
            self.assertIn("text", f)
            self.assertIn("code", f)
            self.assertIn("severity", f)
            self.assertIsInstance(f["line"], int)
            self.assertGreaterEqual(f["line"], 1)
    test.__name__ = "test_fixture_%s" % fixture_name.replace(".", "_").replace("-", "_")
    return test


class TestFixtureValidation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_fixtures_")
        mf.generate(cls.tmp)
        cls.fixtures_dir = cls.tmp
        cls.schema = vt.load_schema(DEFAULT_SCHEMA)
        cls.patterns = vt.compile_patterns(cls.schema)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


for _fixture_name, (_status, _codes) in FIXTURE_EXPECTATIONS.items():
    _t = _make_fixture_test(_fixture_name, _status, _codes)
    setattr(TestFixtureValidation, _t.__name__, _t)


# ==========================================================================
# 3. Named scenarios explicitly called out in the task brief.
# ==========================================================================

class TestNamedScenarios(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_named_")
        mf.generate(cls.tmp)
        cls.schema = vt.load_schema(DEFAULT_SCHEMA)
        cls.patterns = vt.compile_patterns(cls.schema)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _validate(self, name):
        path = os.path.join(self.tmp, name)
        return vt.validate_file(path, self.schema, self.patterns, name)

    def test_empty_file_has_no_command_records(self):
        findings, stats, readable = self._validate("invalid_empty.txt")
        self.assertTrue(readable)
        self.assertEqual({f["code"] for f in findings}, {"TRANSCRIPT_HAS_NO_COMMAND_RECORDS"})
        self.assertEqual(stats["records"], 0)

    def test_preamble_only_is_rejected(self):
        findings, stats, readable = self._validate("invalid_preamble_only.txt")
        self.assertEqual({f["code"] for f in findings}, {"TRANSCRIPT_HAS_NO_COMMAND_RECORDS"})

    def test_crlf_transcript_validates_clean(self):
        findings, stats, readable = self._validate("valid_crlf.txt")
        self.assertEqual(findings, [])
        self.assertEqual(stats["records"], 2)

    def test_crlf_preserves_line_count_vs_lf_sibling(self):
        # The CRLF fixture is the CRLF-translated form of valid_test_record.txt;
        # both must produce the same record/test_record counts.
        f_crlf, s_crlf, _ = self._validate("valid_crlf.txt")
        f_lf, s_lf, _ = self._validate("valid_test_record.txt")
        self.assertEqual(s_crlf, s_lf)

    def test_bom_transcript_validates_clean(self):
        findings, stats, readable = self._validate("valid_bom.txt")
        self.assertEqual(findings, [])

    def test_bom_is_stripped_not_left_in_first_header_text(self):
        path = os.path.join(self.tmp, "valid_bom.txt")
        text = vt.read_transcript_text(path, self.schema)
        self.assertFalse(text.startswith("﻿"))
        self.assertFalse(text.startswith("\xef\xbb\xbf"))

    def test_unicode_content_validates_clean(self):
        findings, stats, readable = self._validate("valid_unicode.txt")
        self.assertEqual(findings, [])
        self.assertEqual(stats["records"], 1)

    def test_negative_exit_value_is_legal(self):
        findings, stats, readable = self._validate("valid_negative_exit.txt")
        self.assertEqual(findings, [])

    def test_negative_exit_value_is_parsed_correctly(self):
        path = os.path.join(self.tmp, "valid_negative_exit.txt")
        text = vt.read_transcript_text(path, self.schema)
        m = self.patterns["exit"].match([l for l in text.splitlines() if l.startswith("exit=")][0])
        self.assertEqual(int(m.group(1)), -9)

    def test_exit_appearing_twice_first_wins_and_is_not_fatal(self):
        findings, stats, readable = self._validate("invalid_duplicate_exit.txt")
        self.assertFalse(vt.any_error_severity(findings))
        dup = [f for f in findings if f["code"] == "TRANSCRIPT_RECORD_DUPLICATE_EXIT"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["detail"]["first_exit_value"], "0")
        self.assertEqual(dup[0]["detail"]["duplicate_value"], "1")

    def test_exit_twice_same_value_still_flagged_as_duplicate(self):
        findings, stats, readable = self._validate("invalid_exit_twice_same_value.txt")
        dup = [f for f in findings if f["code"] == "TRANSCRIPT_RECORD_DUPLICATE_EXIT"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["detail"]["first_exit_value"],
                          dup[0]["detail"]["duplicate_value"])

    def test_header_like_line_inside_body_is_flagged(self):
        findings, stats, readable = self._validate("invalid_header_lookalike_in_body.txt")
        codes = {f["code"] for f in findings}
        self.assertIn("TRANSCRIPT_HEADER_MALFORMED", codes)
        # the record itself is still recognised: 1 real record, valid exit
        self.assertEqual(stats["records"], 1)

    def test_misordering_within_record_is_the_only_ordering_check(self):
        findings, stats, readable = self._validate("invalid_verdict_before_ran.txt")
        self.assertEqual({f["code"] for f in findings}, {"TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN"})

    def test_inter_record_ordering_is_never_flagged(self):
        # cleanup.py runs before setup.py in the file; FORMAT.md states there
        # is no ordering requirement between records, so this must be clean.
        findings, stats, readable = self._validate("valid_out_of_order_records.txt")
        self.assertEqual(findings, [])

    def test_missing_ran_line_on_test_command(self):
        findings, stats, readable = self._validate("invalid_missing_ran_line.txt")
        self.assertEqual({f["code"] for f in findings}, {"TRANSCRIPT_RECORD_MISSING_RAN_LINE"})

    def test_missing_verdict_on_test_command(self):
        findings, stats, readable = self._validate("invalid_missing_verdict.txt")
        self.assertEqual({f["code"] for f in findings}, {"TRANSCRIPT_RECORD_MISSING_VERDICT"})

    def test_missing_both_ran_and_verdict(self):
        findings, stats, readable = self._validate("invalid_missing_both_ran_and_verdict.txt")
        codes = {f["code"] for f in findings}
        self.assertEqual(codes, {"TRANSCRIPT_RECORD_MISSING_RAN_LINE",
                                  "TRANSCRIPT_RECORD_MISSING_VERDICT"})

    def test_duplicate_header_command_text_is_not_an_error(self):
        # Two records with the identical command header text is legal --
        # FORMAT.md never forbids re-running the same command twice.
        text = ("=== $ python3 same.py ===\nexit=0\n\n"
                "=== $ python3 same.py ===\nexit=0\n")
        findings, stats = vt.validate_transcript_text(text, self.schema, self.patterns, "x")
        self.assertEqual(findings, [])
        self.assertEqual(stats["records"], 2)

    def test_each_exit_code_0(self):
        path = os.path.join(self.tmp, "valid_minimal.txt")
        rc, out, err = run_cli([path])
        self.assertEqual(rc, 0)

    def test_each_exit_code_1(self):
        path = os.path.join(self.tmp, "invalid_missing_exit.txt")
        rc, out, err = run_cli([path])
        self.assertEqual(rc, 1)

    def test_each_exit_code_2_nonexistent_file(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "does_not_exist.txt")])
        self.assertEqual(rc, 2)

    def test_each_exit_code_2_bad_utf8(self):
        path = os.path.join(self.tmp, "invalid_bad_utf8.bin")
        rc, out, err = run_cli([path])
        self.assertEqual(rc, 2)

    def test_each_exit_code_2_bad_root(self):
        rc, out, err = run_cli(["--root", "/definitely/not/a/real/path/xyz"])
        self.assertEqual(rc, 2)


class TestEveryDiagnosticCodeIndividually(unittest.TestCase):
    """One test per code in schema.json's diagnostics table, proving each is
    independently reachable (not just co-occurring with some other code)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_codes_")
        mf.generate(cls.tmp)
        cls.schema = vt.load_schema(DEFAULT_SCHEMA)
        cls.patterns = vt.compile_patterns(cls.schema)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _codes_for(self, fixture):
        path = os.path.join(self.tmp, fixture)
        findings, stats, readable = vt.validate_file(path, self.schema, self.patterns, fixture)
        return {f["code"] for f in findings}

    def test_code_TRANSCRIPT_HAS_NO_COMMAND_RECORDS(self):
        self.assertIn("TRANSCRIPT_HAS_NO_COMMAND_RECORDS", self._codes_for("invalid_empty.txt"))

    def test_code_TRANSCRIPT_RECORD_HAS_NO_EXIT(self):
        self.assertIn("TRANSCRIPT_RECORD_HAS_NO_EXIT", self._codes_for("invalid_missing_exit.txt"))

    def test_code_TRANSCRIPT_SHOWS_TEST_FAILURE(self):
        self.assertIn("TRANSCRIPT_SHOWS_TEST_FAILURE", self._codes_for("invalid_test_failure.txt"))

    def test_code_TRANSCRIPT_RECORD_EXIT_MALFORMED(self):
        self.assertIn("TRANSCRIPT_RECORD_EXIT_MALFORMED",
                       self._codes_for("invalid_malformed_exit_case.txt"))

    def test_code_TRANSCRIPT_RECORD_DUPLICATE_EXIT(self):
        self.assertIn("TRANSCRIPT_RECORD_DUPLICATE_EXIT",
                       self._codes_for("invalid_duplicate_exit.txt"))

    def test_code_TRANSCRIPT_HEADER_MALFORMED(self):
        self.assertIn("TRANSCRIPT_HEADER_MALFORMED",
                       self._codes_for("invalid_header_only_malformed.txt"))

    def test_code_TRANSCRIPT_RECORD_MISSING_RAN_LINE(self):
        self.assertIn("TRANSCRIPT_RECORD_MISSING_RAN_LINE",
                       self._codes_for("invalid_missing_ran_line.txt"))

    def test_code_TRANSCRIPT_RECORD_MISSING_VERDICT(self):
        self.assertIn("TRANSCRIPT_RECORD_MISSING_VERDICT",
                       self._codes_for("invalid_missing_verdict.txt"))

    def test_code_TRANSCRIPT_RECORD_RAN_LINE_MALFORMED(self):
        self.assertIn("TRANSCRIPT_RECORD_RAN_LINE_MALFORMED",
                       self._codes_for("invalid_ran_line_malformed.txt"))

    def test_code_TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN(self):
        self.assertIn("TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN",
                       self._codes_for("invalid_verdict_before_ran.txt"))

    def test_code_TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE(self):
        text = "exit=9\n\n=== $ python3 x.py ===\nexit=0\n"
        findings, stats = vt.validate_transcript_text(text, self.schema, self.patterns, "x")
        self.assertIn("TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE", {f["code"] for f in findings})

    def test_code_TRANSCRIPT_FILE_UNREADABLE(self):
        self.assertIn("TRANSCRIPT_FILE_UNREADABLE", self._codes_for("invalid_bad_utf8.bin"))

    def test_all_schema_codes_have_a_covering_test(self):
        # This is the guard against silently adding a new code to schema.json
        # without adding coverage for it above.
        covered = {name[len("test_code_"):] for name in dir(self)
                   if name.startswith("test_code_")}
        schema_codes = set(vt.all_codes(self.schema))
        self.assertEqual(covered, schema_codes)


# ==========================================================================
# 4. CLI behaviour: single-file and --root modes, exit codes, argument
#    handling.
# ==========================================================================

class TestCLISingleFile(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_cli_single_")
        mf.generate(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_valid_file_prints_json_to_stdout(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "valid_minimal.txt")])
        self.assertEqual(rc, 0)
        report = json.loads(out)
        self.assertEqual(report["status"], "valid")

    def test_invalid_file_exit_1_status_invalid(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "invalid_missing_exit.txt")])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertEqual(report["status"], "invalid")

    def test_output_flag_writes_file_not_stdout(self):
        outpath = os.path.join(self.tmp, "_report.json")
        rc, out, err = run_cli([os.path.join(self.tmp, "valid_minimal.txt"), "-o", outpath])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        with open(outpath) as fh:
            report = json.load(fh)
        self.assertEqual(report["status"], "valid")

    def test_no_target_is_setup_error(self):
        rc, out, err = run_cli([])
        self.assertEqual(rc, 2)

    def test_root_and_paths_mutually_exclusive(self):
        rc, out, err = run_cli(["--root", self.tmp, os.path.join(self.tmp, "valid_minimal.txt")])
        self.assertEqual(rc, 2)

    def test_bad_schema_path_is_setup_error(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "valid_minimal.txt"),
                                 "--schema", "/no/such/schema.json"])
        self.assertEqual(rc, 2)

    def test_multiple_files_all_valid(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "valid_minimal.txt"),
                                 os.path.join(self.tmp, "valid_test_record.txt")])
        self.assertEqual(rc, 0)

    def test_multiple_files_one_invalid_fails_whole_run(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "valid_minimal.txt"),
                                 os.path.join(self.tmp, "invalid_missing_exit.txt")])
        self.assertEqual(rc, 1)

    def test_findings_carry_file_line_text_code(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "invalid_missing_exit.txt")])
        report = json.loads(out)
        self.assertTrue(report["findings"])
        for f in report["findings"]:
            self.assertTrue(f["file"])
            self.assertIsInstance(f["line"], int)
            self.assertIsInstance(f["text"], str)
            self.assertTrue(f["code"])

    def test_error_report_on_stderr_when_no_output_flag(self):
        rc, out, err = run_cli([os.path.join(self.tmp, "nope.txt")])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("error", err)


class TestCLIRoot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_cli_root_")
        mf.generate(cls.tmp)
        cls.root = os.path.join(cls.tmp, "root_demo")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_root_scan_finds_expected_tool_dirs(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        self.assertEqual(sorted(report["coverage"]["directories_with_transcript"]),
                          ["tool-broken", "tool-clean", "tool-nohead"])

    def test_root_scan_skips_hidden_and_pycache_dirs(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        names = report["coverage"]["directories_with_transcript"]
        self.assertNotIn(".hidden-tool", names)
        self.assertNotIn("__pycache__", names)

    def test_root_scan_skips_non_directory_entries(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        all_names = (report["coverage"]["directories_with_transcript"]
                     + report["coverage"]["directories_without_transcript"])
        self.assertNotIn("stray_file.txt", all_names)

    def test_root_scan_reports_directories_without_transcript(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        self.assertIn("tool-empty", report["coverage"]["directories_without_transcript"])

    def test_root_scan_exit_1_because_tool_broken_present(self):
        rc, out, err = run_cli(["--root", self.root])
        self.assertEqual(rc, 1)

    def test_root_scan_findings_reference_relative_paths(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        for f in report["findings"]:
            self.assertFalse(os.path.isabs(f["file"]), f["file"])
            self.assertFalse(f["file"].startswith(self.tmp))

    def test_root_scan_only_clean_subset_is_valid(self):
        only_clean = os.path.join(self.tmp, "only_clean_root")
        os.makedirs(os.path.join(only_clean, "tool-clean"))
        shutil.copy(os.path.join(self.root, "tool-clean", "captured_output.txt"),
                    os.path.join(only_clean, "tool-clean", "captured_output.txt"))
        rc, out, err = run_cli(["--root", only_clean])
        self.assertEqual(rc, 0)
        shutil.rmtree(only_clean)

    def test_root_missing_directory_is_setup_error(self):
        rc, out, err = run_cli(["--root", os.path.join(self.tmp, "not_here")])
        self.assertEqual(rc, 2)

    def test_discover_function_directly(self):
        with_t, without_t = vt.discover(self.root)
        names_with = sorted(n for n, _ in with_t)
        self.assertEqual(names_with, ["tool-broken", "tool-clean", "tool-nohead"])
        self.assertEqual(without_t, ["tool-empty"])

    def test_stats_present_per_scanned_directory(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        self.assertIn("tool-clean", report["stats"])
        self.assertEqual(report["stats"]["tool-clean"]["records"], 2)


# ==========================================================================
# 5. Determinism, including the relocation leg.
# ==========================================================================

class TestDeterminism(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_determinism_")
        mf.generate(cls.tmp)
        cls.root = os.path.join(cls.tmp, "root_demo")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_two_runs_in_place_are_byte_identical(self):
        rc1, out1, _ = run_cli(["--root", self.root])
        rc2, out2, _ = run_cli(["--root", self.root])
        self.assertEqual(rc1, rc2)
        self.assertEqual(out1, out2)

    def test_relocated_tree_produces_identical_bytes(self):
        relocated = os.path.join(self.tmp, "relocated_copy_xyz")
        shutil.copytree(self.root, relocated)
        rc1, out1, _ = run_cli(["--root", self.root])
        rc2, out2, _ = run_cli(["--root", relocated])
        self.assertEqual(rc1, rc2)
        # findings differ only in the leading directory name is not the
        # case here -- coverage/findings paths are root-relative, so the
        # bytes must match exactly across relocation.
        self.assertEqual(out1, out2)
        shutil.rmtree(relocated)

    def test_report_contains_no_absolute_paths(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        blob = json.dumps(report)
        self.assertNotIn(self.tmp, blob)
        self.assertNotIn("/tmp/", blob)

    def test_report_has_no_timestamp_like_keys(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        blob = json.dumps(report).lower()
        for forbidden in ("timestamp", "generated_at", "date", "time_ms"):
            self.assertNotIn(forbidden, blob)

    def test_findings_are_sorted_deterministically(self):
        rc, out, err = run_cli(["--root", self.root])
        report = json.loads(out)
        keys = [(f["file"], f["line"], f["code"]) for f in report["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_sha256_stable_across_three_runs_two_in_place_one_relocated(self):
        relocated = os.path.join(self.tmp, "relocated_copy_2")
        shutil.copytree(self.root, relocated)
        digests = []
        for root in (self.root, self.root, relocated):
            rc, out, err = run_cli(["--root", root])
            digests.append(hashlib.sha256(out.encode("utf-8")).hexdigest())
        self.assertEqual(len(set(digests)), 1, digests)
        shutil.rmtree(relocated)


# ==========================================================================
# 6. Proof the schema genuinely drives behaviour.
# ==========================================================================

class TestSchemaDrivesValidator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tsv_schema_drive_")
        mf.generate(cls.tmp)
        with open(DEFAULT_SCHEMA) as fh:
            cls.base_schema = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write_schema(self, schema_obj):
        path = os.path.join(self.tmp, "_schema_%d.json" % id(schema_obj))
        with open(path, "w") as fh:
            json.dump(schema_obj, fh)
        return path

    def test_loosening_header_regex_recognises_a_previously_invisible_record(self):
        # Under the real schema, "===$ cmd ===" (no space after the first
        # ===) matches no header at all, so the file has zero records.
        target_path = os.path.join(self.tmp, "_loose_header_target.txt")
        with open(target_path, "w") as fh:
            fh.write("===$ python3 broken.py ===\nexit=0\n")
        rc_before, out_before, _ = run_cli([target_path])
        self.assertEqual(rc_before, 1)
        report_before = json.loads(out_before)
        self.assertEqual(report_before["diagnostic_counts"]["TRANSCRIPT_HAS_NO_COMMAND_RECORDS"], 1)

        loose = copy.deepcopy(self.base_schema)
        # Accept the missing-space variant too.
        loose["patterns"]["header"]["regex"] = r"^={2,}\s*\$\s*(.+?)\s*={2,}\s*$"
        schema_path = self._write_schema(loose)
        rc_after, out_after, _ = run_cli([target_path, "--schema", schema_path])
        self.assertEqual(rc_after, 0, out_after)
        os.remove(target_path)

    def test_downgrading_severity_to_info_changes_exit_code(self):
        target = os.path.join(self.tmp, "invalid_missing_exit.txt")
        rc_before, _, _ = run_cli([target])
        self.assertEqual(rc_before, 1)

        downgraded = copy.deepcopy(self.base_schema)
        downgraded["diagnostics"]["TRANSCRIPT_RECORD_HAS_NO_EXIT"]["severity"] = "info"
        schema_path = self._write_schema(downgraded)
        rc_after, out_after, _ = run_cli([target, "--schema", schema_path])
        self.assertEqual(rc_after, 0, out_after)
        report = json.loads(out_after)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["diagnostic_counts"]["TRANSCRIPT_RECORD_HAS_NO_EXIT"], 1)

    def test_upgrading_duplicate_exit_to_error_changes_exit_code(self):
        target = os.path.join(self.tmp, "invalid_duplicate_exit.txt")
        rc_before, _, _ = run_cli([target])
        self.assertEqual(rc_before, 0)

        upgraded = copy.deepcopy(self.base_schema)
        upgraded["diagnostics"]["TRANSCRIPT_RECORD_DUPLICATE_EXIT"]["severity"] = "error"
        schema_path = self._write_schema(upgraded)
        rc_after, out_after, _ = run_cli([target, "--schema", schema_path])
        self.assertEqual(rc_after, 1, out_after)

    def test_narrowing_test_command_pattern_removes_missing_ran_finding(self):
        target = os.path.join(self.tmp, "invalid_missing_ran_line.txt")
        rc_before, _, _ = run_cli([target])
        self.assertEqual(rc_before, 1)

        narrowed = copy.deepcopy(self.base_schema)
        narrowed["patterns"]["test_command"]["regex"] = r"this_never_matches_anything_xyz"
        schema_path = self._write_schema(narrowed)
        rc_after, out_after, _ = run_cli([target, "--schema", schema_path])
        self.assertEqual(rc_after, 0, out_after)

    def test_schema_version_is_surfaced_in_report(self):
        target = os.path.join(self.tmp, "valid_minimal.txt")
        rc, out, err = run_cli([target])
        report = json.loads(out)
        self.assertEqual(report["transcript_schema_version"], self.base_schema["schema_version"])

    def test_bumped_schema_version_is_surfaced_too(self):
        bumped = copy.deepcopy(self.base_schema)
        bumped["schema_version"] = 999
        schema_path = self._write_schema(bumped)
        target = os.path.join(self.tmp, "valid_minimal.txt")
        rc, out, err = run_cli([target, "--schema", schema_path])
        report = json.loads(out)
        self.assertEqual(report["transcript_schema_version"], 999)


# ==========================================================================
# 7. Schema loading / self-validation errors.
# ==========================================================================

class TestSchemaLoading(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tsv_schema_load_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def test_nonexistent_schema_path(self):
        with self.assertRaises(vt.SetupError):
            vt.load_schema(os.path.join(self.tmp, "nope.json"))

    def test_invalid_json(self):
        path = self._write("bad.json", "{not json")
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_non_object_root(self):
        path = self._write("list_root.json", "[1,2,3]")
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_missing_schema_version(self):
        path = self._write("no_version.json", json.dumps({"patterns": {}, "diagnostics": {}}))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_non_integer_schema_version(self):
        path = self._write("bad_version.json",
                            json.dumps({"schema_version": "1", "patterns": {}, "diagnostics": {}}))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_missing_patterns_key(self):
        path = self._write("no_patterns.json",
                            json.dumps({"schema_version": 1, "diagnostics": {}}))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_missing_one_required_pattern(self):
        with open(DEFAULT_SCHEMA) as fh:
            schema = json.load(fh)
        del schema["patterns"]["exit_lookalike"]
        path = self._write("missing_pattern.json", json.dumps(schema))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_missing_diagnostics_key(self):
        path = self._write("no_diag.json",
                            json.dumps({"schema_version": 1,
                                        "patterns": {n: {"regex": "."} for n in vt.REQUIRED_PATTERN_NAMES}}))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_empty_diagnostics_object(self):
        path = self._write("empty_diag.json",
                            json.dumps({"schema_version": 1,
                                        "patterns": {n: {"regex": "."} for n in vt.REQUIRED_PATTERN_NAMES},
                                        "diagnostics": {}}))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_diagnostic_with_bad_severity(self):
        with open(DEFAULT_SCHEMA) as fh:
            schema = json.load(fh)
        schema["diagnostics"]["TRANSCRIPT_HAS_NO_COMMAND_RECORDS"]["severity"] = "critical"
        path = self._write("bad_severity.json", json.dumps(schema))
        with self.assertRaises(vt.SetupError):
            vt.load_schema(path)

    def test_pattern_with_invalid_regex_syntax(self):
        with open(DEFAULT_SCHEMA) as fh:
            schema = json.load(fh)
        schema["patterns"]["exit"]["regex"] = "(unclosed["
        path = self._write("bad_regex.json", json.dumps(schema))
        schema_loaded = vt.load_schema(path)
        with self.assertRaises(vt.SetupError):
            vt.compile_patterns(schema_loaded)

    def test_real_schema_loads_without_error(self):
        schema = vt.load_schema(DEFAULT_SCHEMA)
        self.assertEqual(schema["schema_version"], 1)

    def test_real_schema_patterns_all_compile(self):
        schema = vt.load_schema(DEFAULT_SCHEMA)
        patterns = vt.compile_patterns(schema)
        self.assertEqual(set(patterns), set(vt.REQUIRED_PATTERN_NAMES))


# ==========================================================================
# 8. make_fixtures.py generator: round-trip, binary mode, empty dirs.
# ==========================================================================

class TestMakeFixturesGenerator(unittest.TestCase):

    def test_round_trip_ok(self):
        ok, bad = mf.round_trip_ok()
        self.assertTrue(ok, bad)

    def test_every_fixture_is_valid_base64(self):
        for name, b64 in mf.FIXTURES.items():
            base64.b64decode(b64)  # raises if not valid base64

    def test_generate_creates_all_files(self):
        d = tempfile.mkdtemp()
        try:
            written = mf.generate(d)
            self.assertEqual(set(written), set(mf.FIXTURES))
            for rel in mf.FIXTURES:
                self.assertTrue(os.path.isfile(os.path.join(d, rel)))
        finally:
            shutil.rmtree(d)

    def test_generate_creates_empty_dirs_explicitly(self):
        d = tempfile.mkdtemp()
        try:
            mf.generate(d)
            for rel in mf.EMPTY_DIRS:
                p = os.path.join(d, rel)
                self.assertTrue(os.path.isdir(p))
                self.assertEqual(os.listdir(p), [])
        finally:
            shutil.rmtree(d)

    def test_generate_refuses_nonempty_dir_without_force(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "occupied.txt"), "w") as fh:
                fh.write("x")
            with self.assertRaises(FileExistsError):
                mf.generate(d)
        finally:
            shutil.rmtree(d)

    def test_generate_force_overwrites(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "occupied.txt"), "w") as fh:
                fh.write("x")
            mf.generate(d, force=True)
            self.assertFalse(os.path.exists(os.path.join(d, "occupied.txt")))
        finally:
            shutil.rmtree(d)

    def test_crlf_fixture_bytes_survive_generation_on_disk(self):
        d = tempfile.mkdtemp()
        try:
            mf.generate(d)
            with open(os.path.join(d, "valid_crlf.txt"), "rb") as fh:
                data = fh.read()
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"\n\n", data.replace(b"\r\n", b""))
        finally:
            shutil.rmtree(d)

    def test_bom_fixture_bytes_survive_generation_on_disk(self):
        d = tempfile.mkdtemp()
        try:
            mf.generate(d)
            with open(os.path.join(d, "valid_bom.txt"), "rb") as fh:
                data = fh.read()
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        finally:
            shutil.rmtree(d)

    def test_bad_utf8_fixture_bytes_are_actually_invalid_utf8(self):
        d = tempfile.mkdtemp()
        try:
            mf.generate(d)
            with open(os.path.join(d, "invalid_bad_utf8.bin"), "rb") as fh:
                data = fh.read()
            with self.assertRaises(UnicodeDecodeError):
                data.decode("utf-8")
        finally:
            shutil.rmtree(d)

    def test_two_independent_generate_calls_are_byte_identical(self):
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            mf.generate(d1)
            mf.generate(d2)
            for rel in sorted(mf.FIXTURES):
                with open(os.path.join(d1, rel), "rb") as fh:
                    b1 = fh.read()
                with open(os.path.join(d2, rel), "rb") as fh:
                    b2 = fh.read()
                self.assertEqual(b1, b2, rel)
        finally:
            shutil.rmtree(d1)
            shutil.rmtree(d2)

    def test_verify_mode_via_module_function(self):
        self.assertEqual(mf.verify(), 0)

    def test_verify_mode_via_cli(self):
        proc = subprocess.run([PY, os.path.join(HERE, "make_fixtures.py"), "--verify"],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_cli_generate_then_diff_r_against_module_generate(self):
        cli_dir = tempfile.mkdtemp()
        mod_dir = tempfile.mkdtemp()
        try:
            proc = subprocess.run([PY, os.path.join(HERE, "make_fixtures.py"), cli_dir, "--force"],
                                   capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            mf.generate(mod_dir, force=True)
            diff = subprocess.run(["diff", "-r", cli_dir, mod_dir], capture_output=True, text=True)
            self.assertEqual(diff.returncode, 0, diff.stdout)
        finally:
            shutil.rmtree(cli_dir)
            shutil.rmtree(mod_dir)

    def test_cli_no_args_returns_usage_error(self):
        proc = subprocess.run([PY, os.path.join(HERE, "make_fixtures.py")],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_root_demo_tree_generated_under_prefix(self):
        d = tempfile.mkdtemp()
        try:
            mf.generate(d)
            self.assertTrue(os.path.isdir(os.path.join(d, "root_demo", "tool-clean")))
            self.assertTrue(os.path.isdir(os.path.join(d, "root_demo", "tool-broken")))
            self.assertTrue(os.path.isdir(os.path.join(d, "root_demo", "tool-empty")))
        finally:
            shutil.rmtree(d)


# ==========================================================================
# 9. Report-building utility functions.
# ==========================================================================

class TestReportUtilities(unittest.TestCase):

    def setUp(self):
        self.schema = vt.load_schema(DEFAULT_SCHEMA)

    def test_canonical_json_sorted_keys(self):
        obj = {"b": 1, "a": 2}
        text = vt.canonical_json(obj)
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_canonical_json_ends_with_newline(self):
        text = vt.canonical_json({"x": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_canonical_json_compact_separators(self):
        text = vt.canonical_json({"a": [1, 2], "b": {"c": 3}})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_canonical_json_ascii_only(self):
        text = vt.canonical_json({"x": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u", text)

    def test_diagnostic_counts_includes_every_code_even_zero(self):
        counts = vt.diagnostic_counts(self.schema, [])
        self.assertEqual(set(counts), set(vt.all_codes(self.schema)))
        self.assertTrue(all(v == 0 for v in counts.values()))

    def test_diagnostic_counts_tallies_correctly(self):
        findings = [{"code": "TRANSCRIPT_RECORD_HAS_NO_EXIT", "severity": "error"}] * 3
        counts = vt.diagnostic_counts(self.schema, findings)
        self.assertEqual(counts["TRANSCRIPT_RECORD_HAS_NO_EXIT"], 3)

    def test_any_error_severity_true(self):
        findings = [{"severity": "info"}, {"severity": "error"}]
        self.assertTrue(vt.any_error_severity(findings))

    def test_any_error_severity_false(self):
        findings = [{"severity": "info"}, {"severity": "info"}]
        self.assertFalse(vt.any_error_severity(findings))

    def test_any_error_severity_empty(self):
        self.assertFalse(vt.any_error_severity([]))

    def test_all_codes_sorted(self):
        codes = vt.all_codes(self.schema)
        self.assertEqual(codes, sorted(codes))

    def test_severity_of_known_code(self):
        self.assertEqual(vt.severity_of(self.schema, "TRANSCRIPT_RECORD_DUPLICATE_EXIT"), "info")

    def test_severity_of_unknown_code_defaults_to_error(self):
        self.assertEqual(vt.severity_of(self.schema, "NOT_A_REAL_CODE"), "error")

    def test_truncate_short_text_unchanged(self):
        self.assertEqual(vt.truncate("short"), "short")

    def test_truncate_long_text_is_shortened(self):
        long_text = "x" * 1000
        out = vt.truncate(long_text, limit=50)
        self.assertLessEqual(len(out), 80)
        self.assertTrue(out.startswith("x" * 50))

    def test_split_lines_counts_from_one(self):
        lines = vt.split_lines("a\nb\nc")
        self.assertEqual([ln for ln, _ in lines], [1, 2, 3])

    def test_split_lines_handles_crlf(self):
        lines = vt.split_lines("a\r\nb\r\nc")
        self.assertEqual([t for _, t in lines], ["a", "b", "c"])

    def test_split_lines_empty_text(self):
        self.assertEqual(vt.split_lines(""), [])

    def test_build_finding_shape(self):
        f = vt.build_finding(self.schema, "TRANSCRIPT_RECORD_HAS_NO_EXIT", "f.txt", 3, "text here")
        self.assertEqual(f["file"], "f.txt")
        self.assertEqual(f["line"], 3)
        self.assertEqual(f["severity"], "error")
        self.assertEqual(f["detail"], {})


# ==========================================================================
# 10. Stdlib-only import check.
# ==========================================================================

class TestStdlibOnly(unittest.TestCase):

    def test_validate_transcript_imports_are_stdlib(self):
        allowed = {"argparse", "json", "os", "re", "sys"}
        with open(os.path.join(HERE, "validate_transcript.py")) as fh:
            src = fh.read()
        import ast
        tree = ast.parse(src)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertTrue(found.issubset(allowed), found - allowed)

    def test_make_fixtures_imports_are_stdlib(self):
        allowed = {"base64", "filecmp", "os", "shutil", "subprocess", "sys", "tempfile"}
        with open(os.path.join(HERE, "make_fixtures.py")) as fh:
            src = fh.read()
        import ast
        tree = ast.parse(src)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertTrue(found.issubset(allowed), found - allowed)


if __name__ == "__main__":
    unittest.main()

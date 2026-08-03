#!/usr/bin/env python3
"""Test suite for exitharness.py.

Run with:
    python3 -m unittest -v test_exitharness

Organized into:
  - Unit tests against the module's internal functions (canonical JSON,
    manifest-entry validation, single-case execution, sorting,
    report/summary building, report writing, path-escape scrubbing).
  - CLI integration tests that invoke `exitharness.py` as a real
    subprocess against on-disk fixture trees built in a
    tempfile.TemporaryDirectory() for each test.
  - Determinism / relocation / ordering tests.
  - Unicode and CRLF handling tests.
  - A regression test pinning the path-escape bug found during the
    bug hunt (see README.md "Bug found during the bug hunt").

Safety: every test-created directory comes from
tempfile.TemporaryDirectory(), and nothing outside directories this
test file itself created is ever removed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import exitharness as eh

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_PATH = os.path.join(THIS_DIR, "exitharness.py")
FIXTURE_ROOT = os.path.join(THIS_DIR, "fixture_tools")
EXAMPLE_MANIFEST = os.path.join(THIS_DIR, "manifest_example.json")


def run_cli(args, timeout=15):
    """Run exitharness.py as a real subprocess; return CompletedProcess."""
    return subprocess.run(
        [sys.executable, HARNESS_PATH] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def make_case(id_, cwd=".", argv=None, expect_exit=0, **kw):
    case = {"id": id_, "cwd": cwd, "argv": argv or ["python3", "-c", "pass"], "expect_exit": expect_exit}
    case.update(kw)
    return case


# ---------------------------------------------------------------------------
# Unit tests: canonical JSON
# ---------------------------------------------------------------------------

class TestCanonicalJsonDumps(unittest.TestCase):
    def test_sorts_keys(self):
        self.assertEqual(eh.canonical_json_dumps({"b": 1, "a": 2}), '{"a":2,"b":1}')

    def test_no_spaces_after_separators(self):
        out = eh.canonical_json_dumps({"a": [1, 2], "b": 3})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_ensure_ascii_escapes_unicode(self):
        out = eh.canonical_json_dumps({"name": "café"})
        self.assertIn("\\u00e9", out)
        self.assertNotIn("é", out)

    def test_bool_and_null_render_correctly(self):
        out = eh.canonical_json_dumps({"x": True, "y": None, "z": False})
        self.assertEqual(out, '{"x":true,"y":null,"z":false}')

    def test_nested_list_sorted_keys_inside(self):
        out = eh.canonical_json_dumps({"items": [{"b": 1, "a": 2}]})
        self.assertEqual(out, '{"items":[{"a":2,"b":1}]}')


# ---------------------------------------------------------------------------
# Unit tests: validate_case
# ---------------------------------------------------------------------------

class TestValidateCaseWellFormed(unittest.TestCase):
    def test_minimal_valid_case(self):
        norm, errors = eh.validate_case(make_case("c1"), 0)
        self.assertEqual(errors, [])
        self.assertEqual(norm["id"], "c1")
        self.assertEqual(norm["cwd"], ".")
        self.assertEqual(norm["argv"], ["python3", "-c", "pass"])
        self.assertEqual(norm["expect_exit"], 0)
        self.assertFalse(norm["expect_stdout_canonical_json"])
        self.assertIsNone(norm["expect_stdout_contains"])
        self.assertIsNone(norm["expect_stderr_contains"])
        self.assertIsNone(norm["timeout_seconds"])

    def test_all_optional_fields_valid(self):
        raw = make_case(
            "c2",
            expect_stdout_canonical_json=True,
            expect_stdout_contains="ok",
            expect_stderr_contains="warn",
            timeout_seconds=2.5,
        )
        norm, errors = eh.validate_case(raw, 0)
        self.assertEqual(errors, [])
        self.assertTrue(norm["expect_stdout_canonical_json"])
        self.assertEqual(norm["expect_stdout_contains"], "ok")
        self.assertEqual(norm["expect_stderr_contains"], "warn")
        self.assertEqual(norm["timeout_seconds"], 2.5)

    def test_negative_expect_exit_is_valid(self):
        norm, errors = eh.validate_case(make_case("c3", expect_exit=-1), 0)
        self.assertEqual(errors, [])
        self.assertEqual(norm["expect_exit"], -1)

    def test_int_timeout_seconds_is_valid(self):
        norm, errors = eh.validate_case(make_case("c4", timeout_seconds=5), 0)
        self.assertEqual(errors, [])
        self.assertEqual(norm["timeout_seconds"], 5)


class TestValidateCaseMissingKeys(unittest.TestCase):
    def test_missing_id(self):
        raw = make_case("x")
        del raw["id"]
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("missing required key 'id'", errors)

    def test_missing_cwd(self):
        raw = make_case("x")
        del raw["cwd"]
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("missing required key 'cwd'", errors)

    def test_missing_argv(self):
        raw = make_case("x")
        del raw["argv"]
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("missing required key 'argv'", errors)

    def test_missing_expect_exit(self):
        raw = make_case("x")
        del raw["expect_exit"]
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("missing required key 'expect_exit'", errors)

    def test_missing_all_required_keys(self):
        _, errors = eh.validate_case({}, 0)
        self.assertEqual(len(errors), 4)


class TestValidateCaseWrongTypes(unittest.TestCase):
    def test_id_wrong_type_int(self):
        raw = make_case("x")
        raw["id"] = 5
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'id' must be a non-empty string", errors)

    def test_id_empty_string(self):
        raw = make_case("x")
        raw["id"] = ""
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'id' must be a non-empty string", errors)

    def test_cwd_wrong_type(self):
        raw = make_case("x")
        raw["cwd"] = 3
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'cwd' must be a string", errors)

    def test_argv_wrong_type_string(self):
        raw = make_case("x")
        raw["argv"] = "python3"
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'argv' must be a non-empty JSON list of strings", errors)

    def test_argv_empty_list(self):
        raw = make_case("x")
        raw["argv"] = []
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'argv' must be a non-empty JSON list of strings", errors)

    def test_argv_list_with_non_string_element(self):
        raw = make_case("x")
        raw["argv"] = ["python3", 5]
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'argv' must be a non-empty JSON list of strings", errors)

    def test_expect_exit_wrong_type_string(self):
        raw = make_case("x")
        raw["expect_exit"] = "0"
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'expect_exit' must be an integer", errors)

    def test_expect_exit_bool_rejected(self):
        raw = make_case("x")
        raw["expect_exit"] = True
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'expect_exit' must be an integer", errors)

    def test_expect_stdout_canonical_json_wrong_type(self):
        raw = make_case("x", expect_stdout_canonical_json="yes")
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'expect_stdout_canonical_json' must be a boolean", errors)

    def test_expect_stdout_contains_wrong_type(self):
        raw = make_case("x", expect_stdout_contains=42)
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'expect_stdout_contains' must be a string", errors)

    def test_expect_stderr_contains_wrong_type(self):
        raw = make_case("x", expect_stderr_contains=[1, 2])
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'expect_stderr_contains' must be a string", errors)

    def test_timeout_seconds_wrong_type(self):
        raw = make_case("x", timeout_seconds="5")
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'timeout_seconds' must be a positive number", errors)

    def test_timeout_seconds_zero_invalid(self):
        raw = make_case("x", timeout_seconds=0)
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'timeout_seconds' must be a positive number", errors)

    def test_timeout_seconds_negative_invalid(self):
        raw = make_case("x", timeout_seconds=-3)
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'timeout_seconds' must be a positive number", errors)

    def test_timeout_seconds_bool_invalid(self):
        raw = make_case("x", timeout_seconds=True)
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("'timeout_seconds' must be a positive number", errors)

    def test_case_not_a_dict_list(self):
        _, errors = eh.validate_case(["not", "a", "dict"], 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("is not a JSON object", errors[0])

    def test_case_not_a_dict_string(self):
        _, errors = eh.validate_case("nope", 0)
        self.assertEqual(len(errors), 1)

    def test_case_not_a_dict_number(self):
        _, errors = eh.validate_case(42, 0)
        self.assertEqual(len(errors), 1)

    def test_unknown_key_reported(self):
        raw = make_case("x")
        raw["bogus_key"] = 1
        _, errors = eh.validate_case(raw, 0)
        self.assertIn("unknown key 'bogus_key'", errors)

    def test_multiple_errors_all_reported(self):
        raw = {"id": 5, "cwd": 3, "argv": [], "expect_exit": "x"}
        _, errors = eh.validate_case(raw, 0)
        self.assertEqual(len(errors), 4)


# ---------------------------------------------------------------------------
# Unit tests: run_case
# ---------------------------------------------------------------------------

class TestRunCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _case(self, **kw):
        base = {
            "id": "t",
            "cwd": ".",
            "argv": ["python3", "-c", "pass"],
            "expect_exit": 0,
            "expect_stdout_canonical_json": False,
            "expect_stdout_contains": None,
            "expect_stderr_contains": None,
            "timeout_seconds": None,
        }
        base.update(kw)
        return base

    def test_match_exit_zero(self):
        result = eh.run_case(self._case(argv=["python3", "-c", "import sys; sys.exit(0)"]), self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)
        self.assertEqual(result["actual_exit"], 0)

    def test_exit_mismatch(self):
        c = self._case(argv=["python3", "-c", "import sys; sys.exit(3)"], expect_exit=0)
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_EXIT_MISMATCH)
        self.assertEqual(result["actual_exit"], 3)

    def test_stdout_not_json(self):
        c = self._case(argv=["python3", "-c", "print('not json')"], expect_stdout_canonical_json=True)
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_STDOUT_NOT_JSON)

    def test_stdout_not_canonical_extra_space(self):
        c = self._case(
            argv=["python3", "-c", 'print(\'{"a": 1}\')'],
            expect_stdout_canonical_json=True,
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_STDOUT_NOT_CANONICAL)

    def test_stdout_not_canonical_unsorted_keys(self):
        c = self._case(
            argv=["python3", "-c", 'print(\'{"b":1,"a":2}\')'],
            expect_stdout_canonical_json=True,
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_STDOUT_NOT_CANONICAL)

    def test_stdout_canonical_matches(self):
        c = self._case(
            argv=["python3", "-c", 'print(\'{"a":1,"b":2}\')'],
            expect_stdout_canonical_json=True,
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)

    def test_stdout_missing_substring(self):
        c = self._case(argv=["python3", "-c", "print('hello')"], expect_stdout_contains="goodbye")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_STDOUT_MISSING_SUBSTRING)

    def test_stdout_contains_substring_matches(self):
        c = self._case(argv=["python3", "-c", "print('hello world')"], expect_stdout_contains="lo wo")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)

    def test_stderr_missing_substring(self):
        c = self._case(
            argv=["python3", "-c", "import sys; print('oops', file=sys.stderr)"],
            expect_stderr_contains="fatal",
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_STDERR_MISSING_SUBSTRING)

    def test_stderr_contains_substring_matches(self):
        c = self._case(
            argv=["python3", "-c", "import sys; print('fatal error', file=sys.stderr)"],
            expect_stderr_contains="fatal",
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)

    def test_timeout(self):
        c = self._case(argv=["python3", "-c", "import time; time.sleep(5)"], timeout_seconds=0.2)
        result = eh.run_case(c, self.root, 30)
        self.assertEqual(result["result"], eh.RESULT_TIMEOUT)
        self.assertIsNone(result["actual_exit"])

    def test_default_timeout_used_when_case_timeout_absent(self):
        c = self._case(argv=["python3", "-c", "import time; time.sleep(5)"], timeout_seconds=None)
        result = eh.run_case(c, self.root, 0.2)
        self.assertEqual(result["result"], eh.RESULT_TIMEOUT)

    def test_case_timeout_overrides_default(self):
        # Default timeout is huge, but the per-case override is tiny.
        c = self._case(argv=["python3", "-c", "import time; time.sleep(5)"], timeout_seconds=0.2)
        result = eh.run_case(c, self.root, 300)
        self.assertEqual(result["result"], eh.RESULT_TIMEOUT)

    def test_case_error_missing_cwd_dir(self):
        c = self._case(cwd="does_not_exist")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_CASE_ERROR)
        self.assertIn("does not exist", result["detail"])

    def test_case_error_executable_missing(self):
        c = self._case(argv=["definitely_not_a_real_executable_xyz"])
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_CASE_ERROR)

    def test_case_error_cwd_absolute_path_rejected(self):
        c = self._case(cwd="/etc")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_CASE_ERROR)
        self.assertIn("must be a relative path", result["detail"])

    def test_case_error_cwd_escapes_root_via_dotdot(self):
        c = self._case(cwd="../../../../../../etc")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_CASE_ERROR)
        self.assertIn("escapes --root", result["detail"])

    def test_cwd_dot_resolves_to_root_itself(self):
        c = self._case(cwd=".", argv=["python3", "-c", "import os; print(os.getcwd())"])
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)

    def test_cwd_subdirectory_used_as_actual_cwd(self):
        sub = os.path.join(self.root, "subdir")
        os.mkdir(sub)
        with open(os.path.join(sub, "marker.txt"), "w") as f:
            f.write("x")
        c = self._case(
            cwd="subdir",
            argv=["python3", "-c", "import os; assert os.path.exists('marker.txt')"],
        )
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["result"], eh.RESULT_MATCH)

    def test_result_keeps_manifest_relative_cwd_verbatim(self):
        sub = os.path.join(self.root, "subdir2")
        os.mkdir(sub)
        c = self._case(cwd="subdir2")
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["cwd"], "subdir2")

    def test_actual_exit_none_on_case_error(self):
        c = self._case(cwd="nope_missing")
        result = eh.run_case(c, self.root, 5)
        self.assertIsNone(result["actual_exit"])

    def test_expect_exit_recorded_in_result(self):
        c = self._case(expect_exit=7, argv=["python3", "-c", "import sys; sys.exit(7)"])
        result = eh.run_case(c, self.root, 5)
        self.assertEqual(result["expect_exit"], 7)
        self.assertEqual(result["actual_exit"], 7)


# ---------------------------------------------------------------------------
# Unit tests: _scrub
# ---------------------------------------------------------------------------

class TestScrub(unittest.TestCase):
    def test_scrub_removes_root_path(self):
        root = "/tmp/some/abs/root"
        text = f"could not execute case: [Errno 2] No such file: {root}/bin/tool"
        scrubbed = eh._scrub(text, root)
        self.assertNotIn(root, scrubbed)
        self.assertIn("<root>", scrubbed)

    def test_scrub_none_passthrough(self):
        self.assertIsNone(eh._scrub(None, "/tmp/x"))

    def test_scrub_no_match_unchanged(self):
        text = "nothing to scrub here"
        self.assertEqual(eh._scrub(text, "/tmp/other/root"), text)

    def test_scrub_empty_root_noop(self):
        text = "some text"
        self.assertEqual(eh._scrub(text, ""), text)


# ---------------------------------------------------------------------------
# Unit tests: sorting / total order
# ---------------------------------------------------------------------------

def _r(id_, result, **kw):
    base = {"id": id_, "cwd": ".", "argv": ["x"], "expect_exit": 0, "actual_exit": 0, "result": result, "detail": None}
    base.update(kw)
    return base


class TestSorting(unittest.TestCase):
    def test_sorted_by_id(self):
        items = [_r("zebra", "MATCH"), _r("alpha", "MATCH"), _r("mid", "MATCH")]
        sorted_items = eh.sort_results(items)
        self.assertEqual([i["id"] for i in sorted_items], ["alpha", "mid", "zebra"])

    def test_sorted_by_result_when_id_equal(self):
        items = [
            _r("same", "EXIT_MISMATCH", argv=["a"]),
            _r("same", "CASE_ERROR", argv=["b"]),
        ]
        sorted_items = eh.sort_results(items)
        self.assertEqual([i["result"] for i in sorted_items], ["CASE_ERROR", "EXIT_MISMATCH"])

    def test_permutation_invariance(self):
        items = [_r("c", "MATCH"), _r("a", "MATCH"), _r("b", "TIMEOUT"), _r(None, "CASE_MALFORMED")]
        import random

        first = eh.sort_results(list(items))
        shuffled = list(items)
        random.Random(1234).shuffle(shuffled)
        second = eh.sort_results(shuffled)
        self.assertEqual(first, second)

    def test_none_id_sorts_first(self):
        items = [_r("a", "MATCH"), _r(None, "CASE_MALFORMED")]
        sorted_items = eh.sort_results(items)
        self.assertIsNone(sorted_items[0]["id"])

    def test_tiebreak_breaks_identical_id_and_result(self):
        # Two items identical in id and result but differing in another
        # field (argv) must still be ordered deterministically via the
        # canonical-JSON-dump tiebreak, and that order must be stable
        # across repeated calls / permuted input.
        a = _r("dup", "MATCH", argv=["aaa"])
        b = _r("dup", "MATCH", argv=["bbb"])
        result1 = eh.sort_results([a, b])
        result2 = eh.sort_results([b, a])
        self.assertEqual(result1, result2)
        # And the order is exactly what a plain json-dump comparison predicts.
        expect_first = a if eh.canonical_json_dumps(a) < eh.canonical_json_dumps(b) else b
        self.assertEqual(result1[0]["argv"], expect_first["argv"])

    def test_tiebreak_with_fully_identical_items_except_detail(self):
        a = _r("same", "TIMEOUT", detail="a")
        b = _r("same", "TIMEOUT", detail="b")
        out1 = eh.sort_results([a, b])
        out2 = eh.sort_results([b, a])
        self.assertEqual(out1, out2)
        self.assertNotEqual(out1[0]["detail"], out1[1]["detail"])

    def test_sort_result_list_length_preserved(self):
        items = [_r(f"id{i}", "MATCH") for i in range(20)]
        self.assertEqual(len(eh.sort_results(items)), 20)

    def test_sort_is_idempotent(self):
        items = [_r("b", "MATCH"), _r("a", "TIMEOUT")]
        once = eh.sort_results(items)
        twice = eh.sort_results(once)
        self.assertEqual(once, twice)


# ---------------------------------------------------------------------------
# Unit tests: build_report / summary / exit code selection
# ---------------------------------------------------------------------------

class TestBuildReport(unittest.TestCase):
    def test_all_match_gives_exit_0(self):
        report = eh.build_report([_r("a", "MATCH"), _r("b", "MATCH")])
        self.assertEqual(report["harness_exit_code"], 0)
        self.assertEqual(report["summary"], {"total": 2, "matched": 2, "failed": 0, "malformed": 0})

    def test_one_failure_gives_exit_1(self):
        report = eh.build_report([_r("a", "MATCH"), _r("b", "EXIT_MISMATCH")])
        self.assertEqual(report["harness_exit_code"], 1)
        self.assertEqual(report["summary"]["failed"], 1)

    def test_one_malformed_gives_exit_1(self):
        report = eh.build_report([_r("a", "MATCH"), _r(None, "CASE_MALFORMED")])
        self.assertEqual(report["harness_exit_code"], 1)
        self.assertEqual(report["summary"]["malformed"], 1)
        self.assertEqual(report["summary"]["failed"], 0)

    def test_empty_case_list_gives_exit_0(self):
        report = eh.build_report([])
        self.assertEqual(report["harness_exit_code"], 0)
        self.assertEqual(report["summary"]["total"], 0)

    def test_report_results_are_sorted(self):
        report = eh.build_report([_r("z", "MATCH"), _r("a", "MATCH")])
        self.assertEqual([r["id"] for r in report["results"]], ["a", "z"])

    def test_mixed_failure_and_malformed_counts(self):
        report = eh.build_report(
            [_r("a", "MATCH"), _r("b", "TIMEOUT"), _r(None, "CASE_MALFORMED"), _r("d", "CASE_ERROR")]
        )
        self.assertEqual(report["summary"], {"total": 4, "matched": 1, "failed": 2, "malformed": 1})
        self.assertEqual(report["harness_exit_code"], 1)


# ---------------------------------------------------------------------------
# Unit tests: write_report
# ---------------------------------------------------------------------------

class TestWriteReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_report_matches_canonical_dumps(self):
        report = {"a": 1, "b": [3, 2, 1]}
        out_path = os.path.join(self._tmp.name, "report.json")
        eh.write_report(report, out_path)
        with open(out_path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
        self.assertEqual(content, eh.canonical_json_dumps(report) + "\n")

    def test_write_report_uses_lf_only(self):
        report = {"x": 1}
        out_path = os.path.join(self._tmp.name, "report.json")
        eh.write_report(report, out_path)
        with open(out_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r", raw)

    def test_write_report_unwritable_path_raises_harness_error(self):
        bad_path = os.path.join(self._tmp.name, "no_such_dir", "report.json")
        with self.assertRaises(eh.HarnessError):
            eh.write_report({"x": 1}, bad_path)


# ---------------------------------------------------------------------------
# Manifest loading unit tests
# ---------------------------------------------------------------------------

class TestLoadManifest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _path(self, name):
        return os.path.join(self._tmp.name, name)

    def test_load_top_level_list(self):
        p = self._path("m.json")
        write_json(p, [make_case("a")])
        cases = eh.load_manifest(p)
        self.assertEqual(len(cases), 1)

    def test_load_cases_key_wrapper(self):
        p = self._path("m.json")
        write_json(p, {"cases": [make_case("a"), make_case("b")]})
        cases = eh.load_manifest(p)
        self.assertEqual(len(cases), 2)

    def test_missing_file_raises(self):
        with self.assertRaises(eh.HarnessError):
            eh.load_manifest(self._path("does_not_exist.json"))

    def test_invalid_json_raises(self):
        p = self._path("bad.json")
        write_text(p, "{not valid json")
        with self.assertRaises(eh.HarnessError):
            eh.load_manifest(p)

    def test_top_level_dict_without_cases_key_raises(self):
        p = self._path("m.json")
        write_json(p, {"foo": "bar"})
        with self.assertRaises(eh.HarnessError):
            eh.load_manifest(p)

    def test_top_level_string_raises(self):
        p = self._path("m.json")
        write_json(p, "just a string")
        with self.assertRaises(eh.HarnessError):
            eh.load_manifest(p)

    def test_top_level_number_raises(self):
        p = self._path("m.json")
        write_json(p, 42)
        with self.assertRaises(eh.HarnessError):
            eh.load_manifest(p)

    def test_empty_list_is_valid(self):
        p = self._path("m.json")
        write_json(p, [])
        self.assertEqual(eh.load_manifest(p), [])


# ---------------------------------------------------------------------------
# CLI integration tests (real subprocess invocations)
# ---------------------------------------------------------------------------

class CLITestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "root")
        os.mkdir(self.root)
        self.manifest_path = os.path.join(self.tmp, "manifest.json")
        self.out_path = os.path.join(self.tmp, "report.json")

    def tearDown(self):
        self._tmp.cleanup()

    def write_manifest(self, cases):
        write_json(self.manifest_path, cases)

    def run_harness(self, extra_args=None, timeout=15):
        args = [
            "--manifest", self.manifest_path,
            "--root", self.root,
            "-o", self.out_path,
        ] + (extra_args or [])
        return run_cli(args, timeout=timeout)

    def load_report(self):
        with open(self.out_path, "r", encoding="utf-8") as f:
            return json.load(f)


class TestCLIBasicRuns(CLITestBase):
    def test_single_passing_case_exit_0(self):
        self.write_manifest([make_case("ok", argv=["python3", "-c", "import sys; sys.exit(0)"])])
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 0)
        report = self.load_report()
        self.assertEqual(report["harness_exit_code"], 0)
        self.assertEqual(report["results"][0]["result"], "MATCH")

    def test_single_failing_case_exit_1(self):
        self.write_manifest(
            [make_case("bad", argv=["python3", "-c", "import sys; sys.exit(9)"], expect_exit=0)]
        )
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 1)
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "EXIT_MISMATCH")

    def test_malformed_manifest_json_exit_2(self):
        write_text(self.manifest_path, "{ this is not json ]")
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(os.path.exists(self.out_path))
        self.assertIn("not valid JSON", proc.stderr)

    def test_manifest_not_a_list_exit_2(self):
        write_json(self.manifest_path, {"nope": True})
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(os.path.exists(self.out_path))

    def test_manifest_missing_file_exit_2(self):
        proc = run_cli(
            ["--manifest", os.path.join(self.tmp, "nope.json"), "--root", self.root, "-o", self.out_path]
        )
        self.assertEqual(proc.returncode, 2)

    def test_bad_root_nonexistent_exit_2(self):
        self.write_manifest([make_case("a")])
        proc = run_cli(
            ["--manifest", self.manifest_path, "--root", os.path.join(self.tmp, "no_such_root"), "-o", self.out_path]
        )
        self.assertEqual(proc.returncode, 2)

    def test_bad_root_is_a_file_exit_2(self):
        file_as_root = os.path.join(self.tmp, "not_a_dir")
        write_text(file_as_root, "x")
        self.write_manifest([make_case("a")])
        proc = run_cli(["--manifest", self.manifest_path, "--root", file_as_root, "-o", self.out_path])
        self.assertEqual(proc.returncode, 2)

    def test_unwritable_output_path_exit_2(self):
        self.write_manifest([make_case("a")])
        bad_out = os.path.join(self.tmp, "no_such_subdir", "report.json")
        args = ["--manifest", self.manifest_path, "--root", self.root, "-o", bad_out]
        proc = run_cli(args)
        self.assertEqual(proc.returncode, 2)

    def test_missing_required_cli_arg_exit_2(self):
        proc = run_cli(["--root", self.root, "-o", self.out_path])
        self.assertEqual(proc.returncode, 2)

    def test_invalid_timeout_flag_exit_2(self):
        self.write_manifest([make_case("a")])
        proc = self.run_harness(["--timeout", "-5"])
        self.assertEqual(proc.returncode, 2)

    def test_help_flag_exits_zero(self):
        proc = run_cli(["--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stdout.lower())

    def test_empty_case_list_exit_0(self):
        self.write_manifest([])
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 0)
        report = self.load_report()
        self.assertEqual(report["summary"]["total"], 0)


class TestCLIMalformedCases(CLITestBase):
    def test_one_malformed_case_among_good_ones_continues_and_exit_1(self):
        good1 = make_case("good1", argv=["python3", "-c", "pass"], expect_exit=0)
        malformed = {"id": "broken", "cwd": "."}  # missing argv, expect_exit
        good2 = make_case("good2", argv=["python3", "-c", "pass"], expect_exit=0)
        self.write_manifest([good1, malformed, good2])
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 1)
        report = self.load_report()
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["matched"], 2)
        self.assertEqual(report["summary"]["malformed"], 1)
        results_by_id = {r["id"]: r for r in report["results"]}
        self.assertEqual(results_by_id["good1"]["result"], "MATCH")
        self.assertEqual(results_by_id["good2"]["result"], "MATCH")
        self.assertEqual(results_by_id["broken"]["result"], "CASE_MALFORMED")

    def test_all_malformed_cases_exit_1_not_2(self):
        self.write_manifest([{"cwd": "."}, {"id": "x"}])
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 1)
        report = self.load_report()
        self.assertEqual(report["summary"]["malformed"], 2)

    def test_malformed_case_detail_lists_errors(self):
        self.write_manifest([{"id": "broken"}])
        proc = self.run_harness()
        report = self.load_report()
        detail = report["results"][0]["detail"]
        self.assertIn("cwd", detail)
        self.assertIn("argv", detail)
        self.assertIn("expect_exit", detail)


class TestCLIResultCodes(CLITestBase):
    def _run_single(self, **case_kwargs):
        self.write_manifest([make_case("t", **case_kwargs)])
        proc = self.run_harness()
        report = self.load_report()
        return proc, report["results"][0]

    def test_match(self):
        proc, result = self._run_single(argv=["python3", "-c", "import sys; sys.exit(0)"], expect_exit=0)
        self.assertEqual(result["result"], "MATCH")
        self.assertEqual(proc.returncode, 0)

    def test_exit_mismatch(self):
        proc, result = self._run_single(argv=["python3", "-c", "import sys; sys.exit(1)"], expect_exit=0)
        self.assertEqual(result["result"], "EXIT_MISMATCH")
        self.assertEqual(proc.returncode, 1)

    def test_stdout_not_json(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "print('plain text')"],
            expect_exit=0,
            expect_stdout_canonical_json=True,
        )
        self.assertEqual(result["result"], "STDOUT_NOT_JSON")
        self.assertEqual(proc.returncode, 1)

    def test_stdout_not_canonical_spaces(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "print('{\"a\": 1, \"b\": 2}')"],
            expect_exit=0,
            expect_stdout_canonical_json=True,
        )
        self.assertEqual(result["result"], "STDOUT_NOT_CANONICAL")

    def test_stdout_not_canonical_unsorted(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "print('{\"z\":1,\"a\":2}')"],
            expect_exit=0,
            expect_stdout_canonical_json=True,
        )
        self.assertEqual(result["result"], "STDOUT_NOT_CANONICAL")

    def test_stdout_missing_substring(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "print('hello')"],
            expect_exit=0,
            expect_stdout_contains="xyz",
        )
        self.assertEqual(result["result"], "STDOUT_MISSING_SUBSTRING")

    def test_stderr_missing_substring(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "import sys; print('warn', file=sys.stderr)"],
            expect_exit=0,
            expect_stderr_contains="fatal",
        )
        self.assertEqual(result["result"], "STDERR_MISSING_SUBSTRING")

    def test_timeout(self):
        proc, result = self._run_single(
            argv=["python3", "-c", "import time; time.sleep(3)"],
            expect_exit=0,
            timeout_seconds=0.2,
        )
        self.assertEqual(result["result"], "TIMEOUT")
        self.assertEqual(proc.returncode, 1)

    def test_case_error_missing_executable(self):
        proc, result = self._run_single(argv=["no_such_binary_at_all_xyz"], expect_exit=0)
        self.assertEqual(result["result"], "CASE_ERROR")
        self.assertEqual(proc.returncode, 1)

    def test_case_error_bad_cwd(self):
        self.write_manifest([make_case("t", cwd="totally/missing/dir")])
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "CASE_ERROR")
        self.assertEqual(proc.returncode, 1)


class TestCLIThreeExitCodesDemo(CLITestBase):
    """Explicitly demonstrates all three harness exit codes in one place."""

    def test_exit_0_all_match(self):
        self.write_manifest([make_case("a", argv=["python3", "-c", "pass"], expect_exit=0)])
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 0)

    def test_exit_1_some_fail(self):
        self.write_manifest(
            [make_case("a", argv=["python3", "-c", "import sys; sys.exit(1)"], expect_exit=0)]
        )
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 1)

    def test_exit_2_harness_error(self):
        write_text(self.manifest_path, "not json")
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 2)


# ---------------------------------------------------------------------------
# Determinism / ordering / relocation
# ---------------------------------------------------------------------------

class TestDeterminism(CLITestBase):
    def test_report_byte_stable_across_two_runs(self):
        self.write_manifest(
            [
                make_case("a", argv=["python3", "-c", "print('{\"x\":1}')"], expect_stdout_canonical_json=True),
                make_case("b", argv=["python3", "-c", "import sys; sys.exit(2)"], expect_exit=2),
            ]
        )
        out2 = os.path.join(self.tmp, "report2.json")
        self.run_harness()
        with open(self.out_path, "rb") as f:
            first = f.read()
        proc2 = run_cli(["--manifest", self.manifest_path, "--root", self.root, "-o", out2])
        with open(out2, "rb") as f:
            second = f.read()
        self.assertEqual(first, second)

    def test_permuted_manifest_order_yields_identical_report(self):
        cases = [
            make_case("a1", argv=["python3", "-c", "pass"], expect_exit=0),
            make_case("a2", argv=["python3", "-c", "import sys; sys.exit(1)"], expect_exit=0),
            make_case("a3", argv=["python3", "-c", "import sys; sys.exit(2)"], expect_exit=2),
        ]
        out_forward = os.path.join(self.tmp, "forward.json")
        out_reversed = os.path.join(self.tmp, "reversed.json")

        write_json(self.manifest_path, cases)
        run_cli(["--manifest", self.manifest_path, "--root", self.root, "-o", out_forward])

        manifest2 = os.path.join(self.tmp, "manifest2.json")
        write_json(manifest2, list(reversed(cases)))
        run_cli(["--manifest", manifest2, "--root", self.root, "-o", out_reversed])

        with open(out_forward, "rb") as f:
            forward_bytes = f.read()
        with open(out_reversed, "rb") as f:
            reversed_bytes = f.read()
        self.assertEqual(forward_bytes, reversed_bytes)


class TestRelocation(unittest.TestCase):
    """Copies the whole build tree to a different absolute path with a
    different name and confirms the report is byte-identical."""

    def test_relocated_tree_produces_identical_report(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            copy_a = os.path.join(tmp1, "copy_a_name")
            copy_b = os.path.join(tmp2, "totally_different_dirname")
            shutil.copytree(THIS_DIR, copy_a)
            shutil.copytree(THIS_DIR, copy_b)

            out_a = os.path.join(tmp1, "report_a.json")
            out_b = os.path.join(tmp2, "report_b.json")

            proc_a = subprocess.run(
                [
                    sys.executable,
                    os.path.join(copy_a, "exitharness.py"),
                    "--manifest", os.path.join(copy_a, "manifest_example.json"),
                    "--root", os.path.join(copy_a, "fixture_tools"),
                    "-o", out_a,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            proc_b = subprocess.run(
                [
                    sys.executable,
                    os.path.join(copy_b, "exitharness.py"),
                    "--manifest", os.path.join(copy_b, "manifest_example.json"),
                    "--root", os.path.join(copy_b, "fixture_tools"),
                    "-o", out_b,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc_a.returncode, 0)
            self.assertEqual(proc_b.returncode, 0)
            with open(out_a, "rb") as f:
                bytes_a = f.read()
            with open(out_b, "rb") as f:
                bytes_b = f.read()
            self.assertEqual(bytes_a, bytes_b)
            # No absolute path fragment from either tree may appear in the report.
            self.assertNotIn(copy_a.encode(), bytes_a)
            self.assertNotIn(copy_b.encode(), bytes_b)


# ---------------------------------------------------------------------------
# Unicode and CRLF
# ---------------------------------------------------------------------------

class TestUnicodeAndCRLF(CLITestBase):
    def test_unicode_stdout_substring_match(self):
        self.write_manifest(
            [
                make_case(
                    "unicode1",
                    argv=["python3", "-c", "print('caf\\u00e9 \\u2603 snowman')"],
                    expect_exit=0,
                    expect_stdout_contains="café",
                )
            ]
        )
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "MATCH")

    def test_unicode_canonical_json_stdout(self):
        prog = 'import json,sys; sys.stdout.write(json.dumps({"city":"caf\\u00e9"},sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\\n")'
        self.write_manifest(
            [make_case("unicode2", argv=["python3", "-c", prog], expect_exit=0, expect_stdout_canonical_json=True)]
        )
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "MATCH")

    def test_unicode_id_in_manifest_roundtrips_in_report(self):
        self.write_manifest([make_case("café-☃", argv=["python3", "-c", "pass"], expect_exit=0)])
        proc = self.run_harness()
        with open(self.out_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["results"][0]["id"], "café-☃")

    def test_report_file_is_ascii_only_bytes(self):
        self.write_manifest([make_case("café", argv=["python3", "-c", "pass"], expect_exit=0)])
        self.run_harness()
        with open(self.out_path, "rb") as f:
            raw = f.read()
        raw.decode("ascii")  # must not raise: ensure_ascii=True means pure ASCII bytes

    def test_manifest_with_crlf_line_endings_parses(self):
        cases = [make_case("crlf1", argv=["python3", "-c", "pass"], expect_exit=0)]
        text = json.dumps(cases).replace("\n", "\r\n")
        with open(self.manifest_path, "wb") as f:
            f.write(text.encode("utf-8"))
        proc = self.run_harness()
        self.assertEqual(proc.returncode, 0)

    def test_stdout_contains_match_across_crlf_output(self):
        self.write_manifest(
            [
                make_case(
                    "crlf2",
                    argv=["python3", "-c", "import sys; sys.stdout.write('line1\\r\\nline2\\r\\n')"],
                    expect_exit=0,
                    expect_stdout_contains="line2",
                )
            ]
        )
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "MATCH")

    def test_report_never_contains_cr_bytes(self):
        self.write_manifest([make_case("x", argv=["python3", "-c", "pass"], expect_exit=0)])
        self.run_harness()
        with open(self.out_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r", raw)


# ---------------------------------------------------------------------------
# Bug-hunt regression tests (path escape via absolute / ".." cwd)
# ---------------------------------------------------------------------------

class TestBugHuntPathEscape(CLITestBase):
    """Pins the bug found during the mandatory bug hunt: os.path.join()
    silently discards the base path when the second argument is
    absolute, so a naive `os.path.join(root_abs, case_cwd)` would let a
    manifest case whose "cwd" is an absolute path (or a ".."-laden
    relative path) escape --root entirely. Fixed in _resolve_case_cwd.
    """

    def test_absolute_cwd_does_not_escape_root_via_cli(self):
        self.write_manifest([make_case("escape", cwd="/etc", argv=["python3", "-c", "pass"], expect_exit=0)])
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "CASE_ERROR")
        self.assertIn("relative path", report["results"][0]["detail"])
        # Confirms the harness did NOT execute in /etc.
        self.assertEqual(proc.returncode, 1)

    def test_dotdot_cwd_does_not_escape_root_via_cli(self):
        self.write_manifest(
            [make_case("escape2", cwd="../../../../../../../etc", argv=["python3", "-c", "pass"], expect_exit=0)]
        )
        proc = self.run_harness()
        report = self.load_report()
        self.assertEqual(report["results"][0]["result"], "CASE_ERROR")
        self.assertIn("escapes --root", report["results"][0]["detail"])

    def test_os_path_join_absolute_discard_demonstrated(self):
        # Documents the raw stdlib footgun that motivated the fix.
        root = "/tmp/some/root"
        self.assertEqual(os.path.join(root, "/etc"), "/etc")
        self.assertNotEqual(os.path.join(root, "/etc"), os.path.join(root, "etc"))


if __name__ == "__main__":
    unittest.main()

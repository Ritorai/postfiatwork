"""test_claimhist.py -- test suite for claimhist.py (commit-claim-auditor).

Every filesystem fixture used here is created inside a
tempfile.TemporaryDirectory() and removed only via that context
manager's own cleanup -- never via a manually constructed shutil.rmtree
call on a derived path.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CLAIMHIST_PATH = os.path.join(THIS_DIR, "claimhist.py")

sys.path.insert(0, THIS_DIR)
import claimhist  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def write_text(path, text):
    """Write text verbatim (no newline translation) as UTF-8."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def run_cli(args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, CLAIMHIST_PATH] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, check=True
    )


def init_git_repo(path):
    git(["init", "-q"], cwd=path)
    git(["config", "user.email", "test@example.com"], cwd=path)
    git(["config", "user.name", "Test User"], cwd=path)


def git_commit_all(path, message):
    git(["add", "-A"], cwd=path)
    git(["commit", "-q", "-m", message], cwd=path)


class TempDirCase(unittest.TestCase):
    """Base class providing a fresh, safely-scoped temp directory per test."""

    def make_tmp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

class TestCanonicalDumps(unittest.TestCase):
    def test_sorted_keys(self):
        out = claimhist.canonical_dumps({"b": 1, "a": 2})
        self.assertEqual(out, '{"a":2,"b":1}')

    def test_tight_separators_no_spaces(self):
        out = claimhist.canonical_dumps({"a": [1, 2, 3]})
        self.assertNotIn(" ", out)

    def test_ensure_ascii_escapes_unicode(self):
        out = claimhist.canonical_dumps({"x": "café"})
        self.assertIn("\\u00e9", out)
        self.assertNotIn("é", out)

    def test_no_trailing_newline_from_function(self):
        out = claimhist.canonical_dumps({"a": 1})
        self.assertFalse(out.endswith("\n"))

    def test_idempotent(self):
        obj = {"z": 1, "a": [3, 2, 1], "m": None}
        self.assertEqual(claimhist.canonical_dumps(obj), claimhist.canonical_dumps(obj))

    def test_bool_and_null_serialize_correctly(self):
        out = claimhist.canonical_dumps({"t": True, "f": False, "n": None})
        self.assertEqual(out, '{"f":false,"n":null,"t":true}')

    def test_nested_dict_sorted_recursively(self):
        out = claimhist.canonical_dumps({"outer": {"z": 1, "a": 2}})
        self.assertEqual(out, '{"outer":{"a":2,"z":1}}')

    def test_list_order_preserved_not_sorted(self):
        out = claimhist.canonical_dumps({"a": [3, 1, 2]})
        self.assertEqual(out, '{"a":[3,1,2]}')


# --------------------------------------------------------------------------
# read_text_lines
# --------------------------------------------------------------------------

class TestReadTextLines(TempDirCase):
    def test_unix_newlines(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"one\ntwo\nthree\n")
        self.assertEqual(claimhist.read_text_lines(p), ["one", "two", "three"])

    def test_crlf_newlines(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"one\r\ntwo\r\nthree\r\n")
        self.assertEqual(claimhist.read_text_lines(p), ["one", "two", "three"])

    def test_bare_cr_newlines(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"one\rtwo\rthree\r")
        self.assertEqual(claimhist.read_text_lines(p), ["one", "two", "three"])

    def test_mixed_newlines(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"one\ntwo\r\nthree\r\n")
        self.assertEqual(claimhist.read_text_lines(p), ["one", "two", "three"])

    def test_no_trailing_newline_preserved(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"one\ntwo")
        self.assertEqual(claimhist.read_text_lines(p), ["one", "two"])

    def test_invalid_utf8_replaced_not_raised(self):
        d = self.make_tmp()
        p = os.path.join(d, "f.txt")
        write_bytes(p, b"good line\n\xff\xfe bad bytes\nmore good\n")
        lines = claimhist.read_text_lines(p)
        self.assertEqual(len(lines), 3)
        self.assertIn("good line", lines[0])
        self.assertIn("more good", lines[2])


# --------------------------------------------------------------------------
# find_filename_candidates
# --------------------------------------------------------------------------

class TestFindFilenameCandidates(unittest.TestCase):
    def test_single_backtick(self):
        self.assertEqual(
            claimhist.find_filename_candidates("see `report.txt` here"), ["report.txt"]
        )

    def test_multiple_distinct_backticks_order_preserved(self):
        self.assertEqual(
            claimhist.find_filename_candidates("`one.txt` and `two.txt`"),
            ["one.txt", "two.txt"],
        )

    def test_duplicate_backticks_deduped(self):
        self.assertEqual(
            claimhist.find_filename_candidates("`a.txt` again `a.txt`"), ["a.txt"]
        )

    def test_bare_single_with_extension(self):
        self.assertEqual(
            claimhist.find_filename_candidates("check report.txt now"), ["report.txt"]
        )

    def test_bare_with_path_segments(self):
        self.assertEqual(
            claimhist.find_filename_candidates("see sub/dir/report.txt now"),
            ["sub/dir/report.txt"],
        )

    def test_bare_strips_surrounding_punctuation(self):
        self.assertEqual(
            claimhist.find_filename_candidates("(see report.txt)"), ["report.txt"]
        )

    def test_masks_hash_before_bare_match_regression(self):
        h = "a" * 64
        line = f"See {h}.report for details."
        self.assertEqual(claimhist.find_filename_candidates(line), [])

    def test_masks_hash_before_backtick_match(self):
        h = "b" * 64
        line = f"See `{h}.report` for details."
        self.assertEqual(claimhist.find_filename_candidates(line), [])

    def test_no_candidates_in_plain_prose(self):
        self.assertEqual(
            claimhist.find_filename_candidates("just some prose with no files"), []
        )

    def test_backtick_preferred_over_bare_when_both_present(self):
        self.assertEqual(
            claimhist.find_filename_candidates("bare.txt and `quoted.txt`"),
            ["quoted.txt"],
        )


# --------------------------------------------------------------------------
# associate_filename
# --------------------------------------------------------------------------

class TestAssociateFilename(unittest.TestCase):
    def test_same_line_single_candidate(self):
        lines = ["hash here `report.txt`"]
        self.assertEqual(claimhist.associate_filename(lines, 0), ("report.txt", None))

    def test_same_line_ambiguous_backticks(self):
        lines = ["`a.txt` `b.txt`"]
        self.assertEqual(
            claimhist.associate_filename(lines, 0), (None, "ambiguous_filename_association")
        )

    def test_same_line_ambiguous_bare(self):
        lines = ["a.txt and b.txt"]
        self.assertEqual(
            claimhist.associate_filename(lines, 0), (None, "ambiguous_filename_association")
        )

    def test_falls_back_to_previous_line(self):
        lines = ["File: report.txt", "the hash line"]
        self.assertEqual(claimhist.associate_filename(lines, 1), ("report.txt", None))

    def test_falls_back_to_next_line_when_no_previous(self):
        lines = ["the hash line", "File: report.txt"]
        self.assertEqual(claimhist.associate_filename(lines, 0), ("report.txt", None))

    def test_skips_multiple_blank_lines_backward(self):
        lines = ["File: report.txt", "", "", "the hash line"]
        self.assertEqual(claimhist.associate_filename(lines, 3), ("report.txt", None))

    def test_skips_multiple_blank_lines_forward(self):
        lines = ["the hash line", "", "", "File: report.txt"]
        self.assertEqual(claimhist.associate_filename(lines, 0), ("report.txt", None))

    def test_no_candidate_anywhere(self):
        lines = ["", "the hash line", ""]
        self.assertEqual(
            claimhist.associate_filename(lines, 1), (None, "no_filename_association")
        )

    def test_ambiguous_previous_line(self):
        lines = ["a.txt and b.txt", "the hash line"]
        self.assertEqual(
            claimhist.associate_filename(lines, 1), (None, "ambiguous_filename_association")
        )

    def test_ambiguous_next_line_when_previous_empty(self):
        lines = ["the hash line", "a.txt and b.txt"]
        self.assertEqual(
            claimhist.associate_filename(lines, 0), (None, "ambiguous_filename_association")
        )

    def test_prev_checked_before_next(self):
        lines = ["File: prev.txt", "the hash line", "File: next.txt"]
        self.assertEqual(claimhist.associate_filename(lines, 1), ("prev.txt", None))


# --------------------------------------------------------------------------
# SHA256SUM_LINE_RE (transcript form)
# --------------------------------------------------------------------------

class TestSha256sumTranscriptRegex(unittest.TestCase):
    def test_two_space_separator(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h}  report.txt")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "report.txt")

    def test_one_space_separator(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h} report.txt")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "report.txt")

    def test_binary_asterisk_marker(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h} *report.bin")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "report.bin")

    def test_leading_whitespace_before_hash(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"   {h}  report.txt")
        self.assertIsNotNone(m)

    def test_filename_with_spaces_preserved(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h}  my report file.txt")
        self.assertEqual(m.group(2), "my report file.txt")

    def test_extensionless_filename(self):
        h = "a" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h}  Makefile")
        self.assertEqual(m.group(2), "Makefile")

    def test_uppercase_hex_accepted(self):
        h = "A" * 64
        m = claimhist.SHA256SUM_LINE_RE.match(f"{h}  report.txt")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).lower(), "a" * 64)

    def test_prose_line_does_not_match(self):
        m = claimhist.SHA256SUM_LINE_RE.match("This is unrelated prose.")
        self.assertIsNone(m)


# --------------------------------------------------------------------------
# SHA_TOKEN_RE
# --------------------------------------------------------------------------

class TestShaTokenRe(unittest.TestCase):
    def test_matches_standalone_64_hex(self):
        h = "a" * 64
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(h), [h])

    def test_does_not_match_63_chars(self):
        h = "a" * 63
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(h), [])

    def test_does_not_match_65_char_contiguous_run(self):
        h = "a" * 65
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(h), [])

    def test_case_insensitive(self):
        h = "ABCDEF0123456789" * 4
        self.assertEqual(len(claimhist.SHA_TOKEN_RE.findall(h)), 1)

    def test_multiple_tokens_per_line(self):
        h1 = "a" * 64
        h2 = "b" * 64
        line = f"{h1} then {h2}"
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(line), [h1, h2])

    def test_underscore_adjacent_hash_still_matches(self):
        h = "a" * 64
        line = f"prefix_{h}_suffix"
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(line), [h])

    def test_hash_adjacent_to_dot_matches_as_own_token(self):
        h = "a" * 64
        line = f"{h}.report"
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(line), [h])

    def test_hash_inside_backticks_matches(self):
        h = "a" * 64
        line = f"`{h}`"
        self.assertEqual(claimhist.SHA_TOKEN_RE.findall(line), [h])


# --------------------------------------------------------------------------
# match_testcount
# --------------------------------------------------------------------------

class TestMatchTestcount(unittest.TestCase):
    def test_ran_tests_in_shape(self):
        r = claimhist.match_testcount("Ran 137 tests in 0.045s")
        self.assertEqual(r, {"malformed": False, "value": 137, "shape": "ran_tests_in"})

    def test_bold_across_tools_shape(self):
        r = claimhist.match_testcount("**476 tests across 13 tools**")
        self.assertEqual(
            r, {"malformed": False, "value": 476, "shape": "bold_across_tools"}
        )

    def test_bare_tests_shape(self):
        r = claimhist.match_testcount("we have 42 tests total")
        self.assertEqual(r, {"malformed": False, "value": 42, "shape": "bare_tests"})

    def test_ran_tests_in_wins_priority_over_bare(self):
        r = claimhist.match_testcount("Ran 5 tests in 0.01s (target was 999 tests)")
        self.assertEqual(r["shape"], "ran_tests_in")
        self.assertEqual(r["value"], 5)

    def test_bold_wins_priority_over_bare(self):
        r = claimhist.match_testcount("**10 tests across 2 tools** (see 999 tests below)")
        self.assertEqual(r["shape"], "bold_across_tools")
        self.assertEqual(r["value"], 10)

    def test_comma_guard_on_bare_shape(self):
        r = claimhist.match_testcount("we ran 1,234 tests total")
        self.assertEqual(r["malformed"], True)
        self.assertEqual(r["reason"], "ambiguous_number_format")

    def test_comma_guard_via_bold_fallback(self):
        r = claimhist.match_testcount("**1,234 tests across 5 tools**")
        self.assertTrue(r["malformed"])
        self.assertEqual(r["reason"], "ambiguous_number_format")

    def test_comma_guard_via_ran_tests_fallback(self):
        r = claimhist.match_testcount("Ran 1,234 tests in 0.5s")
        self.assertTrue(r["malformed"])

    def test_no_match_on_unrelated_line(self):
        self.assertIsNone(claimhist.match_testcount("nothing to see here"))

    def test_singular_test_matches(self):
        r = claimhist.match_testcount("Ran 1 test in 0.001s")
        self.assertEqual(r, {"malformed": False, "value": 1, "shape": "ran_tests_in"})

    def test_large_number_matches(self):
        r = claimhist.match_testcount("Ran 100000 tests in 12.0s")
        self.assertEqual(r["value"], 100000)

    def test_zero_tests(self):
        r = claimhist.match_testcount("Ran 0 tests in 0.000s")
        self.assertEqual(r["value"], 0)


# --------------------------------------------------------------------------
# resolve_target
# --------------------------------------------------------------------------

class TestResolveTarget(TempDirCase):
    def test_resolves_relative_to_source_dir(self):
        root = self.make_tmp()
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        write_text(os.path.join(sub, "data.txt"), "hi")
        result = claimhist.resolve_target(root, sub, "data.txt")
        self.assertEqual(result, os.path.join(sub, "data.txt"))

    def test_resolves_relative_to_root_when_not_in_source_dir(self):
        root = self.make_tmp()
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        write_text(os.path.join(root, "data.txt"), "hi")
        result = claimhist.resolve_target(root, sub, "data.txt")
        self.assertEqual(result, os.path.join(root, "data.txt"))

    def test_prefers_source_dir_over_root(self):
        root = self.make_tmp()
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        write_text(os.path.join(root, "data.txt"), "root version")
        write_text(os.path.join(sub, "data.txt"), "sub version")
        result = claimhist.resolve_target(root, sub, "data.txt")
        self.assertEqual(result, os.path.join(sub, "data.txt"))

    def test_missing_everywhere_returns_none(self):
        root = self.make_tmp()
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        self.assertIsNone(claimhist.resolve_target(root, sub, "ghost.txt"))

    def test_handles_dot_slash_prefix(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "data.txt"), "hi")
        result = claimhist.resolve_target(root, root, "./data.txt")
        self.assertEqual(result, os.path.join(root, "data.txt"))

    def test_handles_subdirectory_path(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "sub"))
        write_text(os.path.join(root, "sub", "data.txt"), "hi")
        result = claimhist.resolve_target(root, root, "sub/data.txt")
        self.assertEqual(result, os.path.join(root, "sub", "data.txt"))


# --------------------------------------------------------------------------
# sha256_of_file
# --------------------------------------------------------------------------

class TestSha256OfFile(TempDirCase):
    def test_known_content(self):
        root = self.make_tmp()
        p = os.path.join(root, "f.txt")
        write_bytes(p, b"hello world")
        self.assertEqual(claimhist.sha256_of_file(p), sha256_hex(b"hello world"))

    def test_empty_file(self):
        root = self.make_tmp()
        p = os.path.join(root, "empty.txt")
        write_bytes(p, b"")
        self.assertEqual(claimhist.sha256_of_file(p), sha256_hex(b""))

    def test_larger_chunked_file(self):
        root = self.make_tmp()
        p = os.path.join(root, "big.bin")
        data = os.urandom(1 << 21)  # 2 MiB, exercises the chunked read loop
        write_bytes(p, data)
        self.assertEqual(claimhist.sha256_of_file(p), sha256_hex(data))


# --------------------------------------------------------------------------
# find_test_module
# --------------------------------------------------------------------------

class TestFindTestModule(TempDirCase):
    def test_zero_test_files(self):
        root = self.make_tmp()
        self.assertEqual(claimhist.find_test_module(root), [])

    def test_exactly_one(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_foo.py"), "import unittest\n")
        self.assertEqual(claimhist.find_test_module(root), ["test_foo.py"])

    def test_multiple_sorted(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_b.py"), "")
        write_text(os.path.join(root, "test_a.py"), "")
        self.assertEqual(claimhist.find_test_module(root), ["test_a.py", "test_b.py"])

    def test_ignores_non_test_prefixed_files(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "helpers.py"), "")
        write_text(os.path.join(root, "test_real.py"), "")
        self.assertEqual(claimhist.find_test_module(root), ["test_real.py"])

    def test_ignores_directories_named_like_test_files(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "test_dir.py"))
        write_text(os.path.join(root, "test_real.py"), "")
        self.assertEqual(claimhist.find_test_module(root), ["test_real.py"])


# --------------------------------------------------------------------------
# run_test_module
# --------------------------------------------------------------------------

PASSING_MODULE = """
import unittest

class T(unittest.TestCase):
    def test_1(self):
        self.assertTrue(True)
    def test_2(self):
        self.assertTrue(True)
    def test_3(self):
        self.assertTrue(True)
"""

FAILING_MODULE = """
import unittest

class T(unittest.TestCase):
    def test_1(self):
        self.assertTrue(True)
    def test_2(self):
        self.assertEqual(1, 2)
"""

SYNTAX_ERROR_MODULE = "this is not valid python (((\n"


class TestRunTestModule(TempDirCase):
    def test_passing_suite_returns_count(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_pass.py"), PASSING_MODULE)
        count, reason = claimhist.run_test_module(root, "test_pass")
        self.assertEqual(count, 3)
        self.assertIsNone(reason)

    def test_failing_suite_still_returns_count(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_fail.py"), FAILING_MODULE)
        count, reason = claimhist.run_test_module(root, "test_fail")
        self.assertEqual(count, 2)
        self.assertIsNone(reason)

    def test_syntax_error_module_fails(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_bad.py"), SYNTAX_ERROR_MODULE)
        count, reason = claimhist.run_test_module(root, "test_bad")
        self.assertIsNone(count)
        self.assertEqual(reason, "unittest_execution_failed")

    def test_module_name_mismatch_reports_synthetic_import_failure_test(self):
        # unittest's own behavior: importing a nonexistent module name
        # yields a single synthetic "_FailedTest" case, so the summary
        # line still reads "Ran 1 test in ..." (with return code 1).
        # This documents that behavior rather than asserting a crash.
        root = self.make_tmp()
        write_text(os.path.join(root, "test_pass.py"), PASSING_MODULE)
        count, reason = claimhist.run_test_module(root, "test_does_not_exist")
        self.assertEqual(count, 1)
        self.assertIsNone(reason)

    def test_python3_missing_from_path_fails_gracefully(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_pass.py"), PASSING_MODULE)
        with unittest.mock.patch.dict(os.environ, {"PATH": "/totally/nonexistent/bin"}, clear=False):
            saved = os.environ.get("PATH")
            os.environ["PATH"] = "/totally/nonexistent/bin"
            try:
                count, reason = claimhist.run_test_module(root, "test_pass")
            finally:
                os.environ["PATH"] = saved
        self.assertIsNone(count)
        self.assertEqual(reason, "unittest_execution_failed")


import unittest.mock  # noqa: E402  (used just above)


# --------------------------------------------------------------------------
# git_provenance
# --------------------------------------------------------------------------

class TestGitProvenance(TempDirCase):
    def _make_repo_with_two_commits(self):
        root = self.make_tmp()
        init_git_repo(root)
        write_text(os.path.join(root, "f.txt"), "line1\nline2\nline3\n")
        git_commit_all(root, "c1")
        write_text(os.path.join(root, "f.txt"), "line1\nCHANGED\nline3\n")
        git_commit_all(root, "c2")
        return root

    def test_committed_line_has_commit_and_date(self):
        root = self._make_repo_with_two_commits()
        prov = claimhist.git_provenance(root, "f.txt", 2, {})
        self.assertIsNotNone(prov["commit"])
        self.assertEqual(len(prov["commit"]), 40)
        self.assertIsNone(prov["note"])
        self.assertIsNotNone(prov["author_date"])

    def test_author_date_is_iso8601(self):
        root = self._make_repo_with_two_commits()
        prov = claimhist.git_provenance(root, "f.txt", 2, {})
        # ISO-8601 with a timezone offset, e.g. 2026-08-03T12:00:00+00:00
        self.assertRegex(
            prov["author_date"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"
        )

    def test_commit_sha_is_40_hex_chars(self):
        root = self._make_repo_with_two_commits()
        prov = claimhist.git_provenance(root, "f.txt", 2, {})
        self.assertTrue(all(c in "0123456789abcdef" for c in prov["commit"]))

    def test_unchanged_line_attributes_to_first_commit(self):
        root = self._make_repo_with_two_commits()
        log = subprocess.run(
            ["git", "log", "--format=%H"], cwd=root, capture_output=True, text=True
        )
        shas = log.stdout.strip().splitlines()
        first_sha, second_sha = shas[-1], shas[0]
        prov_line1 = claimhist.git_provenance(root, "f.txt", 1, {})
        prov_line2 = claimhist.git_provenance(root, "f.txt", 2, {})
        self.assertEqual(prov_line1["commit"], first_sha)
        self.assertEqual(prov_line2["commit"], second_sha)
        self.assertNotEqual(prov_line1["commit"], prov_line2["commit"])

    def test_uncommitted_line_note(self):
        root = self._make_repo_with_two_commits()
        write_text(os.path.join(root, "f.txt"), "line1\nCHANGED\nline3\nUNCOMMITTED\n")
        prov = claimhist.git_provenance(root, "f.txt", 4, {})
        self.assertIsNone(prov["commit"])
        self.assertIsNone(prov["author_date"])
        self.assertEqual(prov["note"], "UNCOMMITTED_LINE")

    def test_not_a_git_repo(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "f.txt"), "line1\n")
        prov = claimhist.git_provenance(root, "f.txt", 1, {})
        self.assertIsNone(prov["commit"])
        self.assertIsNone(prov["author_date"])
        self.assertEqual(prov["note"], "GIT_UNAVAILABLE")

    def test_git_binary_unavailable(self):
        root = self._make_repo_with_two_commits()
        cache = {"git_present": False}
        prov = claimhist.git_provenance(root, "f.txt", 1, cache)
        self.assertEqual(prov["note"], "GIT_UNAVAILABLE")

    def test_line_beyond_end_of_file_graceful(self):
        root = self._make_repo_with_two_commits()
        prov = claimhist.git_provenance(root, "f.txt", 999, {})
        self.assertEqual(prov["note"], "GIT_UNAVAILABLE")
        self.assertIsNone(prov["commit"])

    def test_cache_is_populated_and_reused(self):
        root = self._make_repo_with_two_commits()
        cache = {}
        claimhist.git_provenance(root, "f.txt", 1, cache)
        self.assertIn("git_present", cache)
        self.assertIn("is_worktree", cache)
        self.assertTrue(cache["git_present"])
        self.assertTrue(cache["is_worktree"])

    def test_single_line_file(self):
        root = self.make_tmp()
        init_git_repo(root)
        write_text(os.path.join(root, "one.txt"), "only line\n")
        git_commit_all(root, "only commit")
        prov = claimhist.git_provenance(root, "one.txt", 1, {})
        self.assertIsNotNone(prov["commit"])
        self.assertIsNone(prov["note"])


# --------------------------------------------------------------------------
# build_hash_claim / scan_file (hash side)
# --------------------------------------------------------------------------

class TestHashClaimRecompute(TempDirCase):
    def test_current_when_hash_matches(self):
        root = self.make_tmp()
        data = b"payload"
        write_bytes(os.path.join(root, "data.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  data.txt\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["status"], "CURRENT")
        self.assertEqual(claims[0]["recomputed_hash"], h)

    def test_stale_when_hash_mismatches(self):
        root = self.make_tmp()
        write_bytes(os.path.join(root, "data.txt"), b"new content")
        old_h = sha256_hex(b"old content")
        write_text(os.path.join(root, "README.md"), f"{old_h}  data.txt\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["status"], "STALE")
        self.assertEqual(claims[0]["reason"], "hash_mismatch")
        self.assertEqual(claims[0]["recomputed_hash"], sha256_hex(b"new content"))

    def test_missing_source_when_file_absent(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"{h}  ghost.txt\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["status"], "MISSING_SOURCE")
        self.assertEqual(claims[0]["reason"], "referenced_file_not_found")

    def test_malformed_no_filename_association(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"random prose\n\n{h}\n\nmore prose\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["status"], "MALFORMED")
        self.assertEqual(claims[0]["reason"], "no_filename_association")

    def test_malformed_ambiguous_filename_association(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"`one.txt` `two.txt` {h}\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["status"], "MALFORMED")
        self.assertEqual(claims[0]["reason"], "ambiguous_filename_association")

    def test_multiple_stray_hashes_become_separate_claims(self):
        root = self.make_tmp()
        h1 = "a" * 64
        h2 = "b" * 64
        write_text(os.path.join(root, "README.md"), f"stray {h1} and stray {h2} with no files\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(len(claims), 2)
        self.assertEqual({c["claimed_hash"] for c in claims}, {h1, h2})

    def test_transcript_shape_fields(self):
        root = self.make_tmp()
        data = b"x"
        write_bytes(os.path.join(root, "data.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  data.txt\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["claim_shape"], "sha256sum_transcript")
        self.assertEqual(claims[0]["associated_target"], "data.txt")

    def test_prose_shape_fields(self):
        root = self.make_tmp()
        data = b"y"
        write_bytes(os.path.join(root, "data.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"The hash of `data.txt` is {h}.\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["claim_shape"], "prose_association")
        self.assertEqual(claims[0]["associated_target"], "data.txt")

    def test_raw_text_matches_original_line(self):
        root = self.make_tmp()
        h = "a" * 64
        line = f"{h}  ghost.txt"
        write_text(os.path.join(root, "README.md"), line + "\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["raw_text"], line)

    def test_provenance_populated_with_real_git_repo(self):
        root = self.make_tmp()
        init_git_repo(root)
        data = b"z"
        write_bytes(os.path.join(root, "data.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  data.txt\n")
        git_commit_all(root, "add readme and data")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertIsNotNone(claims[0]["provenance"]["commit"])
        self.assertIsNone(claims[0]["provenance"]["note"])


# --------------------------------------------------------------------------
# build_testcount_claim
# --------------------------------------------------------------------------

class TestTestcountClaimRecompute(TempDirCase):
    def test_not_recomputed_by_default(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["status"], "NOT_RECOMPUTED")
        self.assertEqual(claims[0]["reason"], "test_execution_not_requested")

    def test_current_with_run_tests(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_x.py"), PASSING_MODULE)
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims[0]["status"], "CURRENT")
        self.assertEqual(claims[0]["recomputed_count"], 3)

    def test_stale_with_run_tests(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_x.py"), PASSING_MODULE)
        write_text(os.path.join(root, "README.md"), "Ran 99 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims[0]["status"], "STALE")
        self.assertEqual(claims[0]["reason"], "test_count_mismatch")
        self.assertEqual(claims[0]["recomputed_count"], 3)

    def test_missing_source_no_test_module(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims[0]["status"], "MISSING_SOURCE")
        self.assertEqual(claims[0]["reason"], "no_test_module_found")

    def test_malformed_ambiguous_test_module(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_a.py"), PASSING_MODULE)
        write_text(os.path.join(root, "test_b.py"), PASSING_MODULE)
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims[0]["status"], "MALFORMED")
        self.assertEqual(claims[0]["reason"], "ambiguous_test_module")

    def test_malformed_ambiguous_number_regardless_of_run_tests(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 1,234 tests in 0.01s\n")
        claims_norun = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        claims_run = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims_norun[0]["status"], "MALFORMED")
        self.assertEqual(claims_run[0]["status"], "MALFORMED")
        self.assertEqual(claims_norun[0]["reason"], "ambiguous_number_format")

    def test_claim_shape_recorded(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "**42 tests across 3 tools**\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims[0]["claim_shape"], "bold_across_tools")

    def test_associated_target_only_populated_when_run_tests_and_found(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims_norun = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertIsNone(claims_norun[0]["associated_target"])
        write_text(os.path.join(root, "test_x.py"), PASSING_MODULE)
        claims_run = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims_run[0]["associated_target"], "test_x.py")

    def test_no_claim_on_unrelated_line(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "nothing about tests here\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, False)
        self.assertEqual(claims, [])

    def test_unittest_execution_failed_with_run_tests(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_bad.py"), SYNTAX_ERROR_MODULE)
        write_text(os.path.join(root, "README.md"), "Ran 3 tests in 0.01s\n")
        claims = claimhist.scan_file(root, "README.md", os.path.join(root, "README.md"), {}, True)
        self.assertEqual(claims[0]["status"], "MALFORMED")
        self.assertEqual(claims[0]["reason"], "unittest_execution_failed")


# --------------------------------------------------------------------------
# build_report ordering, summary, exit codes
# --------------------------------------------------------------------------

class TestBuildReport(TempDirCase):
    def test_claims_sorted_by_source_file_and_line(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "a"))
        os.makedirs(os.path.join(root, "b"))
        write_text(os.path.join(root, "a", "README.md"), "Ran 1 test in 0.01s\n")
        write_text(os.path.join(root, "b", "README.md"), "Ran 2 tests in 0.01s\n")
        report, _ = claimhist.build_report(root, False)
        files = [c["source_file"] for c in report["claims"]]
        self.assertEqual(files, sorted(files))

    def test_tiebreak_breaks_genuine_tie(self):
        root = self.make_tmp()
        h1 = "c" * 64
        h2 = "d" * 64
        write_text(os.path.join(root, "README.md"), f"stray {h1} and stray {h2} alone\n")
        report, _ = claimhist.build_report(root, False)
        claims = [c for c in report["claims"] if c["claim_type"] == "SHA256_CLAIM"]
        self.assertEqual(len(claims), 2)
        # They tie on claim_type/source_file/line/associated_target/status;
        # the final order must equal ascending order of their own canonical dumps.
        dumps = [claimhist.canonical_dumps(c) for c in claims]
        self.assertEqual(dumps, sorted(dumps))
        self.assertNotEqual(claims[0]["claimed_hash"], claims[1]["claimed_hash"])

    def test_notes_sorted_and_deduped(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "a"))
        os.makedirs(os.path.join(root, "b"))
        h = "e" * 64
        write_text(os.path.join(root, "a", "README.md"), f"{h}  x.txt\n")
        write_text(os.path.join(root, "b", "README.md"), f"{h}  y.txt\n")
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["notes"], ["GIT_UNAVAILABLE"])

    def test_summary_counts(self):
        root = self.make_tmp()
        good_data = b"ok"
        write_bytes(os.path.join(root, "good.txt"), good_data)
        good_h = sha256_hex(good_data)
        bad_h = "f" * 64
        content = (
            f"{good_h}  good.txt\n"
            f"{bad_h}  ghost.txt\n"
            "Ran 3 tests in 0.01s\n"
        )
        write_text(os.path.join(root, "README.md"), content)
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["CURRENT"], 1)
        self.assertEqual(report["summary"]["MISSING_SOURCE"], 1)
        self.assertEqual(report["summary"]["NOT_RECOMPUTED"], 1)
        self.assertEqual(report["summary"]["total_claims"], 3)

    def test_exit_code_0_all_current_or_not_recomputed(self):
        root = self.make_tmp()
        data = b"ok"
        write_bytes(os.path.join(root, "good.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  good.txt\nRan 1 test in 0.01s\n")
        _, exit_code = claimhist.build_report(root, False)
        self.assertEqual(exit_code, 0)

    def test_exit_code_1_on_stale(self):
        root = self.make_tmp()
        write_bytes(os.path.join(root, "data.txt"), b"new")
        old_h = sha256_hex(b"old")
        write_text(os.path.join(root, "README.md"), f"{old_h}  data.txt\n")
        _, exit_code = claimhist.build_report(root, False)
        self.assertEqual(exit_code, 1)

    def test_exit_code_1_on_missing_source(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"{h}  ghost.txt\n")
        _, exit_code = claimhist.build_report(root, False)
        self.assertEqual(exit_code, 1)

    def test_exit_code_1_on_malformed(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"stray hash with no file {h}\n")
        _, exit_code = claimhist.build_report(root, False)
        self.assertEqual(exit_code, 1)


# --------------------------------------------------------------------------
# Malformed mid-batch never aborts the run
# --------------------------------------------------------------------------

class TestMalformedMidBatch(TempDirCase):
    def test_malformed_then_valid_claim_same_file(self):
        # A non-blank filler line (with no filename-like token of its
        # own) separates the two claims, so the nearest-non-blank-line
        # adjacency heuristic for the stray hash stops there and finds
        # nothing, instead of reaching all the way to "good.txt".
        root = self.make_tmp()
        data = b"content"
        write_bytes(os.path.join(root, "good.txt"), data)
        h_good = sha256_hex(data)
        h_bad = "a" * 64
        content = (
            f"stray {h_bad} with no file anywhere nearby\n"
            "unrelated filler prose with no dotted token\n"
            f"{h_good}  good.txt\n"
        )
        write_text(os.path.join(root, "README.md"), content)
        report, _ = claimhist.build_report(root, False)
        statuses = sorted(c["status"] for c in report["claims"])
        self.assertEqual(statuses, ["CURRENT", "MALFORMED"])

    def test_malformed_in_one_file_does_not_stop_second_file(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "broken"))
        os.makedirs(os.path.join(root, "fine"))
        h_bad = "a" * 64
        write_text(os.path.join(root, "broken", "README.md"), f"stray {h_bad}\n")
        write_text(os.path.join(root, "fine", "README.md"), "Ran 5 tests in 0.01s\n")
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["total_claims"], 2)
        self.assertEqual(report["summary"]["MALFORMED"], 1)
        self.assertEqual(report["summary"]["NOT_RECOMPUTED"], 1)

    def test_multiple_malformed_claims_all_captured(self):
        root = self.make_tmp()
        h1, h2, h3 = "a" * 64, "b" * 64, "c" * 64
        content = f"stray {h1}\n\nstray {h2}\n\nstray {h3}\n"
        write_text(os.path.join(root, "README.md"), content)
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["MALFORMED"], 3)

    def test_binary_garbage_in_one_file_does_not_crash_run(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "garbage"))
        os.makedirs(os.path.join(root, "clean"))
        write_bytes(
            os.path.join(root, "garbage", "README.md"),
            b"\x00\x01\xff\xfe binary junk \xff\xff\n",
        )
        write_text(os.path.join(root, "clean", "README.md"), "Ran 7 tests in 0.01s\n")
        report, exit_code = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["NOT_RECOMPUTED"], 1)
        self.assertIsInstance(exit_code, int)


# --------------------------------------------------------------------------
# Unicode and CRLF
# --------------------------------------------------------------------------

class TestUnicodeAndCrlf(TempDirCase):
    def test_unicode_filename_current(self):
        root = self.make_tmp()
        data = "café content\n".encode("utf-8")
        write_bytes(os.path.join(root, "café.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  café.txt\n")
        report, exit_code = claimhist.build_report(root, False)
        self.assertEqual(report["claims"][0]["status"], "CURRENT")
        self.assertEqual(exit_code, 0)

    def test_unicode_prose_parsed(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Résumé: Ran 12 tests in 0.03s ✓\n")
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["claims"][0]["claimed_count"], 12)

    def test_crlf_preserves_line_numbers(self):
        root = self.make_tmp()
        write_bytes(
            os.path.join(root, "README.md"),
            b"line one\r\nline two\r\nRan 8 tests in 0.02s\r\n",
        )
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["claims"][0]["line"], 3)

    def test_bare_cr_line_endings(self):
        root = self.make_tmp()
        write_bytes(os.path.join(root, "README.md"), b"one\rtwo\rRan 4 tests in 0.01s\r")
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["claims"][0]["line"], 3)

    def test_mixed_lf_and_crlf(self):
        root = self.make_tmp()
        write_bytes(
            os.path.join(root, "README.md"),
            b"one\ntwo\r\nRan 9 tests in 0.01s\n",
        )
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["claims"][0]["line"], 3)

    def test_invalid_utf8_does_not_crash_and_other_claims_found(self):
        root = self.make_tmp()
        write_bytes(
            os.path.join(root, "README.md"),
            b"\xff\xfe garbage\nRan 6 tests in 0.01s\n",
        )
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["total_claims"], 1)
        self.assertEqual(report["claims"][0]["claimed_count"], 6)


# --------------------------------------------------------------------------
# Empty root
# --------------------------------------------------------------------------

class TestEmptyRoot(TempDirCase):
    def test_no_target_files_at_all(self):
        root = self.make_tmp()
        report, exit_code = claimhist.build_report(root, False)
        self.assertEqual(report["claims"], [])
        self.assertEqual(exit_code, 0)

    def test_completely_empty_directory(self):
        root = self.make_tmp()
        self.assertEqual(os.listdir(root), [])
        report, exit_code = claimhist.build_report(root, False)
        self.assertEqual(report["summary"]["total_claims"], 0)
        self.assertEqual(exit_code, 0)

    def test_unrelated_files_ignored(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "notes.txt"), "Ran 3 tests in 0.01s\n")
        write_text(os.path.join(root, "OTHER.md"), "**5 tests across 1 tools**\n")
        report, _ = claimhist.build_report(root, False)
        self.assertEqual(report["claims"], [])


# --------------------------------------------------------------------------
# CLI subprocess: exit codes
# --------------------------------------------------------------------------

class TestCliExitCodes(TempDirCase):
    def test_exit_0(self):
        root = self.make_tmp()
        data = b"ok"
        write_bytes(os.path.join(root, "good.txt"), data)
        h = sha256_hex(data)
        write_text(os.path.join(root, "README.md"), f"{h}  good.txt\n")
        result = run_cli(["--root", root])
        self.assertEqual(result.returncode, 0)

    def test_exit_1_stale(self):
        root = self.make_tmp()
        write_bytes(os.path.join(root, "data.txt"), b"new")
        old_h = sha256_hex(b"old")
        write_text(os.path.join(root, "README.md"), f"{old_h}  data.txt\n")
        result = run_cli(["--root", root])
        self.assertEqual(result.returncode, 1)

    def test_exit_1_missing_source(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"{h}  ghost.txt\n")
        result = run_cli(["--root", root])
        self.assertEqual(result.returncode, 1)

    def test_exit_1_malformed(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"stray {h} with no file\n")
        result = run_cli(["--root", root])
        self.assertEqual(result.returncode, 1)

    def test_exit_2_bad_root_nonexistent(self):
        root = self.make_tmp()
        result = run_cli(["--root", os.path.join(root, "does_not_exist")])
        self.assertEqual(result.returncode, 2)

    def test_exit_2_root_is_a_file(self):
        root = self.make_tmp()
        f = os.path.join(root, "afile")
        write_text(f, "hi")
        result = run_cli(["--root", f])
        self.assertEqual(result.returncode, 2)

    def test_exit_2_unwritable_output(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "nothing\n")
        result = run_cli(["--root", root, "-o", os.path.join(root, "no", "such", "dir", "out.json")])
        self.assertEqual(result.returncode, 2)

    def test_exit_2_missing_required_root_arg(self):
        result = run_cli([])
        self.assertEqual(result.returncode, 2)

    def test_exit_2_unknown_argument(self):
        root = self.make_tmp()
        result = run_cli(["--root", root, "--not-a-real-flag"])
        self.assertEqual(result.returncode, 2)

    def test_run_tests_flag_changes_behavior_end_to_end(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "test_x.py"), PASSING_MODULE)
        write_text(os.path.join(root, "README.md"), "Ran 999 tests in 0.01s\n")
        without = run_cli(["--root", root])
        withrun = run_cli(["--root", root, "--run-tests"])
        self.assertEqual(without.returncode, 0)  # NOT_RECOMPUTED is fine
        self.assertEqual(withrun.returncode, 1)  # STALE: 999 != 3


# --------------------------------------------------------------------------
# CLI output file handling
# --------------------------------------------------------------------------

class TestCliOutputFile(TempDirCase):
    def test_output_file_matches_canonical_format(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 1 test in 0.01s\n")
        outpath = os.path.join(root, "out.json")
        result = run_cli(["--root", root, "-o", outpath])
        self.assertEqual(result.returncode, 0)
        with open(outpath, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertFalse(raw.endswith(b"\n\n"))
        parsed = json.loads(raw.decode("utf-8"))
        self.assertIn("claims", parsed)

    def test_output_file_encoding_and_newline(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "café Ran 2 tests in 0.01s\n")
        outpath = os.path.join(root, "out.json")
        run_cli(["--root", root, "-o", outpath])
        with open(outpath, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw)
        # ensure_ascii means no raw utf-8 multi-byte sequences should appear
        raw.decode("ascii")

    def test_stdout_and_file_output_identical_content(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 4 tests in 0.01s\n")
        outpath = os.path.join(root, "out.json")
        stdout_result = run_cli(["--root", root])
        run_cli(["--root", root, "-o", outpath])
        with open(outpath, "r", encoding="utf-8") as fh:
            file_content = fh.read()
        self.assertEqual(stdout_result.stdout, file_content)

    def test_no_file_created_without_output_flag(self):
        root = self.make_tmp()
        write_text(os.path.join(root, "README.md"), "Ran 4 tests in 0.01s\n")
        before = set(os.listdir(root))
        run_cli(["--root", root])
        after = set(os.listdir(root))
        self.assertEqual(before, after)


# --------------------------------------------------------------------------
# Determinism and relocation
# --------------------------------------------------------------------------

class TestDeterminismAndRelocation(TempDirCase):
    def _make_fixture(self, root):
        data = b"payload data"
        write_bytes(os.path.join(root, "artifact.bin"), data)
        h = sha256_hex(data)
        write_text(
            os.path.join(root, "README.md"),
            f"{h}  artifact.bin\n\nRan 11 tests in 0.02s\n",
        )

    def test_two_runs_same_directory_identical_bytes(self):
        root = self.make_tmp()
        self._make_fixture(root)
        r1 = run_cli(["--root", root])
        r2 = run_cli(["--root", root])
        self.assertEqual(r1.stdout, r2.stdout)
        self.assertEqual(r1.returncode, r2.returncode)

    def test_relocated_tree_identical_bytes(self):
        parent = self.make_tmp()
        loc_a = os.path.join(parent, "loc_aaa", "fx")
        loc_b = os.path.join(parent, "loc_zzzzzzzz", "fx")
        os.makedirs(loc_a)
        os.makedirs(loc_b)
        self._make_fixture(loc_a)
        self._make_fixture(loc_b)
        ra = run_cli(["--root", loc_a])
        rb = run_cli(["--root", loc_b])
        self.assertEqual(ra.stdout, rb.stdout)

    def test_no_absolute_path_substring_in_report(self):
        root = self.make_tmp()
        self._make_fixture(root)
        result = run_cli(["--root", root])
        self.assertNotIn(root, result.stdout)

    def test_build_report_function_identical_across_two_absolute_roots(self):
        parent = self.make_tmp()
        loc_1 = os.path.join(parent, "first_location", "fx")
        loc_2 = os.path.join(parent, "second_location_longer_name", "fx")
        os.makedirs(loc_1)
        os.makedirs(loc_2)
        self._make_fixture(loc_1)
        self._make_fixture(loc_2)
        report1, _ = claimhist.build_report(loc_1, False)
        report2, _ = claimhist.build_report(loc_2, False)
        self.assertEqual(claimhist.canonical_dumps(report1), claimhist.canonical_dumps(report2))


# --------------------------------------------------------------------------
# Regression tests for the found bug (hash glued to an extension with no
# separator being mis-associated with a filename literally containing the
# hash itself).
# --------------------------------------------------------------------------

class TestBugRegressionHashAdjacentDot(TempDirCase):
    def test_hash_immediately_followed_by_extension_not_mistaken_for_filename(self):
        h = "a" * 64
        line = f"See {h}.report for details."
        candidates = claimhist.find_filename_candidates(line)
        self.assertEqual(candidates, [])

    def test_end_to_end_reports_no_filename_association_not_bogus_missing_source(self):
        root = self.make_tmp()
        h = "a" * 64
        write_text(os.path.join(root, "README.md"), f"See {h}.report for details.\n")
        report, _ = claimhist.build_report(root, False)
        claim = report["claims"][0]
        self.assertEqual(claim["status"], "MALFORMED")
        self.assertEqual(claim["reason"], "no_filename_association")
        self.assertIsNone(claim["associated_target"])

    def test_hash_inside_backticks_glued_to_extension_also_fixed(self):
        h = "b" * 64
        line = f"See `{h}.report` for details."
        candidates = claimhist.find_filename_candidates(line)
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()

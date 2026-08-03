"""Unit tests for claimcheck.py.

Organised into test classes by layer: pure extraction (regex) tests first
(fast, no subprocess), then verification-logic tests against small temp
bundles, then full build_report integration tests, then CLI/subprocess
level tests, then the canonical-JSON / determinism / no-absolute-path
contract tests that back the claims made in README.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claimcheck  # noqa: E402

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_PATH = os.path.join(THIS_DIR, "claimcheck.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(root: Path, relpath: str, content) -> Path:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    else:
        p.write_bytes(content)
    return p


class TempBundleMixin:
    def make_bundle(self, files: dict) -> Path:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        for rel, content in files.items():
            write(root, rel, content)
        return root

    def write_notes(self, root: Path, name: str, text: str) -> Path:
        return write(root, name, text)


# ==========================================================================
# SHA256_CLAIM extraction
# ==========================================================================

class TestExtractSha256Occurrences(unittest.TestCase):
    def occ(self, line):
        return claimcheck._extract_sha256_occurrences(1, line)

    def test_no_hash_no_claims(self):
        self.assertEqual(self.occ("nothing to see here"), [])

    def test_63_hex_chars_not_a_claim(self):
        self.assertEqual(self.occ("a" * 63), [])

    def test_65_hex_chars_not_a_claim(self):
        self.assertEqual(self.occ("a" * 65), [])

    def test_64_hex_bounded_by_hex_is_not_a_claim(self):
        # a 64-hex run immediately adjacent to more hex digits on either
        # side is not a standalone 64-char token.
        self.assertEqual(self.occ("f" + "a" * 64), [])
        self.assertEqual(self.occ("a" * 64 + "f"), [])

    def test_bare_64_hex_no_filename(self):
        h = "a" * 64
        occs = self.occ("digest: %s" % h)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_hash"], h)
        self.assertIsNone(occs[0].params["filename"])

    def test_sha256_paren_equals_form(self):
        h = sha(b"x")
        occs = self.occ("sha256(report.txt) = %s" % h)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["filename"], "report.txt")
        self.assertEqual(occs[0].params["asserted_hash"], h)

    def test_sha256_paren_colon_form(self):
        h = sha(b"x")
        occs = self.occ("sha256(report.txt): %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha256_paren_no_separator_form(self):
        h = sha(b"x")
        occs = self.occ("sha256(report.txt) %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha256_paren_case_insensitive_tag(self):
        h = sha(b"x")
        occs = self.occ("SHA256(report.txt) = %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha_dash_256_paren_form(self):
        h = sha(b"x")
        occs = self.occ("sha-256(report.txt) = %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_filename_colon_form(self):
        h = sha(b"x")
        occs = self.occ("report.txt: %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_filename_equals_form(self):
        h = sha(b"x")
        occs = self.occ("report.txt = %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_filename_sha256_colon_form(self):
        h = sha(b"x")
        occs = self.occ("report.txt sha256: %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_filename_sha256_equals_form(self):
        h = sha(b"x")
        occs = self.occ("report.txt sha256 = %s" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha256sum_style_two_spaces(self):
        h = sha(b"x")
        occs = self.occ("%s  report.txt" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha256sum_style_star_binary_marker(self):
        h = sha(b"x")
        occs = self.occ("%s *report.txt" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_sha256sum_style_one_space(self):
        h = sha(b"x")
        occs = self.occ("%s report.txt" % h)
        self.assertEqual(occs[0].params["filename"], "report.txt")

    def test_filename_with_path_separator(self):
        h = sha(b"x")
        occs = self.occ("sha256(src/report.txt) = %s" % h)
        self.assertEqual(occs[0].params["filename"], "src/report.txt")

    def test_uppercase_hex_extracted_verbatim(self):
        h = sha(b"x").upper()
        occs = self.occ("sha256(report.txt) = %s" % h)
        self.assertEqual(occs[0].params["asserted_hash"], h)

    def test_mixed_case_hex_extracted(self):
        h_lower = sha(b"x")
        h_mixed = h_lower[:32].upper() + h_lower[32:]
        occs = self.occ("sha256(report.txt) = %s" % h_mixed)
        self.assertEqual(occs[0].params["asserted_hash"], h_mixed)

    def test_two_claims_one_line_both_have_filenames(self):
        h1, h2 = sha(b"1"), sha(b"2")
        line = "a.py: %s  b.py: %s" % (h1, h2)
        occs = self.occ(line)
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0].params["asserted_hash"], h1)
        self.assertEqual(occs[0].params["filename"], "a.py")
        self.assertEqual(occs[1].params["asserted_hash"], h2)
        self.assertEqual(occs[1].params["filename"], "b.py")

    def test_two_claims_one_line_sha256sum_style(self):
        h1, h2 = sha(b"1"), sha(b"2")
        line = "%s  a.py    %s  b.py" % (h1, h2)
        occs = self.occ(line)
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0].params["filename"], "a.py")
        self.assertEqual(occs[1].params["filename"], "b.py")

    def test_two_bare_claims_one_line(self):
        h1, h2 = sha(b"1"), sha(b"2")
        line = "hashes seen: %s and %s" % (h1, h2)
        occs = self.occ(line)
        self.assertEqual(len(occs), 2)
        self.assertIsNone(occs[0].params["filename"])
        self.assertIsNone(occs[1].params["filename"])

    def test_offsets_are_increasing_left_to_right(self):
        h1, h2 = sha(b"1"), sha(b"2")
        line = "%s then %s" % (h1, h2)
        occs = self.occ(line)
        self.assertLess(occs[0].offset, occs[1].offset)

    def test_filename_token_stops_at_backtick(self):
        h = sha(b"x")
        occs = self.occ("`report.txt`: %s" % h)
        # backtick is excluded from the filename token character class
        self.assertNotIn("`", occs[0].params["filename"] or "")

    def test_no_filename_when_preceding_token_has_no_extension(self):
        h = sha(b"x")
        occs = self.occ("report: %s" % h)
        self.assertIsNone(occs[0].params["filename"])

    def test_claim_text_is_whole_line(self):
        h = sha(b"x")
        line = "  leading space sha256(report.txt) = %s trailing" % h
        occs = self.occ(line)
        self.assertEqual(occs[0].line_text, line)

    def test_line_number_recorded(self):
        h = sha(b"x")
        occs = claimcheck._extract_sha256_occurrences(42, "sha256(x.py) = %s" % h)
        self.assertEqual(occs[0].line_no, 42)

    def test_version_number_not_misread_as_filename(self):
        # Real bug found while building this tool: "Version 2.5.1 sha256:
        # <hash>" used to extract "2.5.1" as the claimed filename, because
        # a bare digit run after a dot satisfied the old filename-token
        # pattern. A version number is not a filename; this must fall
        # back to a bare (no-filename) claim instead.
        h = sha(b"x")
        occs = self.occ("Version 2.5.1 sha256: %s" % h)
        self.assertEqual(len(occs), 1)
        self.assertIsNone(occs[0].params["filename"])

    def test_decimal_number_not_misread_as_filename(self):
        h = sha(b"x")
        occs = self.occ("measured 3.14 sha256: %s" % h)
        self.assertIsNone(occs[0].params["filename"])

    def test_numeric_extension_real_filename_still_not_matched_as_such(self):
        # A file literally named "backup.001" is a documented limitation
        # (see README "Limitations"), not a contradiction of the fix
        # above: the tool cannot tell a numeric-suffixed real filename
        # from a version/measurement number, and chooses to treat both
        # as "not a filename" rather than risk the version-number
        # false positive, which is far more common in verifier notes.
        h = sha(b"x")
        occs = self.occ("sha256(backup.001) = %s" % h)
        self.assertIsNone(occs[0].params["filename"])

    def test_extension_starting_with_letter_still_matches(self):
        h = sha(b"x")
        occs = self.occ("sha256(archive.v2) = %s" % h)
        self.assertEqual(occs[0].params["filename"], "archive.v2")


# ==========================================================================
# TEST_COUNT_CLAIM extraction
# ==========================================================================

class TestExtractTestCountOccurrences(unittest.TestCase):
    def occ(self, line):
        return claimcheck._extract_test_count_occurrences(1, line)

    def test_no_number_no_claim(self):
        self.assertEqual(self.occ("all tests passed"), [])

    def test_ran_n_tests_plural(self):
        occs = self.occ("Ran 42 tests in 0.01s")
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_count"], 42)

    def test_ran_1_test_singular(self):
        occs = self.occ("Ran 1 test in 0.01s")
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_count"], 1)

    def test_ran_case_insensitive(self):
        occs = self.occ("ran 7 TESTS ok")
        self.assertEqual(occs[0].params["asserted_count"], 7)

    def test_ran_n_tests_does_not_double_count_as_bare(self):
        occs = self.occ("Ran 5 tests in 0.00s")
        self.assertEqual(len(occs), 1)

    def test_bare_n_tests_no_ran_prefix(self):
        occs = self.occ("the suite has 150 tests")
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_count"], 150)

    def test_bare_1_test_singular(self):
        occs = self.occ("there is 1 test in this module")
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_count"], 1)

    def test_two_bare_counts_one_line(self):
        occs = self.occ("module a has 3 tests, module b has 4 tests")
        self.assertEqual(len(occs), 2)
        self.assertEqual([o.params["asserted_count"] for o in occs], [3, 4])

    def test_ran_and_bare_together_distinct(self):
        occs = self.occ("Ran 3 tests earlier; the suite now has 9 tests")
        counts = sorted(o.params["asserted_count"] for o in occs)
        self.assertEqual(counts, [3, 9])

    def test_zero_tests(self):
        occs = self.occ("Ran 0 tests in 0.000s")
        self.assertEqual(occs[0].params["asserted_count"], 0)

    def test_word_testsuite_is_not_a_claim(self):
        # "tests" must be its own word; "testsuite" should not match \btests\b
        self.assertEqual(self.occ("5 testsuite entries"), [])

    def test_number_not_adjacent_to_tests_word_is_not_a_claim(self):
        self.assertEqual(self.occ("we have 5 things and some tests"), [])

    def test_line_number_recorded(self):
        occs = claimcheck._extract_test_count_occurrences(99, "Ran 2 tests")
        self.assertEqual(occs[0].line_no, 99)

    def test_claim_text_is_whole_line(self):
        line = "prefix Ran 2 tests suffix"
        occs = self.occ(line)
        self.assertEqual(occs[0].line_text, line)


# ==========================================================================
# EXIT_CODE_CLAIM extraction
# ==========================================================================

class TestExtractExitCodeOccurrences(unittest.TestCase):
    def occ(self, line):
        return claimcheck._extract_exit_code_occurrences(1, line)

    def test_no_exit_mention_no_claim(self):
        self.assertEqual(self.occ("`python3 x.py` ran fine"), [])

    def test_exit_equals_form(self):
        occs = self.occ("`python3 x.py` exit=0")
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].params["asserted_exit"], 0)
        self.assertEqual(occs[0].params["command_text"], "python3 x.py")

    def test_exit_code_space_form(self):
        occs = self.occ("`python3 x.py` exit code 0")
        self.assertEqual(occs[0].params["asserted_exit"], 0)

    def test_exit_code_colon_form(self):
        occs = self.occ("`python3 x.py` exit code: 0")
        self.assertEqual(occs[0].params["asserted_exit"], 0)

    def test_exit_status_word_not_required(self):
        occs = self.occ("`python3 x.py` exit 3")
        self.assertEqual(occs[0].params["asserted_exit"], 3)

    def test_negative_exit_code(self):
        occs = self.occ("`python3 x.py` exit=-1")
        self.assertEqual(occs[0].params["asserted_exit"], -1)

    def test_exit_code_underscore_form(self):
        occs = self.occ("`python3 x.py` exit_code=137")
        self.assertEqual(occs[0].params["asserted_exit"], 137)

    def test_no_command_present(self):
        occs = self.occ("we observed exit=0 with no command quoted")
        self.assertEqual(len(occs), 1)
        self.assertIsNone(occs[0].params["command_text"])

    def test_nearest_preceding_command_chosen(self):
        line = "`python3 a.py` did something, then `python3 b.py` exit=0"
        occs = self.occ(line)
        self.assertEqual(occs[0].params["command_text"], "python3 b.py")

    def test_command_after_exit_mention_not_chosen(self):
        line = "exit=0 was seen before `python3 a.py` ran"
        occs = self.occ(line)
        self.assertIsNone(occs[0].params["command_text"])

    def test_two_exit_claims_one_line(self):
        line = "`python3 a.py` exit=0 and `python3 b.py` exit=1"
        occs = self.occ(line)
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0].params["command_text"], "python3 a.py")
        self.assertEqual(occs[0].params["asserted_exit"], 0)
        self.assertEqual(occs[1].params["command_text"], "python3 b.py")
        self.assertEqual(occs[1].params["asserted_exit"], 1)

    def test_command_with_arguments_captured_whole(self):
        occs = self.occ("`python3 a.py --flag value` exit=0")
        self.assertEqual(occs[0].params["command_text"], "python3 a.py --flag value")

    def test_line_number_recorded(self):
        occs = claimcheck._extract_exit_code_occurrences(7, "`python3 a.py` exit=0")
        self.assertEqual(occs[0].line_no, 7)

    def test_claim_text_is_whole_line(self):
        line = "note: `python3 a.py` exit=0 (see log)"
        occs = self.occ(line)
        self.assertEqual(occs[0].line_text, line)


# ==========================================================================
# extract_claim_occurrences (multi-line, ordering, empty/no-claim notes)
# ==========================================================================

class TestExtractClaimOccurrences(unittest.TestCase):
    def test_empty_notes_text_no_claims(self):
        self.assertEqual(claimcheck.extract_claim_occurrences(""), [])

    def test_whitespace_only_notes_no_claims(self):
        self.assertEqual(claimcheck.extract_claim_occurrences("   \n\n  \n"), [])

    def test_notes_with_no_claims_at_all(self):
        text = "This bundle looks fine.\nNo hashes, no counts, no commands here.\n"
        self.assertEqual(claimcheck.extract_claim_occurrences(text), [])

    def test_single_claim_line_number_is_one_indexed(self):
        h = sha(b"x")
        occs = claimcheck.extract_claim_occurrences("sha256(x.py) = %s\n" % h)
        self.assertEqual(occs[0].line_no, 1)

    def test_claim_on_second_line(self):
        h = sha(b"x")
        text = "first line has nothing\nsha256(x.py) = %s\n" % h
        occs = claimcheck.extract_claim_occurrences(text)
        self.assertEqual(occs[0].line_no, 2)

    def test_no_trailing_newline_still_scans_last_line(self):
        h = sha(b"x")
        text = "sha256(x.py) = %s" % h  # no trailing \n
        occs = claimcheck.extract_claim_occurrences(text)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].line_no, 1)

    def test_sorted_by_line_then_offset_then_type(self):
        h = sha(b"x")
        text = "\n".join([
            "`python3 x.py` exit=0",   # line 1: exit claim
            "sha256(x.py) = %s" % h,   # line 2: sha claim
            "Ran 3 tests",             # line 3: count claim
        ])
        occs = claimcheck.extract_claim_occurrences(text)
        self.assertEqual([o.line_no for o in occs], [1, 2, 3])
        self.assertEqual([o.claim_type for o in occs],
                          [claimcheck.CLAIM_EXIT_CODE, claimcheck.CLAIM_SHA256, claimcheck.CLAIM_TEST_COUNT])

    def test_mixed_claim_types_same_line_sorted_by_offset(self):
        h = sha(b"x")
        line = "Ran 3 tests, sha256(x.py) = %s, `python3 x.py` exit=0" % h
        occs = claimcheck.extract_claim_occurrences(line)
        self.assertEqual(len(occs), 3)
        offsets = [o.offset for o in occs]
        self.assertEqual(offsets, sorted(offsets))

    def test_all_occurrences_same_line_number_when_all_on_one_line(self):
        h = sha(b"x")
        line = "Ran 3 tests, sha256(x.py) = %s, `python3 x.py` exit=0" % h
        occs = claimcheck.extract_claim_occurrences(line)
        self.assertTrue(all(o.line_no == 1 for o in occs))


# ==========================================================================
# resolve_filename
# ==========================================================================

class TestResolveFilename(unittest.TestCase):
    def test_exact_relpath_match(self):
        resolved, candidates = claimcheck.resolve_filename("src/a.py", ["src/a.py", "b.py"])
        self.assertEqual(resolved, "src/a.py")
        self.assertEqual(candidates, [])

    def test_basename_unique_match(self):
        resolved, candidates = claimcheck.resolve_filename("a.py", ["src/a.py", "b.py"])
        self.assertEqual(resolved, "src/a.py")

    def test_basename_ambiguous_match(self):
        resolved, candidates = claimcheck.resolve_filename("a.py", ["src/a.py", "lib/a.py"])
        self.assertIsNone(resolved)
        self.assertEqual(candidates, ["lib/a.py", "src/a.py"])

    def test_not_found(self):
        resolved, candidates = claimcheck.resolve_filename("missing.py", ["a.py"])
        self.assertIsNone(resolved)
        self.assertEqual(candidates, [])

    def test_leading_dot_slash_normalised(self):
        resolved, candidates = claimcheck.resolve_filename("./a.py", ["a.py"])
        self.assertEqual(resolved, "a.py")

    def test_backslash_normalised_to_forward_slash(self):
        resolved, candidates = claimcheck.resolve_filename("src\\a.py", ["src/a.py"])
        self.assertEqual(resolved, "src/a.py")


# ==========================================================================
# vet_command (safety gate)
# ==========================================================================

class TestVetCommand(TempBundleMixin, unittest.TestCase):
    def setUp(self):
        self.bundle = self.make_bundle({"ok.py": "print('hi')\n", "sub/nested.py": "print('n')\n"})
        self.relpaths = claimcheck.discover_files(self.bundle)

    def test_valid_python3_invocation_allowed(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py", self.bundle, self.relpaths)
        self.assertTrue(allowed)
        self.assertEqual(argv, ["python3", "ok.py"])

    def test_valid_with_arguments_allowed(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py --flag 1", self.bundle, self.relpaths)
        self.assertTrue(allowed)
        self.assertEqual(argv, ["python3", "ok.py", "--flag", "1"])

    def test_nested_file_allowed(self):
        allowed, reason, argv = claimcheck.vet_command("python3 sub/nested.py", self.bundle, self.relpaths)
        self.assertTrue(allowed)

    def test_non_python3_interpreter_refused(self):
        allowed, reason, argv = claimcheck.vet_command("bash ok.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("python3", reason)
        self.assertIsNone(argv)

    def test_python_without_3_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python ok.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_python3_dot_version_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3.10 ok.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_absolute_interpreter_path_refused(self):
        allowed, reason, argv = claimcheck.vet_command("/usr/bin/python3 ok.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_no_target_file_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_target_not_in_bundle_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 missing.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("not found", reason)

    def test_absolute_target_path_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 /etc/hostname", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("absolute", reason)

    def test_parent_traversal_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ../ok.py", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("outside", reason)

    def test_semicolon_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py; rm -rf /", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("metacharacter", reason)

    def test_pipe_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py | cat", self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("metacharacter", reason)

    def test_ampersand_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py &", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_dollar_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py $(whoami)", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_redirect_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py > out.txt", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_redirect_in_refused(self):
        allowed, reason, argv = claimcheck.vet_command("python3 ok.py < in.txt", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_unbalanced_quotes_refused(self):
        allowed, reason, argv = claimcheck.vet_command('python3 "ok.py', self.bundle, self.relpaths)
        self.assertFalse(allowed)
        self.assertIn("parsed", reason)

    def test_empty_command_refused(self):
        allowed, reason, argv = claimcheck.vet_command("   ", self.bundle, self.relpaths)
        self.assertFalse(allowed)

    def test_quoted_target_with_spaces_allowed_if_exists(self):
        write(self.bundle, "has space.py", "print(1)\n")
        relpaths = claimcheck.discover_files(self.bundle)
        allowed, reason, argv = claimcheck.vet_command('python3 "has space.py"', self.bundle, relpaths)
        self.assertTrue(allowed)
        self.assertEqual(argv, ["python3", "has space.py"])


# ==========================================================================
# run_command
# ==========================================================================

class TestRunCommand(TempBundleMixin, unittest.TestCase):
    def test_successful_run_exit_zero(self):
        bundle = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        rc, detail = claimcheck.run_command(["python3", "ok.py"], bundle)
        self.assertEqual(rc, 0)

    def test_nonzero_exit_captured(self):
        bundle = self.make_bundle({"bad.py": "import sys\nsys.exit(7)\n"})
        rc, detail = claimcheck.run_command(["python3", "bad.py"], bundle)
        self.assertEqual(rc, 7)

    def test_negative_style_large_exit_code_wraps_like_os(self):
        bundle = self.make_bundle({"e.py": "import sys\nsys.exit(250)\n"})
        rc, detail = claimcheck.run_command(["python3", "e.py"], bundle)
        self.assertEqual(rc, 250)

    def test_timeout_reports_none_and_detail(self):
        bundle = self.make_bundle({"slow.py": "import time\ntime.sleep(5)\n"})
        old = claimcheck.COMMAND_TIMEOUT_SECONDS
        claimcheck.COMMAND_TIMEOUT_SECONDS = 1
        try:
            rc, detail = claimcheck.run_command(["python3", "slow.py"], bundle)
        finally:
            claimcheck.COMMAND_TIMEOUT_SECONDS = old
        self.assertIsNone(rc)
        self.assertIn("timeout", detail)

    def test_nonexistent_interpreter_reports_none(self):
        bundle = self.make_bundle({"ok.py": "print(1)\n"})
        rc, detail = claimcheck.run_command(["python3_does_not_exist_xyz", "ok.py"], bundle)
        self.assertIsNone(rc)
        self.assertIn("could not be started", detail)

    def test_command_output_does_not_affect_returncode(self):
        bundle = self.make_bundle({"noisy.py": "print('a' * 10000)\nimport sys\nsys.exit(2)\n"})
        rc, detail = claimcheck.run_command(["python3", "noisy.py"], bundle)
        self.assertEqual(rc, 2)


# ==========================================================================
# verify_sha256_claim
# ==========================================================================

class TestVerifySha256Claim(TempBundleMixin, unittest.TestCase):
    def occ_for(self, asserted_hash, filename, line="line"):
        return claimcheck.ClaimOccurrence(claimcheck.CLAIM_SHA256, 1, 0, line,
                                           {"asserted_hash": asserted_hash, "filename": filename})

    def setUp(self):
        self.bundle = self.make_bundle({"a.py": "AAA", "b.py": "BBB"})
        self.relpaths = claimcheck.discover_files(self.bundle)
        self.file_hashes = claimcheck.hash_bundle_files(self.bundle, self.relpaths)
        self.hash_a = sha(b"AAA")
        self.hash_b = sha(b"BBB")

    def test_filename_matched(self):
        occ = self.occ_for(self.hash_a, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(result["evidence_source"], "hashed bundle file 'a.py': sha256 matches the claim")
        self.assertEqual(result["observed_value"]["sha256"], self.hash_a)

    def test_filename_matched_case_insensitive(self):
        occ = self.occ_for(self.hash_a.upper(), "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)

    def test_filename_mismatched(self):
        occ = self.occ_for(self.hash_b, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MISMATCHED)

    def test_filename_mismatch_reports_where_hash_actually_lives(self):
        occ = self.occ_for(self.hash_b, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["observed_value"]["hash_claimed_found_at"], ["b.py"])
        self.assertIn("b.py", result["evidence_source"])

    def test_filename_mismatch_with_no_elsewhere_match(self):
        fake = sha(b"not-in-bundle-at-all")
        occ = self.occ_for(fake, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MISMATCHED)
        self.assertEqual(result["observed_value"]["hash_claimed_found_at"], [])

    def test_filename_missing_is_unsubstantiated(self):
        occ = self.occ_for(self.hash_a, "missing.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_UNSUBSTANTIATED)
        self.assertIsNone(result["observed_value"])
        self.assertIn("nothing in the bundle could substantiate this", result["evidence_source"])

    def test_ambiguous_filename_is_unsubstantiated(self):
        bundle = self.make_bundle({"src/a.py": "AAA", "lib/a.py": "AAA"})
        relpaths = claimcheck.discover_files(bundle)
        file_hashes = claimcheck.hash_bundle_files(bundle, relpaths)
        occ = self.occ_for(self.hash_a, "a.py")
        result = claimcheck.verify_sha256_claim(occ, bundle, relpaths, file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_UNSUBSTANTIATED)
        self.assertIn("ambiguous", result["evidence_source"])

    def test_bare_hash_matched(self):
        occ = self.occ_for(self.hash_a, None)
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(result["observed_value"]["matched_files"], ["a.py"])

    def test_bare_hash_mismatched_no_file_found(self):
        fake = sha(b"nothing-matches-this")
        occ = self.occ_for(fake, None)
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MISMATCHED)
        self.assertEqual(result["observed_value"]["matched_files"], [])

    def test_bare_hash_case_insensitive_match(self):
        occ = self.occ_for(self.hash_a.upper(), None)
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)

    def test_bare_hash_matches_multiple_files(self):
        bundle = self.make_bundle({"x.py": "SAME", "y.py": "SAME"})
        relpaths = claimcheck.discover_files(bundle)
        file_hashes = claimcheck.hash_bundle_files(bundle, relpaths)
        occ = self.occ_for(sha(b"SAME"), None)
        result = claimcheck.verify_sha256_claim(occ, bundle, relpaths, file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(result["observed_value"]["matched_files"], ["x.py", "y.py"])

    def test_unreadable_file_is_unsubstantiated(self):
        occ = self.occ_for(self.hash_a, "a.py")
        file_hashes = dict(self.file_hashes)
        file_hashes["a.py"] = None
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, file_hashes)
        self.assertEqual(result["result"], claimcheck.RESULT_UNSUBSTANTIATED)

    def test_result_always_has_all_seven_fields(self):
        occ = self.occ_for(self.hash_a, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        expected_keys = {"claim_type", "claim_text", "notes_line_number",
                          "asserted_value", "observed_value", "result", "evidence_source"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_claim_type_field(self):
        occ = self.occ_for(self.hash_a, "a.py")
        result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
        self.assertEqual(result["claim_type"], claimcheck.CLAIM_SHA256)

    def test_evidence_source_is_nonempty_string_for_every_result(self):
        cases = [
            self.occ_for(self.hash_a, "a.py"),        # MATCHED
            self.occ_for(self.hash_b, "a.py"),        # MISMATCHED
            self.occ_for(self.hash_a, "missing.py"),  # UNSUBSTANTIATED
        ]
        for occ in cases:
            result = claimcheck.verify_sha256_claim(occ, self.bundle, self.relpaths, self.file_hashes)
            self.assertIsInstance(result["evidence_source"], str)
            self.assertGreater(len(result["evidence_source"]), 0)


# ==========================================================================
# verify_test_count_claim
# ==========================================================================

class TestVerifyTestCountClaim(unittest.TestCase):
    def occ_for(self, asserted_count):
        return claimcheck.ClaimOccurrence(claimcheck.CLAIM_TEST_COUNT, 1, 0, "line",
                                           {"asserted_count": asserted_count})

    def test_matched(self):
        run = {"observed_count": 5, "detail": "observed summary line 'Ran 5 tests'"}
        result = claimcheck.verify_test_count_claim(self.occ_for(5), run)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(result["observed_value"]["tests"], 5)

    def test_mismatched(self):
        run = {"observed_count": 2, "detail": "observed summary line 'Ran 2 tests'"}
        result = claimcheck.verify_test_count_claim(self.occ_for(10), run)
        self.assertEqual(result["result"], claimcheck.RESULT_MISMATCHED)
        self.assertEqual(result["observed_value"]["tests"], 2)

    def test_unsubstantiated_when_run_failed(self):
        run = {"observed_count": None, "detail": "execution failed (FileNotFoundError: ...)"}
        result = claimcheck.verify_test_count_claim(self.occ_for(5), run)
        self.assertEqual(result["result"], claimcheck.RESULT_UNSUBSTANTIATED)
        self.assertIsNone(result["observed_value"])

    def test_evidence_source_mentions_real_command(self):
        run = {"observed_count": 5, "detail": "observed summary line 'Ran 5 tests'"}
        result = claimcheck.verify_test_count_claim(self.occ_for(5), run)
        self.assertIn("python3 -m unittest discover", result["evidence_source"])

    def test_zero_matches_zero(self):
        run = {"observed_count": 0, "detail": "no 'Ran N tests' summary line was produced (0 tests discovered)"}
        result = claimcheck.verify_test_count_claim(self.occ_for(0), run)
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)


# ==========================================================================
# verify_exit_code_claim
# ==========================================================================

class TestVerifyExitCodeClaim(TempBundleMixin, unittest.TestCase):
    def occ_for(self, command_text, asserted_exit):
        return claimcheck.ClaimOccurrence(claimcheck.CLAIM_EXIT_CODE, 1, 0, "line",
                                           {"command_text": command_text, "asserted_exit": asserted_exit})

    def test_matched(self):
        bundle = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(self.occ_for("python3 ok.py", 0), bundle, relpaths, {})
        self.assertEqual(result["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(result["observed_value"]["exit_code"], 0)

    def test_mismatched(self):
        bundle = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(self.occ_for("python3 ok.py", 1), bundle, relpaths, {})
        self.assertEqual(result["result"], claimcheck.RESULT_MISMATCHED)
        self.assertEqual(result["observed_value"]["exit_code"], 0)

    def test_no_command_text_is_unverifiable(self):
        bundle = self.make_bundle({"ok.py": "print(1)\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(self.occ_for(None, 0), bundle, relpaths, {})
        self.assertEqual(result["result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertIsNone(result["observed_value"])

    def test_refused_command_is_unverifiable(self):
        bundle = self.make_bundle({"ok.py": "print(1)\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(self.occ_for("bash ok.py", 0), bundle, relpaths, {})
        self.assertEqual(result["result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)

    def test_shell_metacharacter_never_executed(self):
        bundle = self.make_bundle({"ok.py": "import pathlib\npathlib.Path('marker').write_text('x')\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(
            self.occ_for("python3 ok.py; python3 ok.py", 0), bundle, relpaths, {})
        self.assertEqual(result["result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertFalse((bundle / "marker").exists())

    def test_command_cache_reused_for_identical_command(self):
        bundle = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        relpaths = claimcheck.discover_files(bundle)
        cache = {}
        claimcheck.verify_exit_code_claim(self.occ_for("python3 ok.py", 0), bundle, relpaths, cache)
        self.assertIn("python3 ok.py", cache)
        # second call must not raise and must reuse the cached result
        result2 = claimcheck.verify_exit_code_claim(self.occ_for("python3 ok.py", 5), bundle, relpaths, cache)
        self.assertEqual(result2["observed_value"]["exit_code"], 0)
        self.assertEqual(result2["result"], claimcheck.RESULT_MISMATCHED)

    def test_timeout_is_unverifiable(self):
        bundle = self.make_bundle({"slow.py": "import time\ntime.sleep(5)\n"})
        relpaths = claimcheck.discover_files(bundle)
        old = claimcheck.COMMAND_TIMEOUT_SECONDS
        claimcheck.COMMAND_TIMEOUT_SECONDS = 1
        try:
            result = claimcheck.verify_exit_code_claim(self.occ_for("python3 slow.py", 0), bundle, relpaths, {})
        finally:
            claimcheck.COMMAND_TIMEOUT_SECONDS = old
        self.assertEqual(result["result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertIn("timeout", result["evidence_source"])

    def test_asserted_value_carries_command_and_exit_code(self):
        bundle = self.make_bundle({"ok.py": "import sys\nsys.exit(3)\n"})
        relpaths = claimcheck.discover_files(bundle)
        result = claimcheck.verify_exit_code_claim(self.occ_for("python3 ok.py", 3), bundle, relpaths, {})
        self.assertEqual(result["asserted_value"], {"command": "python3 ok.py", "exit_code": 3})


# ==========================================================================
# build_report
# ==========================================================================

class TestBuildReport(TempBundleMixin, unittest.TestCase):
    def test_input_error_bundle_dir_missing(self):
        notes = self.make_bundle({"notes.txt": "no claims\n"}) / "notes.txt"
        with self.assertRaises(claimcheck.InputError):
            claimcheck.build_report(Path("/no/such/dir/xyz"), notes, "/no/such/dir/xyz", str(notes))

    def test_input_error_bundle_dir_is_a_file(self):
        root = self.make_bundle({"notes.txt": "x\n", "notafile.txt": "y\n"})
        not_a_dir = root / "notafile.txt"
        notes = root / "notes.txt"
        with self.assertRaises(claimcheck.InputError):
            claimcheck.build_report(not_a_dir, notes, str(not_a_dir), str(notes))

    def test_input_error_notes_file_missing(self):
        root = self.make_bundle({"a.py": "x\n"})
        with self.assertRaises(claimcheck.InputError):
            claimcheck.build_report(root, root / "no_notes.txt", str(root), str(root / "no_notes.txt"))

    def test_input_error_notes_path_is_a_directory(self):
        root = self.make_bundle({"sub/a.py": "x\n"})
        with self.assertRaises(claimcheck.InputError):
            claimcheck.build_report(root, root / "sub", str(root), str(root / "sub"))

    def test_input_error_notes_not_utf8(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = root / "notes.txt"
        notes.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(claimcheck.InputError):
            claimcheck.build_report(root, notes, str(root), str(notes))

    def test_empty_notes_file_zero_claims_exit_zero(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claim_count"], 0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "all_matched")

    def test_notes_with_no_claims_at_all_exit_zero(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Everything looked fine during review.\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claim_count"], 0)
        self.assertEqual(exit_code, 0)

    def test_all_matched_claims_exit_zero(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["claims"][0]["result"], claimcheck.RESULT_MATCHED)

    def test_one_mismatch_gives_exit_one(self):
        root = self.make_bundle({"a.py": "hello\n"})
        wrong = sha(b"not hello")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % wrong)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "issues_found")

    def test_one_unsubstantiated_gives_exit_one(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(missing.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 1)

    def test_one_unverifiable_command_gives_exit_one(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `bash a.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 1)

    def test_test_count_claim_triggers_real_unittest_run(self):
        root = self.make_bundle({
            "pkg.py": "def f():\n    return 1\n",
            "test_pkg.py": (
                "import unittest\nimport pkg\n"
                "class T(unittest.TestCase):\n"
                "    def test_one(self):\n"
                "        self.assertEqual(pkg.f(), 1)\n"
                "    def test_two(self):\n"
                "        self.assertTrue(True)\n"
            ),
        })
        notes = self.write_notes(root, "notes.txt", "Ran 2 tests, all green.\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["claims"][0]["observed_value"]["tests"], 2)

    def test_test_run_only_happens_once_even_with_multiple_claims(self):
        root = self.make_bundle({
            "pkg.py": "x = 1\n",
            "test_pkg.py": (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self):\n        self.assertTrue(True)\n"
            ),
        })
        notes = self.write_notes(root, "notes.txt", "Ran 1 test.\nRan 1 test again.\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claim_count"], 2)
        for c in report["claims"]:
            self.assertEqual(c["result"], claimcheck.RESULT_MATCHED)

    def test_no_test_count_claim_means_no_unittest_run(self):
        # If the bundle's test suite would fail to import, a report with no
        # TEST_COUNT_CLAIM must still succeed -- the suite is never run.
        root = self.make_bundle({
            "broken_test_module.py": "this is not python !!! ((",
            "a.py": "x\n",
        })
        h = sha(b"x\n")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 0)

    def test_claim_count_matches_len_claims(self):
        root = self.make_bundle({"a.py": "x\n"})
        h = sha(b"x\n")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\nsha256(a.py) = %s\n" % (h, h))
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claim_count"], len(report["claims"]))
        self.assertEqual(report["claim_count"], 2)

    def test_summary_counts_add_up(self):
        root = self.make_bundle({"a.py": "x\n"})
        h = sha(b"x\n")
        wrong = sha(b"y")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\nsha256(a.py) = %s\n" % (h, wrong))
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        s = report["summary"]
        total = s["matched"] + s["mismatched"] + s["unsubstantiated"] + s["unverifiable_command"]
        self.assertEqual(total, report["claim_count"])

    def test_report_has_expected_top_level_keys(self):
        # NOTE: this assertion was extended (schema_version 1 -> 2) to add
        # "checklist" and "human_review_notice" -- the new claim-to-artifact
        # linking / missing-limitations / unsupported-assertion checklist
        # and its mandatory human-review notice. Every field this test
        # already checked for is still present and unchanged; nothing was
        # removed, only added.
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "no claims here\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        expected = {"tool", "tool_version", "schema_version", "bundle_dir", "notes_file",
                    "claim_count", "claims", "checklist", "human_review_notice",
                    "status", "exit_code", "summary"}
        self.assertEqual(set(report.keys()), expected)

    def test_bundle_dir_and_notes_file_echo_cli_args_verbatim(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "no claims\n")
        report, _ = claimcheck.build_report(root, notes, "my_bundle", "my_bundle/notes.txt")
        self.assertEqual(report["bundle_dir"], "my_bundle")
        self.assertEqual(report["notes_file"], "my_bundle/notes.txt")

    def test_report_exit_code_field_matches_returned_exit_code(self):
        root = self.make_bundle({"a.py": "x\n"})
        h = sha(b"WRONG")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["exit_code"], exit_code)


# ==========================================================================
# discover_files / hash_bundle_files
# ==========================================================================

class TestDiscoverAndHash(TempBundleMixin, unittest.TestCase):
    def test_discover_files_sorted(self):
        root = self.make_bundle({"z.py": "1", "a.py": "2", "m/b.py": "3"})
        rels = claimcheck.discover_files(root)
        self.assertEqual(rels, sorted(rels))
        self.assertEqual(set(rels), {"z.py", "a.py", "m/b.py"})

    def test_discover_files_forward_slash(self):
        root = self.make_bundle({"a/b/c.py": "1"})
        rels = claimcheck.discover_files(root)
        self.assertIn("a/b/c.py", rels)

    def test_hash_bundle_files_real_sha256(self):
        root = self.make_bundle({"a.py": "hello"})
        rels = claimcheck.discover_files(root)
        hashes = claimcheck.hash_bundle_files(root, rels)
        self.assertEqual(hashes["a.py"], sha(b"hello"))

    def test_empty_bundle_no_files(self):
        root = self.make_bundle({})
        rels = claimcheck.discover_files(root)
        self.assertEqual(rels, [])


# ==========================================================================
# canonical_json_bytes
# ==========================================================================

class TestCanonicalJsonBytes(unittest.TestCase):
    def test_ends_with_single_trailing_newline(self):
        out = claimcheck.canonical_json_bytes({"a": 1})
        self.assertTrue(out.endswith(b"\n"))
        self.assertFalse(out.endswith(b"\n\n"))

    def test_no_spaces_after_separators(self):
        out = claimcheck.canonical_json_bytes({"b": 1, "a": [1, 2]})
        text = out.decode("ascii")
        self.assertNotIn(": ", text)
        self.assertNotIn(", ", text)

    def test_keys_sorted(self):
        out = claimcheck.canonical_json_bytes({"z": 1, "a": 2, "m": 3})
        text = out.decode("ascii")
        self.assertLess(text.index('"a"'), text.index('"m"'))
        self.assertLess(text.index('"m"'), text.index('"z"'))

    def test_ascii_only_escapes_unicode(self):
        out = claimcheck.canonical_json_bytes({"x": "café"})
        out.decode("ascii")  # must not raise
        self.assertIn(b"\\u00e9", out)

    def test_is_pure_ascii_bytes(self):
        out = claimcheck.canonical_json_bytes({"x": "héllo wörld"})
        # decode("ascii") raises UnicodeDecodeError on any byte >= 0x80, so
        # this both proves purity and gets us the text to inspect further.
        decoded = out.decode("ascii")
        self.assertTrue(all(b < 128 for b in out))
        self.assertIn("\\u00e9", decoded)  # é
        self.assertIn("\\u00f6", decoded)  # ö

    def test_round_trips_through_json_loads(self):
        obj = {"a": [3, 1, 2], "b": {"nested": True}}
        out = claimcheck.canonical_json_bytes(obj)
        self.assertEqual(json.loads(out.decode("ascii")), obj)


# ==========================================================================
# CLI / subprocess level
# ==========================================================================

class TestCLI(TempBundleMixin, unittest.TestCase):
    def run_cli(self, args, cwd=None):
        proc = subprocess.run(
            [sys.executable, TOOL_PATH] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_missing_all_args_exit_2(self):
        rc, out, err = self.run_cli([])
        self.assertEqual(rc, 2)
        self.assertEqual(out, b"")

    def test_missing_notes_arg_exit_2(self):
        root = self.make_bundle({"a.py": "x\n"})
        rc, out, err = self.run_cli([str(root)])
        self.assertEqual(rc, 2)

    def test_nonexistent_bundle_dir_exit_2(self):
        root = self.make_bundle({"notes.txt": "no claims\n"})
        rc, out, err = self.run_cli(["/nonexistent_dir_xyz_abc", str(root / "notes.txt")])
        self.assertEqual(rc, 2)
        self.assertIn(b"input error", err)

    def test_nonexistent_notes_file_exit_2(self):
        root = self.make_bundle({"a.py": "x\n"})
        rc, out, err = self.run_cli([str(root), str(root / "no_such_notes.txt")])
        self.assertEqual(rc, 2)

    def test_bundle_dir_is_a_file_exit_2(self):
        root = self.make_bundle({"a.py": "x\n"})
        rc, out, err = self.run_cli([str(root / "a.py"), str(root / "a.py")])
        self.assertEqual(rc, 2)

    def test_help_flag_exit_0(self):
        rc, out, err = self.run_cli(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn(b"usage", out.lower())

    def test_all_matched_exit_0(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc, 0)
        report = json.loads(out.decode("ascii"))
        self.assertEqual(report["exit_code"], 0)

    def test_mismatch_exit_1(self):
        root = self.make_bundle({"a.py": "hello\n"})
        wrong = sha(b"nope")
        self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % wrong)
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc, 1)

    def test_output_flag_writes_identical_bytes_to_stdout(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        out_path = root / "report.json"
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt"), "-o", str(out_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(out_path.read_bytes(), out)

    def test_output_long_flag(self):
        root = self.make_bundle({"a.py": "x\n"})
        self.write_notes(root, "notes.txt", "no claims\n")
        out_path = root / "report.json"
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt"), "--output", str(out_path)])
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())

    def test_output_creates_parent_directories(self):
        root = self.make_bundle({"a.py": "x\n"})
        self.write_notes(root, "notes.txt", "no claims\n")
        out_path = root / "nested" / "deep" / "report.json"
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt"), "-o", str(out_path)])
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())

    def test_stderr_empty_on_success(self):
        root = self.make_bundle({"a.py": "x\n"})
        self.write_notes(root, "notes.txt", "no claims\n")
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(err, b"")

    def test_notes_not_utf8_exit_2(self):
        root = self.make_bundle({"a.py": "x\n"})
        (root / "notes.txt").write_bytes(b"\xff\xfe garbage")
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc, 2)

    def test_empty_notes_file_exit_0(self):
        root = self.make_bundle({"a.py": "x\n"})
        self.write_notes(root, "notes.txt", "")
        rc, out, err = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc, 0)
        report = json.loads(out.decode("ascii"))
        self.assertEqual(report["claim_count"], 0)


# ==========================================================================
# Determinism / no-absolute-path contract
# ==========================================================================

class TestDeterminismAndNoAbsolutePath(TempBundleMixin, unittest.TestCase):
    def run_cli(self, args, cwd=None):
        proc = subprocess.run(
            [sys.executable, TOOL_PATH] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _mixed_bundle(self, root):
        write(root, "a.py", "hello\n")
        write(root, "b.py", "world\n")
        write(root, "test_pkg.py",
              "import unittest\nclass T(unittest.TestCase):\n"
              "    def test_a(self):\n        self.assertTrue(True)\n"
              "    def test_b(self):\n        self.assertTrue(True)\n")
        good_hash = sha(b"hello\n")
        bad_hash = sha(b"nope")
        notes = (
            "sha256(a.py) = %s\n"
            "sha256(b.py) = %s\n"
            "Ran 2 tests\n"
            "Ran 9 tests\n"
            "`python3 a.py` exit=0\n"
        ) % (good_hash, bad_hash)
        write(root, "notes.txt", notes)

    def test_two_runs_byte_identical(self):
        d1 = tempfile.TemporaryDirectory()
        self.addCleanup(d1.cleanup)
        root = Path(d1.name)
        self._mixed_bundle(root)
        rc1, out1, _ = self.run_cli([str(root), str(root / "notes.txt")])
        rc2, out2, _ = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc1, rc2)
        self.assertEqual(out1, out2)

    def test_no_absolute_path_fragments_in_report(self):
        d1 = tempfile.TemporaryDirectory()
        self.addCleanup(d1.cleanup)
        root = Path(d1.name)
        self._mixed_bundle(root)
        # Run with CLI args relative to root's own directory, so nothing
        # absolute is ever typed -- and confirm nothing absolute leaks in.
        proc = subprocess.run(
            [sys.executable, TOOL_PATH, ".", "notes.txt"],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        text = proc.stdout.decode("ascii")
        self.assertNotIn(str(root), text)
        self.assertNotIn("/tmp", text)
        self.assertNotIn("/home", text)
        self.assertNotIn("/sessions", text)

    def test_relocation_produces_byte_identical_report(self):
        parent1 = tempfile.TemporaryDirectory()
        parent2 = tempfile.TemporaryDirectory()
        self.addCleanup(parent1.cleanup)
        self.addCleanup(parent2.cleanup)
        root1 = Path(parent1.name) / "bundle_x"
        root2 = Path(parent2.name) / "somewhere" / "else" / "bundle_x"
        root1.mkdir(parents=True)
        root2.mkdir(parents=True)
        self._mixed_bundle(root1)
        self._mixed_bundle(root2)

        proc1 = subprocess.run(
            [sys.executable, TOOL_PATH, "bundle_x", "bundle_x/notes.txt"],
            cwd=str(root1.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        proc2 = subprocess.run(
            [sys.executable, TOOL_PATH, "bundle_x", "bundle_x/notes.txt"],
            cwd=str(root2.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        self.assertEqual(proc1.returncode, proc2.returncode)
        self.assertEqual(proc1.stdout, proc2.stdout)
        self.assertEqual(sha(proc1.stdout), sha(proc2.stdout))

    def test_claims_list_order_independent_of_dict_iteration(self):
        # Build the same report twice with claim occurrences constructed
        # in a different Python-level order; the final sorted list must
        # be identical either way.
        root = self.make_bundle({"a.py": "1", "b.py": "2"})
        h_a, h_b = sha(b"1"), sha(b"2")
        notes_text = "sha256(b.py) = %s\nsha256(a.py) = %s\n" % (h_b, h_a)
        notes = self.write_notes(root, "notes.txt", notes_text)
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claims"][0]["notes_line_number"], 1)
        self.assertEqual(report["claims"][1]["notes_line_number"], 2)


class TestFixtureBundles(unittest.TestCase):
    """Regression tests pinning the exact behaviour of the shipped fixtures."""

    def run_tool(self, bundle, notes, *extra_args):
        proc = subprocess.run(
            [sys.executable, TOOL_PATH, bundle, notes] + list(extra_args),
            cwd=THIS_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        return proc

    def test_bundle_truthful_exit_0(self):
        proc = self.run_tool("bundle_truthful", "bundle_truthful/notes_truthful.txt")
        self.assertEqual(proc.returncode, 0)
        report = json.loads(proc.stdout.decode("ascii"))
        self.assertTrue(all(c["result"] == "MATCHED" for c in report["claims"]))
        self.assertGreaterEqual(report["claim_count"], 3)

    def test_bundle_false_exit_1(self):
        proc = self.run_tool("bundle_false", "bundle_false/notes_false.txt")
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout.decode("ascii"))
        results = {c["result"] for c in report["claims"]}
        self.assertIn("MISMATCHED", results)
        self.assertIn("UNSUBSTANTIATED", results)
        self.assertIn("UNVERIFIABLE_COMMAND", results)
        self.assertIn("MATCHED", results)

    def test_bundle_false_every_claim_has_evidence_source(self):
        proc = self.run_tool("bundle_false", "bundle_false/notes_false.txt")
        report = json.loads(proc.stdout.decode("ascii"))
        for c in report["claims"]:
            self.assertTrue(c["evidence_source"])

    def test_bundle_false_two_runs_identical(self):
        p1 = self.run_tool("bundle_false", "bundle_false/notes_false.txt")
        p2 = self.run_tool("bundle_false", "bundle_false/notes_false.txt")
        self.assertEqual(p1.stdout, p2.stdout)

    def test_bundle_truthful_no_checklist_regressions_assumed(self):
        # bundle_truthful/notes_truthful.txt predates the checklist feature
        # and was never edited to add one -- it may or may not trigger
        # NO_DISCLOSED_LIMITATIONS/UNSUPPORTED_ASSERTION, but either way
        # checklist must never affect this fixture's exit code.
        proc = self.run_tool("bundle_truthful", "bundle_truthful/notes_truthful.txt")
        self.assertEqual(proc.returncode, 0)

    def test_bundle_repro_exit_1_without_run_repro(self):
        proc = self.run_tool("bundle_repro", "bundle_repro/notes_repro.txt")
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout.decode("ascii"))
        self.assertEqual(report["claims"][1]["repro_result"], "NOT_RUN")
        self.assertEqual(report["claims"][1]["result"], "MISMATCHED")

    def test_bundle_repro_exit_1_with_run_repro_and_mismatched_repro_result(self):
        proc = self.run_tool("bundle_repro", "bundle_repro/notes_repro.txt", "--run-repro")
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout.decode("ascii"))
        self.assertEqual(report["claims"][1]["repro_result"], "MISMATCHED")
        self.assertIn("observed real exit code 3", report["claims"][1]["repro_evidence_source"])

    def test_bundle_repro_checklist_has_both_new_kinds(self):
        proc = self.run_tool("bundle_repro", "bundle_repro/notes_repro.txt")
        report = json.loads(proc.stdout.decode("ascii"))
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertIn("NO_DISCLOSED_LIMITATIONS", kinds)
        self.assertIn("UNSUPPORTED_ASSERTION", kinds)
        self.assertNotIn("UNLINKED_CLAIM", kinds)  # both claims resolve to real bundle content

    def test_bundle_repro_two_runs_identical_with_run_repro(self):
        p1 = self.run_tool("bundle_repro", "bundle_repro/notes_repro.txt", "--run-repro")
        p2 = self.run_tool("bundle_repro", "bundle_repro/notes_repro.txt", "--run-repro")
        self.assertEqual(p1.returncode, p2.returncode)
        self.assertEqual(p1.stdout, p2.stdout)


# ==========================================================================
# NEW: claim-to-artifact linking
# ==========================================================================

class TestClaimArtifactLinking(TempBundleMixin, unittest.TestCase):
    def test_claim_with_backing_artifact_not_flagged(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertNotIn(claimcheck.CHECKLIST_UNLINKED_CLAIM, kinds)

    def test_sha256_claim_without_backing_artifact_flagged(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(missing.py) = %s\n" % h)
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        unlinked = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNLINKED_CLAIM]
        self.assertEqual(len(unlinked), 1)
        self.assertEqual(unlinked[0]["notes_line_number"], 1)
        self.assertEqual(unlinked[0]["claim_text"], "sha256(missing.py) = %s" % h)

    def test_exit_code_claim_with_missing_target_flagged_unlinked(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `python3 missing.py` and observed exit=0\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        unlinked = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNLINKED_CLAIM]
        self.assertEqual(len(unlinked), 1)

    def test_exit_code_claim_with_no_command_flagged_unlinked(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "we observed exit=0 with no command quoted\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        unlinked = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNLINKED_CLAIM]
        self.assertEqual(len(unlinked), 1)

    def test_bare_hash_with_no_match_flagged_unlinked(self):
        root = self.make_bundle({"a.py": "hello\n"})
        fake = sha(b"nothing-in-this-bundle-matches")
        notes = self.write_notes(root, "notes.txt", "Reference digest %s was noted.\n" % fake)
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        unlinked = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNLINKED_CLAIM]
        self.assertEqual(len(unlinked), 1)

    def test_unlinked_claim_checklist_entry_does_not_add_a_second_exit_path(self):
        # The claim is already UNSUBSTANTIATED (drives exit 1 on its own);
        # the checklist entry must not somehow ALSO independently matter.
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(missing.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["claims"][0]["result"], claimcheck.RESULT_UNSUBSTANTIATED)


# ==========================================================================
# NEW: reproduction-command results (--run-repro)
# ==========================================================================

class TestRunRepro(TempBundleMixin, unittest.TestCase):
    def test_default_is_not_run(self):
        root = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `python3 ok.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_NOT_RUN)
        self.assertIn("--run-repro", report["claims"][0]["repro_evidence_source"])
        self.assertEqual(exit_code, 0)

    def test_run_repro_matched(self):
        root = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `python3 ok.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(exit_code, 0)

    def test_run_repro_command_exits_nonzero_is_mismatched(self):
        root = self.make_bundle({"bad.py": "import sys\nsys.exit(5)\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `python3 bad.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_MISMATCHED)
        self.assertIn("observed real exit code 5", report["claims"][0]["repro_evidence_source"])
        self.assertEqual(exit_code, 1)

    def test_run_repro_uses_disposable_copy_not_original_bundle(self):
        # writer.py exits with the CURRENT value of a persistent counter,
        # then increments it. The direct (non-repro) check always runs
        # first, against bundle_dir itself, and writes counter.txt=1
        # there. If --run-repro's "disposable copy" were a bug and it
        # actually reused bundle_dir instead of a fresh copy, the repro
        # run would see that just-written counter.txt=1, exit(1), and
        # MISMATCH the claimed exit code of 0. Because the workspace is
        # copied from bundle_dir BEFORE the direct check ever runs, the
        # repro run instead sees a bundle with no counter.txt at all and
        # also exits 0 -- proving it really executed in an independent copy.
        root = self.make_bundle({"writer.py": (
            "import pathlib, sys\n"
            "p = pathlib.Path('counter.txt')\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\n"
            "sys.exit(n)\n"
        )})
        notes = self.write_notes(root, "notes.txt", "Ran `python3 writer.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_MATCHED)
        self.assertEqual((root / "counter.txt").read_text(), "1")

    def test_run_repro_workspace_tolerates_broken_symlink_in_bundle(self):
        # A real bug found while bug-hunting this extension: copytree's
        # default (symlinks=False) DEREFERENCES symlinks, so a broken
        # symlink anywhere in the bundle used to make --run-repro raise
        # an unconditional InputError (exit 2) even though the same
        # broken symlink is tolerated everywhere else in this tool
        # (hash_bundle_files just records it as unreadable). Fixed by
        # passing symlinks=True to shutil.copytree.
        root = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        os.symlink(root / "does_not_exist_target_xyz", root / "broken_link")
        notes = self.write_notes(root, "notes.txt", "Ran `python3 ok.py` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(exit_code, 0)

    def test_run_repro_extra_argument_absolute_path_refused(self):
        root = self.make_bundle({
            "reader.py": "import sys\nwith open(sys.argv[1]) as f:\n    f.read()\nprint('ok')\n",
        })
        notes = self.write_notes(root, "notes.txt", "Ran `python3 reader.py /etc/hostname` and observed exit=0\n")
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertIn("absolute", report["claims"][0]["repro_evidence_source"])
        self.assertEqual(exit_code, 1)

    def test_run_repro_extra_argument_dotdot_escape_refused(self):
        root = self.make_bundle({"reader.py": "print(1)\n"})
        notes = self.write_notes(
            root, "notes.txt",
            "Ran `python3 reader.py ../../../../../../etc/hostname` and observed exit=0\n",
        )
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertIn("outside", report["claims"][0]["repro_evidence_source"])
        self.assertEqual(exit_code, 1)

    def test_run_repro_can_flip_exit_code_independently_of_the_direct_check(self):
        # The ORIGINAL, direct EXIT_CODE_CLAIM check has no reason to fail
        # here (the script really does exit 0 when given an absolute path
        # that happens to be readable) -- only --run-repro's extra,
        # stricter argument-containment guard catches the absolute path
        # and turns it into an issue, proving the flag genuinely adds a
        # new, independent check rather than just repeating the old one.
        root = self.make_bundle({
            "reader.py": "import sys\nwith open(sys.argv[1]) as f:\n    f.read()\nprint('ok')\n",
        })
        notes = self.write_notes(root, "notes.txt", "Ran `python3 reader.py /etc/hostname` and observed exit=0\n")

        report_default, exit_default = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(report_default["claims"][0]["result"], claimcheck.RESULT_MATCHED)
        self.assertEqual(exit_default, 0)

        report_repro, exit_repro = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report_repro["claims"][0]["repro_result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)
        self.assertEqual(exit_repro, 1)

    def test_no_command_repro_result_is_unverifiable_when_run_repro(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "we observed exit=0 with no command quoted\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)

    def test_refused_command_repro_result_is_unverifiable_when_run_repro(self):
        root = self.make_bundle({"ok.py": "print(1)\n"})
        notes = self.write_notes(root, "notes.txt", "Ran `bash ok.py` and observed exit=0\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes), run_repro=True)
        self.assertEqual(report["claims"][0]["repro_result"], claimcheck.RESULT_UNVERIFIABLE_COMMAND)

    def test_vet_repro_arguments_rejects_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            ok, reason = claimcheck.vet_repro_arguments(["python3", "ok.py", "/etc/hostname"], Path(d))
            self.assertFalse(ok)
            self.assertIn("absolute", reason)

    def test_vet_repro_arguments_rejects_dotdot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "workspace"
            root.mkdir()
            ok, reason = claimcheck.vet_repro_arguments(["python3", "ok.py", "../escape.txt"], root)
            self.assertFalse(ok)
            self.assertIn("outside", reason)

    def test_vet_repro_arguments_allows_relative_inside(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "workspace"
            (root / "data").mkdir(parents=True)
            ok, reason = claimcheck.vet_repro_arguments(["python3", "ok.py", "data/file.txt"], root)
            self.assertTrue(ok)

    def test_resolve_within_workspace_absolute_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(claimcheck.resolve_within_workspace(Path(d), "/etc/hostname"))

    def test_resolve_within_workspace_dotdot_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "workspace"
            root.mkdir()
            self.assertIsNone(claimcheck.resolve_within_workspace(root, "../../outside"))

    def test_resolve_within_workspace_relative_inside_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "workspace"
            root.mkdir()
            resolved = claimcheck.resolve_within_workspace(root, "sub/file.txt")
            self.assertIsNotNone(resolved)
            self.assertTrue(resolved.startswith(os.path.realpath(str(root))))


# ==========================================================================
# NEW: missing disclosed limitations
# ==========================================================================

class TestLimitationsDisclosure(TempBundleMixin, unittest.TestCase):
    def test_no_limitations_section_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Everything works great, nothing to report.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertIn(claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS, kinds)

    def test_limitations_word_present_not_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Limitations: the parser is slow on huge files.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertNotIn(claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS, kinds)

    def test_known_issue_phrasing_not_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "Known issue: does not support Windows paths.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertNotIn(claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS, kinds)

    def test_missing_limitations_appears_at_most_once(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "no limitations mentioned here.\nor here.\nor here.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        # (deliberately the WORD "limitations" appears, so this is NOT flagged --
        # a companion test below checks the true-absence case only once.)
        kinds = [c["kind"] for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS]
        self.assertEqual(len(kinds), 0)

    def test_missing_limitations_flagged_exactly_once_across_many_lines(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "line one\nline two\nline three\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        kinds = [c["kind"] for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS]
        self.assertEqual(len(kinds), 1)

    def test_missing_limitations_does_not_flip_exit_code(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "sha256(a.py) = %s\n" % h)
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 0)
        kinds = {c["kind"] for c in report["checklist"]}
        self.assertIn(claimcheck.CHECKLIST_NO_DISCLOSED_LIMITATIONS, kinds)


# ==========================================================================
# NEW: unsupported assertions
# ==========================================================================

class TestUnsupportedAssertions(TempBundleMixin, unittest.TestCase):
    def test_unsupported_confident_line_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "This implementation is fully tested and guaranteed correct.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["notes_line_number"], 1)

    def test_supported_confident_line_with_hash_claim_not_flagged(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(root, "notes.txt", "This is fully verified: sha256(a.py) = %s\n" % h)
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(items, [])

    def test_supported_confident_line_with_command_not_flagged(self):
        root = self.make_bundle({"ok.py": "import sys\nsys.exit(0)\n"})
        notes = self.write_notes(root, "notes.txt", "This always works: `python3 ok.py` exit=0\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(items, [])

    def test_confidence_phrase_with_percent_and_no_claim_still_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "This works 100% of the time.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(len(items), 1)

    def test_plain_line_no_confidence_phrase_not_flagged(self):
        root = self.make_bundle({"a.py": "x\n"})
        notes = self.write_notes(root, "notes.txt", "This mostly works, some edge cases remain.\n")
        report, _ = claimcheck.build_report(root, notes, str(root), str(notes))
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(items, [])

    def test_unsupported_assertion_does_not_flip_exit_code(self):
        root = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        notes = self.write_notes(
            root, "notes.txt",
            "This is fully tested and always works.\nsha256(a.py) = %s\n" % h,
        )
        report, exit_code = claimcheck.build_report(root, notes, str(root), str(notes))
        self.assertEqual(exit_code, 0)
        items = [c for c in report["checklist"] if c["kind"] == claimcheck.CHECKLIST_UNSUPPORTED_ASSERTION]
        self.assertEqual(len(items), 1)


# ==========================================================================
# NEW: total-order tiebreak (canonical-JSON-dump as final sort key)
# ==========================================================================

class TestTiebreakTotalOrder(unittest.TestCase):
    def test_claims_tiebreak_orders_identical_line_offset_type_by_canonical_json(self):
        # Two claims sharing (line_no, offset, claim_type) -- impossible
        # from real extraction (two occurrences never share a start
        # offset), constructed here purely to pin down that the sort's
        # FINAL key really is the canonical JSON dump of the claim itself,
        # giving a genuine, reproducible total order rather than relying
        # on whatever order the claims happened to be built in.
        occ1 = claimcheck.ClaimOccurrence(claimcheck.CLAIM_SHA256, 1, 0, "bbb line",
                                           {"asserted_hash": "a" * 64, "filename": None})
        occ2 = claimcheck.ClaimOccurrence(claimcheck.CLAIM_SHA256, 1, 0, "aaa line",
                                           {"asserted_hash": "a" * 64, "filename": None})
        claim1 = claimcheck._claim_dict(occ1, {"x": 1}, None, claimcheck.RESULT_MATCHED, "e")
        claim2 = claimcheck._claim_dict(occ2, {"x": 1}, None, claimcheck.RESULT_MATCHED, "e")
        pairs = [(occ1, claim1), (occ2, claim2)]
        pairs.sort(key=lambda pair: (
            pair[0].line_no, pair[0].offset, claimcheck._TYPE_RANK[pair[0].claim_type],
            claimcheck.canonical_json_bytes(pair[1]),
        ))
        self.assertEqual([c["claim_text"] for _o, c in pairs], ["aaa line", "bbb line"])

    def test_checklist_tiebreak_orders_identical_kind_and_line_by_canonical_json(self):
        item1 = claimcheck._checklist_item(claimcheck.CHECKLIST_UNLINKED_CLAIM, 3, "zzz", "detail")
        item2 = claimcheck._checklist_item(claimcheck.CHECKLIST_UNLINKED_CLAIM, 3, "aaa", "detail")
        items = [item1, item2]
        items.sort(key=lambda it: (
            claimcheck._CHECKLIST_KIND_RANK[it["kind"]],
            -1 if it["notes_line_number"] is None else it["notes_line_number"],
            claimcheck.canonical_json_bytes(it),
        ))
        self.assertEqual([it["claim_text"] for it in items], ["aaa", "zzz"])


# ==========================================================================
# NEW: exit codes via subprocess, determinism/relocation with new fields
# ==========================================================================

class TestExitCodesViaSubprocessExtended(TempBundleMixin, unittest.TestCase):
    def run_cli(self, args, cwd=None):
        proc = subprocess.run(
            [sys.executable, TOOL_PATH] + args,
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_exit_0_1_2_all_distinct_via_subprocess(self):
        root0 = self.make_bundle({"a.py": "hello\n"})
        h = sha(b"hello\n")
        self.write_notes(root0, "notes.txt", "sha256(a.py) = %s\n" % h)
        rc0, out0, _ = self.run_cli([str(root0), str(root0 / "notes.txt")])
        self.assertEqual(rc0, 0)

        root1 = self.make_bundle({"a.py": "hello\n"})
        wrong = sha(b"nope")
        self.write_notes(root1, "notes.txt", "sha256(a.py) = %s\n" % wrong)
        rc1, out1, _ = self.run_cli([str(root1), str(root1 / "notes.txt")])
        self.assertEqual(rc1, 1)

        rc2, out2, err2 = self.run_cli(["/nonexistent_dir_for_exit_2_xyz", str(root1 / "notes.txt")])
        self.assertEqual(rc2, 2)

        self.assertEqual({rc0, rc1, rc2}, {0, 1, 2})

    def test_run_repro_flag_via_subprocess(self):
        root = self.make_bundle({"ok.py": "import sys\nsys.exit(9)\n"})
        self.write_notes(root, "notes.txt", "Ran `python3 ok.py` and observed exit=9\n")

        rc_default, out_default, _ = self.run_cli([str(root), str(root / "notes.txt")])
        self.assertEqual(rc_default, 0)
        report_default = json.loads(out_default.decode("ascii"))
        self.assertEqual(report_default["claims"][0]["repro_result"], "NOT_RUN")

        rc_repro, out_repro, _ = self.run_cli([str(root), str(root / "notes.txt"), "--run-repro"])
        self.assertEqual(rc_repro, 0)
        report_repro = json.loads(out_repro.decode("ascii"))
        self.assertEqual(report_repro["claims"][0]["repro_result"], "MATCHED")

    def test_two_runs_byte_identical_with_checklist_and_repro(self):
        root = self.make_bundle({
            "ok.py": "import sys\nsys.exit(0)\n",
            "test_ok.py": (
                "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_a(self):\n        self.assertTrue(True)\n"
            ),
        })
        notes_text = (
            "This module works 100% of the time.\n"
            "Ran `python3 ok.py` and observed exit=0\n"
            "Ran 1 test\n"
        )
        self.write_notes(root, "notes.txt", notes_text)
        rc1, out1, _ = self.run_cli([str(root), str(root / "notes.txt"), "--run-repro"])
        rc2, out2, _ = self.run_cli([str(root), str(root / "notes.txt"), "--run-repro"])
        self.assertEqual(rc1, rc2)
        self.assertEqual(out1, out2)
        self.assertEqual(sha(out1), sha(out2))


if __name__ == "__main__":
    unittest.main()

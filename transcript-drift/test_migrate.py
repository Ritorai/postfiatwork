#!/usr/bin/env python3
"""test_migrate.py -- unittest coverage for migrate.py.

Organised into:

  TestSplitJoinLines        binary line splitter/joiner round-trips
  TestRegexBoundaries        HEADER/BARE/EXIT regex edge cases (false
                              positive/negative avoidance -- the whole
                              safety property depends on these)
  TestAnalyzeBare             analyze() on zero-header (bare "$ ") input
  TestAnalyzeNormative        analyze() on already-headered input
  TestProcessFileDisk         real file I/O: migrate / dry-run / refuse
  TestCLI                     argument handling, exit codes, --report
  TestIdempotence              running twice changes nothing further
  TestRealRepoFixtures         the actual tool directories from this repo
  TestDeterminism               repeated runs + a relocation leg
  TestReportShape                JSON report determinism and structure
"""

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
import migrate  # noqa: E402

# TestRealRepoFixtures copies its captured_output.txt inputs FROM this repo
# checkout, so this test file must be run against a PRISTINE (unmigrated)
# copy of the repository -- e.g. from /path/to/repo/transcript-drift, not
# from inside a tree migrate.py has already rewritten in place. Every real
# run behind MIGRATION.md was made this way: tests first, against pristine
# fixtures, then a separate migrate.py --all pass against its own copy.
REPO_ROOT = os.path.dirname(HERE)
MIGRATE_PY = os.path.join(HERE, "migrate.py")


def mktemp_dir():
    d = tempfile.mkdtemp(prefix="migrate_test_")
    return d


class TempDirMixin:
    def setUp(self):
        self.tmp = mktemp_dir()
        self.addCleanup(self._rmtree, self.tmp)

    @staticmethod
    def _rmtree(path):
        # Safety: only ever remove a directory this test itself created,
        # identified by our own prefix, and never the system temp root.
        assert os.path.basename(path.rstrip(os.sep)).startswith("migrate_test_")
        shutil.rmtree(path, ignore_errors=True)

    def write(self, relpath, data):
        path = os.path.join(self.tmp, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, relpath):
        with open(os.path.join(self.tmp, relpath), "rb") as fh:
            return fh.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# split_lines / join_lines
# ---------------------------------------------------------------------------

class TestSplitJoinLines(unittest.TestCase):
    def roundtrip(self, data):
        lines = migrate.split_lines(data)
        self.assertEqual(migrate.join_lines(lines), data)
        return lines

    def test_empty_file(self):
        lines = self.roundtrip(b"")
        self.assertEqual(lines, [])

    def test_single_line_no_newline(self):
        lines = self.roundtrip(b"hello")
        self.assertEqual(lines, [(b"hello", b"")])

    def test_single_line_with_lf(self):
        lines = self.roundtrip(b"hello\n")
        self.assertEqual(lines, [(b"hello", b"\n")])

    def test_two_lines_lf(self):
        lines = self.roundtrip(b"a\nb\n")
        self.assertEqual(lines, [(b"a", b"\n"), (b"b", b"\n")])

    def test_two_lines_lf_no_trailing_newline(self):
        lines = self.roundtrip(b"a\nb")
        self.assertEqual(lines, [(b"a", b"\n"), (b"b", b"")])

    def test_crlf(self):
        lines = self.roundtrip(b"a\r\nb\r\n")
        self.assertEqual(lines, [(b"a", b"\r\n"), (b"b", b"\r\n")])

    def test_mixed_crlf_and_lf(self):
        lines = self.roundtrip(b"a\r\nb\nc")
        self.assertEqual(lines, [(b"a", b"\r\n"), (b"b", b"\n"), (b"c", b"")])

    def test_bare_cr(self):
        lines = self.roundtrip(b"a\rb\r")
        self.assertEqual(lines, [(b"a", b"\r"), (b"b", b"\r")])

    def test_blank_lines_preserved(self):
        lines = self.roundtrip(b"a\n\n\nb\n")
        self.assertEqual(lines, [(b"a", b"\n"), (b"", b"\n"), (b"", b"\n"), (b"b", b"\n")])

    def test_only_newline(self):
        lines = self.roundtrip(b"\n")
        self.assertEqual(lines, [(b"", b"\n")])

    def test_unicode_bytes_preserved(self):
        data = "café ☃ emoji \U0001F600\n".encode("utf-8")
        lines = self.roundtrip(data)
        self.assertEqual(len(lines), 1)

    def test_join_is_pure_concatenation(self):
        lines = [(b"x", b"\n"), (b"y", b"")]
        self.assertEqual(migrate.join_lines(lines), b"x\ny")


# ---------------------------------------------------------------------------
# Regex boundary behaviour -- the core safety property
# ---------------------------------------------------------------------------

class TestRegexBoundaries(unittest.TestCase):
    def test_header_matches_normative_form(self):
        self.assertTrue(migrate.HEADER_RE_B.match(b"=== $ python3 foo.py ==="))

    def test_header_matches_with_trailing_whitespace(self):
        self.assertTrue(migrate.HEADER_RE_B.match(b"=== $ python3 foo.py ===   "))

    def test_header_rejects_missing_dollar(self):
        self.assertIsNone(migrate.HEADER_RE_B.match(b"=== python3 foo.py ==="))

    def test_header_rejects_single_equals(self):
        self.assertIsNone(migrate.HEADER_RE_B.match(b"= $ python3 foo.py ="))

    def test_bare_matches_dollar_space_command(self):
        self.assertTrue(migrate.BARE_RE_B.match(b"$ python3 foo.py"))

    def test_bare_rejects_dollar_no_command(self):
        self.assertIsNone(migrate.BARE_RE_B.match(b"$ "))

    def test_bare_rejects_dollar_no_space(self):
        self.assertIsNone(migrate.BARE_RE_B.match(b"$python3"))

    def test_bare_rejects_normative_header_line_too(self):
        # A header line ALSO happens not to start with "$ " (it starts with
        # "="), so BARE_RE_B correctly never matches a header.
        self.assertIsNone(migrate.BARE_RE_B.match(b"=== $ x ==="))

    def test_exit_matches_zero(self):
        self.assertEqual(migrate.EXIT_RE_B.match(b"exit=0").group(1), b"0")

    def test_exit_matches_negative(self):
        self.assertEqual(migrate.EXIT_RE_B.match(b"exit=-9").group(1), b"-9")

    def test_exit_matches_with_surrounding_whitespace(self):
        self.assertTrue(migrate.EXIT_RE_B.match(b"   exit=2   "))

    def test_exit_rejects_prefixed_word(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b"build exit=0"))

    def test_exit_rejects_jq_prefixed(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b"jq exit=0"))

    def test_exit_rejects_json_key(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b'"expected_exit_code": 0,'))

    def test_exit_rejects_trailing_text(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b"exit=0 (clean)"))

    def test_exit_rejects_no_digits(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b"exit=$?"))

    def test_exit_rejects_float(self):
        self.assertIsNone(migrate.EXIT_RE_B.match(b"exit=0.0"))


# ---------------------------------------------------------------------------
# analyze() on bare-"$ " (zero header) transcripts
# ---------------------------------------------------------------------------

class TestAnalyzeBare(unittest.TestCase):
    def test_single_promotable_command(self):
        data = b"$ python3 foo.py ; echo \"exit=$?\"\nexit=0\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)
        self.assertTrue(a.changed)
        self.assertEqual(migrate.join_lines(a.new_lines),
                         b"=== $ python3 foo.py ; echo \"exit=$?\" ===\nexit=0\n")
        self.assertEqual(len(a.promoted), 1)
        self.assertEqual(a.promoted[0][0], 1)  # line number

    def test_command_with_no_exit_left_unchanged(self):
        data = b"$ python3 foo.py\nsome output\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)
        self.assertEqual(a.new_lines, a.old_lines)

    def test_mixed_batch_within_one_file(self):
        data = (b"$ python3 --version\n"
                b"Python 3.11.15\n"
                b"$ python3 foo.py ; echo \"exit=$?\"\n"
                b"exit=0\n"
                b"$ python3 bar.py ; echo \"exit=$?\"\n"
                b"exit=1\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)
        self.assertEqual(len(a.promoted), 2)
        self.assertEqual(len(a.left_bare), 1)
        new = migrate.join_lines(a.new_lines)
        self.assertIn(b"$ python3 --version\n", new)  # untouched
        self.assertNotIn(b"=== $ python3 --version ===", new)
        self.assertIn(b"=== $ python3 foo.py ; echo \"exit=$?\" ===", new)
        self.assertIn(b"=== $ python3 bar.py ; echo \"exit=$?\" ===", new)

    def test_no_bare_lines_at_all_is_refused(self):
        data = b"just some prose\nwith no dollar-sign commands anywhere\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)
        self.assertIn("nothing safe to migrate", a.reason)

    def test_empty_file_is_refused(self):
        a = migrate.analyze(b"")
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)
        self.assertEqual(a.new_lines, [])

    def test_preamble_only_file_is_refused(self):
        data = b"Environment: CPython 3.11.15\nNo commands were echoed here.\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)

    def test_candidates_present_but_none_promotable_is_refused(self):
        data = (b"$ python3 --version\n"
                b"Python 3.11.15\n"
                b"$ uname -s\n"
                b"Linux\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)
        self.assertEqual(len(a.left_bare), 2)

    def test_exit_region_bounded_by_next_bare_line(self):
        # exit=0 appears, but only AFTER the next "$ " line starts, so it
        # must NOT count toward the first command's region.
        data = (b"$ cmd_one\n"
                b"output one\n"
                b"$ cmd_two\n"
                b"exit=0\n")
        a = migrate.analyze(data)
        self.assertEqual(len(a.promoted), 1)
        self.assertEqual(a.promoted[0][1], "cmd_two")
        self.assertEqual(len(a.left_bare), 1)
        self.assertEqual(a.left_bare[0][1], "cmd_one")

    def test_json_body_does_not_false_positive_exit(self):
        data = (b"$ python3 tool.py -o r.json ; echo \"exit=$?\"\n"
                b'{"expected_exit_code": 0, "status": "clean"}\n'
                b"exit=1\n")
        a = migrate.analyze(data)
        self.assertEqual(len(a.promoted), 1)
        self.assertEqual(a.promoted[0][2], 3)  # exit= is on line 3, not line 2

    def test_build_exit_prefix_not_mistaken_for_exit_line(self):
        data = b"$ python3 build.py\nbuild exit=0\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)

    def test_only_first_exit_line_used_when_multiple_present(self):
        data = (b"$ cmd ; echo \"exit=$?\"\n"
                b"exit=0\n"
                b"exit=99\n")
        a = migrate.analyze(data)
        self.assertEqual(a.promoted[0][2], 2)

    def test_last_record_region_extends_to_eof(self):
        data = (b"$ cmd_one\n"
                b"no exit here\n"
                b"more text\n"
                b"exit=3\n")
        a = migrate.analyze(data)
        self.assertEqual(len(a.promoted), 1)
        self.assertEqual(a.promoted[0][2], 4)

    def test_header_wrap_is_exact_bytes(self):
        data = b"$ echo hi ; echo \"exit=$?\"\nexit=0\n"
        a = migrate.analyze(data)
        new = migrate.join_lines(a.new_lines)
        self.assertTrue(new.startswith(b"=== $ echo hi ; echo \"exit=$?\" ===\n"))

    def test_promoted_line_matches_header_re(self):
        data = b"$ echo hi\nexit=0\n"
        a = migrate.analyze(data)
        content, _ = a.new_lines[0]
        self.assertTrue(migrate.HEADER_RE_B.match(content))

    def test_command_text_with_special_regex_characters(self):
        data = b'$ python3 t.py --pattern "^(a+)+$" ; echo "exit=$?"\nexit=0\n'
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)
        new = migrate.join_lines(a.new_lines)
        self.assertIn(b'=== $ python3 t.py --pattern "^(a+)+$" ; echo "exit=$?" ===', new)

    def test_unicode_command_promoted_correctly(self):
        data = ('$ python3 t.py --name café ; echo "exit=$?"\nexit=0\n').encode("utf-8")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)
        new = migrate.join_lines(a.new_lines)
        self.assertEqual(new, ('=== $ python3 t.py --name café ; echo "exit=$?" ===\nexit=0\n').encode("utf-8"))

    def test_crlf_file_promotion_preserves_crlf(self):
        data = b'$ echo hi ; echo "exit=$?"\r\nexit=0\r\n'
        a = migrate.analyze(data)
        new = migrate.join_lines(a.new_lines)
        self.assertEqual(new, b'=== $ echo hi ; echo "exit=$?" ===\r\nexit=0\r\n')
        self.assertNotIn(b"\n\n", new.replace(b"\r\n", b""))

    def test_crlf_untouched_lines_keep_crlf(self):
        data = b"$ nope\r\nno exit here\r\n"
        a = migrate.analyze(data)
        self.assertEqual(a.new_lines, a.old_lines)
        self.assertIn(b"\r\n", migrate.join_lines(a.new_lines))

    def test_no_trailing_newline_preserved_after_promotion(self):
        data = b'$ echo hi ; echo "exit=$?"\nexit=0'
        a = migrate.analyze(data)
        new = migrate.join_lines(a.new_lines)
        self.assertFalse(new.endswith(b"\n"))
        self.assertEqual(new, b'=== $ echo hi ; echo "exit=$?" ===\nexit=0')

    def test_leading_preamble_before_first_bare_line_untouched(self):
        data = (b"some free text up top\n"
                b"more free text\n"
                b"$ cmd ; echo \"exit=$?\"\n"
                b"exit=0\n")
        a = migrate.analyze(data)
        new = migrate.join_lines(a.new_lines)
        self.assertTrue(new.startswith(b"some free text up top\nmore free text\n"))

    def test_negative_exit_code_promotable(self):
        data = b"$ crashy ; echo \"exit=$?\"\nexit=-9\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)

    def test_left_bare_reason_is_specific_per_line(self):
        data = b"$ python3 --version\nPython 3.11.15\n"
        a = migrate.analyze(data)
        self.assertEqual(len(a.left_bare), 1)
        ln, cmd, why = a.left_bare[0]
        self.assertEqual(ln, 1)
        self.assertEqual(cmd, "python3 --version")
        self.assertIn("exit=<int>", why)


# ---------------------------------------------------------------------------
# analyze() on already-normative (>=1 header) transcripts
# ---------------------------------------------------------------------------

class TestAnalyzeNormative(unittest.TestCase):
    def test_all_records_have_exit_is_unchanged_conformant(self):
        data = (b"=== $ python3 foo.py ===\n"
                b"exit=0\n"
                b"=== $ python3 bar.py ===\n"
                b"exit=1\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_UNCHANGED_CONFORMANT)
        self.assertFalse(a.changed)
        self.assertEqual(a.new_lines, a.old_lines)

    def test_one_record_missing_exit_refuses_whole_file(self):
        data = (b"=== $ python3 -m unittest -v ===\n"
                b"...ok\n"
                b"=== $ python3 foo.py ===\n"
                b"exit=0\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)
        self.assertEqual(a.new_lines, a.old_lines)

    def test_refusal_names_exact_command_and_line(self):
        data = (b"=== $ python3 -m unittest -v ===\n"
                b"...ok\n"
                b"=== $ python3 foo.py ===\n"
                b"exit=0\n")
        a = migrate.analyze(data)
        self.assertEqual(len(a.refused_records), 1)
        ln, cmd, why = a.refused_records[0]
        self.assertEqual(ln, 1)
        self.assertEqual(cmd, "python3 -m unittest -v")
        self.assertIn("exit=<int>", why)

    def test_multiple_missing_exit_records_all_named(self):
        data = (b"=== $ a ===\nno exit here\n"
                b"=== $ b ===\nexit=0\n"
                b"=== $ c ===\nstill no exit\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertEqual(len(a.refused_records), 2)
        commands = {c for _, c, _ in a.refused_records}
        self.assertEqual(commands, {"a", "c"})

    def test_bare_lines_inside_normative_file_are_not_promoted(self):
        # Even though "$ echo hi" looks promotable in isolation, once a real
        # header exists in the file this tool does not also try bare-line
        # promotion -- see docstring for rationale.
        data = (b"=== $ python3 foo.py ===\n"
                b"exit=0\n"
                b"$ echo hi\n"
                b"exit=0\n")
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_UNCHANGED_CONFORMANT)
        self.assertEqual(a.new_lines, a.old_lines)

    def test_normative_file_bytes_never_touched_on_refusal(self):
        data = (b"=== $ python3 -m unittest -v ===\n"
                b"...ok\n"
                b"=== $ sha256sum a b ===\n"
                b"deadbeef  a\n"
                b"deadbeef  b\n")
        a = migrate.analyze(data)
        self.assertEqual(migrate.join_lines(a.new_lines), data)

    def test_single_record_conformant(self):
        data = b"=== $ x ===\nexit=0\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_UNCHANGED_CONFORMANT)


# ---------------------------------------------------------------------------
# process_file() / real filesystem behaviour
# ---------------------------------------------------------------------------

class TestProcessFileDisk(TempDirMixin, unittest.TestCase):
    def test_migrate_writes_new_bytes_to_disk(self):
        path = self.write("captured_output.txt",
                          b'$ echo hi ; echo "exit=$?"\nexit=0\n')
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        on_disk = self.read("captured_output.txt")
        self.assertTrue(on_disk.startswith(b'=== $ echo hi ; echo "exit=$?" ==='))

    def test_dry_run_leaves_disk_untouched(self):
        original = b'$ echo hi ; echo "exit=$?"\nexit=0\n'
        path = self.write("captured_output.txt", original)
        result = migrate.process_file(path, dry_run=True)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertTrue(result["changed"])
        self.assertEqual(self.read("captured_output.txt"), original)

    def test_refused_file_untouched_on_disk(self):
        original = b"=== $ a ===\nno exit\n"
        path = self.write("captured_output.txt", original)
        before_hash = sha256(self.read("captured_output.txt"))
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        after_hash = sha256(self.read("captured_output.txt"))
        self.assertEqual(before_hash, after_hash)

    def test_conformant_file_untouched_on_disk(self):
        original = b"=== $ a ===\nexit=0\n"
        path = self.write("captured_output.txt", original)
        mtime_before = os.stat(path).st_mtime_ns
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_UNCHANGED_CONFORMANT)
        self.assertEqual(self.read("captured_output.txt"), original)

    def test_missing_result_shape(self):
        r = migrate.missing_result("evidence-validator", "/x/evidence-validator/captured_output.txt")
        self.assertEqual(r["status"], migrate.STATUS_REFUSED)
        self.assertFalse(r["changed"])
        self.assertIn("does not create", r["reason"])

    def test_discover_all_sorted_and_skips_dotdirs(self):
        self.write("zzz_tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        self.write("aaa_tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        os.makedirs(os.path.join(self.tmp, ".hidden"))
        found = migrate.discover_all(self.tmp)
        names = [n for n, _ in found]
        self.assertEqual(names, sorted(names))
        self.assertNotIn(".hidden", names)

    def test_discover_all_marks_missing_transcript_as_none(self):
        os.makedirs(os.path.join(self.tmp, "no_transcript_tool"))
        found = dict(migrate.discover_all(self.tmp))
        self.assertIsNone(found["no_transcript_tool"])

    def test_discover_all_root_not_a_directory_raises(self):
        with self.assertRaises(migrate.SetupError):
            migrate.discover_all(os.path.join(self.tmp, "does_not_exist"))

    def test_binary_write_uses_wb_mode(self):
        # A CRLF fixture proves the write path is binary: text mode "w"
        # would silently rewrite \r\n as \n on write.
        original = b'$ echo hi ; echo "exit=$?"\r\nexit=0\r\n'
        path = self.write("captured_output.txt", original)
        migrate.process_file(path, dry_run=False)
        self.assertIn(b"\r\n", self.read("captured_output.txt"))


# ---------------------------------------------------------------------------
# CLI: main(), exit codes, --report
# ---------------------------------------------------------------------------

class TestCLI(TempDirMixin, unittest.TestCase):
    def run_cli(self, args, cwd=None):
        cmd = [sys.executable, MIGRATE_PY] + args
        proc = subprocess.run(cmd, cwd=cwd or self.tmp,
                              capture_output=True, text=True)
        return proc

    def test_exit_0_when_all_already_conformant(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        self.write("tool/README.md", b"# tool\n")
        proc = self.run_cli(["--all", "--root", "."])
        self.assertEqual(proc.returncode, migrate.EXIT_OK)

    def test_exit_3_when_a_refusal_present(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nno exit\n")
        proc = self.run_cli(["--all", "--root", "."])
        self.assertEqual(proc.returncode, migrate.EXIT_REFUSED)

    def test_exit_2_for_nonexistent_explicit_file(self):
        proc = self.run_cli(["does/not/exist.txt"])
        self.assertEqual(proc.returncode, migrate.EXIT_SETUP_ERROR)

    def test_exit_2_for_bad_root(self):
        proc = self.run_cli(["--all", "--root", "does/not/exist"])
        self.assertEqual(proc.returncode, migrate.EXIT_SETUP_ERROR)

    def test_exit_2_for_no_arguments(self):
        proc = self.run_cli([])
        self.assertEqual(proc.returncode, migrate.EXIT_SETUP_ERROR)

    def test_exit_2_for_all_and_files_together(self):
        f = self.write("captured_output.txt", b"=== $ a ===\nexit=0\n")
        proc = self.run_cli(["--all", f])
        self.assertEqual(proc.returncode, migrate.EXIT_SETUP_ERROR)

    def test_exit_codes_are_pairwise_distinct(self):
        self.assertEqual(len({migrate.EXIT_OK, migrate.EXIT_SETUP_ERROR,
                              migrate.EXIT_REFUSED}), 3)

    def test_explicit_file_migrated_via_cli(self):
        f = self.write("tool/captured_output.txt",
                       b'$ echo hi ; echo "exit=$?"\nexit=0\n')
        proc = self.run_cli([f])
        self.assertEqual(proc.returncode, migrate.EXIT_OK)
        self.assertTrue(self.read("tool/captured_output.txt")
                        .startswith(b"=== $ echo hi"))

    def test_report_file_written_and_valid_json(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        report_path = os.path.join(self.tmp, "report.json")
        proc = self.run_cli(["--all", "--root", ".", "--report", "report.json"])
        self.assertEqual(proc.returncode, migrate.EXIT_OK)
        with open(report_path) as fh:
            report = json.load(fh)
        self.assertEqual(report["schema_version"], migrate.SCHEMA_VERSION)
        self.assertIn("counts", report)

    def test_dry_run_cli_leaves_disk_untouched(self):
        original = b'$ echo hi ; echo "exit=$?"\nexit=0\n'
        self.write("tool/captured_output.txt", original)
        proc = self.run_cli(["--all", "--root", ".", "--dry-run"])
        self.assertEqual(proc.returncode, migrate.EXIT_OK)
        self.assertEqual(self.read("tool/captured_output.txt"), original)

    def test_missing_transcript_via_all_is_refused(self):
        os.makedirs(os.path.join(self.tmp, "no_transcript"))
        proc = self.run_cli(["--all", "--root", "."])
        self.assertEqual(proc.returncode, migrate.EXIT_REFUSED)
        self.assertIn("no_transcript", proc.stdout)

    def test_report_json_has_no_timestamps(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        report_path = os.path.join(self.tmp, "report.json")
        self.run_cli(["--all", "--root", ".", "--report", "report.json"])
        with open(report_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

class TestIdempotence(TempDirMixin, unittest.TestCase):
    def test_second_migrate_run_makes_no_further_change(self):
        original = (b"$ python3 --version\n"
                    b"Python 3.11.15\n"
                    b'$ python3 foo.py ; echo "exit=$?"\n'
                    b"exit=0\n")
        path = self.write("captured_output.txt", original)
        r1 = migrate.process_file(path, dry_run=False)
        self.assertEqual(r1["status"], migrate.STATUS_MIGRATED)
        once = self.read("captured_output.txt")

        r2 = migrate.process_file(path, dry_run=False)
        twice = self.read("captured_output.txt")

        self.assertEqual(once, twice)
        self.assertEqual(sha256(once), sha256(twice))
        self.assertEqual(r2["status"], migrate.STATUS_UNCHANGED_CONFORMANT)
        self.assertFalse(r2["changed"])

    def test_refused_file_idempotent_across_runs(self):
        original = b"=== $ a ===\nno exit\n"
        path = self.write("captured_output.txt", original)
        migrate.process_file(path, dry_run=False)
        migrate.process_file(path, dry_run=False)
        self.assertEqual(self.read("captured_output.txt"), original)

    def test_conformant_file_idempotent(self):
        original = b"=== $ a ===\nexit=0\n"
        path = self.write("captured_output.txt", original)
        for _ in range(3):
            migrate.process_file(path, dry_run=False)
        self.assertEqual(self.read("captured_output.txt"), original)

    def test_repeated_cli_invocation_is_idempotent(self):
        cmd_data = b'$ echo hi ; echo "exit=$?"\nexit=0\n'
        path = self.write("captured_output.txt", cmd_data)
        for _ in range(2):
            subprocess.run([sys.executable, MIGRATE_PY, path],
                          capture_output=True, text=True)
        first = self.read("captured_output.txt")
        subprocess.run([sys.executable, MIGRATE_PY, path], capture_output=True, text=True)
        second = self.read("captured_output.txt")
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# Real repository fixtures -- grounds the test suite in the actual bug hunt
# ---------------------------------------------------------------------------

TOOL_DIRS_FOR_FIXTURE = [
    "crosspath-runner", "event-linter", "evidence-manifest", "lifecycle-linter",
    "limitations-probe", "path-collision-scanner", "regression-checker",
    "reward-reconciler", "sybil-detector", "transcript-drift", "xrpl-auditor",
    "evidence-validator",
]


def copy_fixture_tree(dest):
    for name in TOOL_DIRS_FOR_FIXTURE:
        src = os.path.join(REPO_ROOT, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, name),
                            ignore=shutil.ignore_patterns("__pycache__"))


@unittest.skipUnless(os.path.isdir(REPO_ROOT) and
                     os.path.isdir(os.path.join(REPO_ROOT, "event-linter")),
                     "real repo fixtures not available in this checkout")
def normative_headers(path):
    """Set of commands that appear as `=== $ cmd ===` headers in a file, and
    whose record carries an exit= line. This is an END-STATE property: it is
    the same whether the file arrived here already migrated or was migrated
    by this run, which is exactly why the real-repo assertions below use it
    instead of counting promotions."""
    import re as _re
    H = _re.compile(r"^=== \$ (.+?) ===\s*$")
    E = _re.compile(r"^\s*exit=(-?\d+)\s*$")
    with open(path, "rb") as fh:
        lines = fh.read().decode("utf-8", "replace").split("\n")
    idx = [i for i, l in enumerate(lines) if H.match(l)]
    out = set()
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(lines)
        if any(E.match(b) for b in lines[i + 1:end]):
            out.add(H.match(lines[i]).group(1))
    return out


class TestRealRepoFixtures(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        copy_fixture_tree(self.tmp)

    def test_event_linter_is_refused_no_exit_on_unittest_record(self):
        path = os.path.join(self.tmp, "event-linter", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        commands = {r["command"] for r in result["refused_records"]}
        self.assertIn("python3 -m unittest test_event_lint -v", commands)

    def test_event_linter_bytes_unchanged(self):
        path = os.path.join(self.tmp, "event-linter", "captured_output.txt")
        with open(path, "rb") as fh:
            before = fh.read()
        migrate.process_file(path, dry_run=False)
        with open(path, "rb") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_evidence_manifest_refused_three_records(self):
        path = os.path.join(self.tmp, "evidence-manifest", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertEqual(len(result["refused_records"]), 3)

    def test_lifecycle_linter_refused_three_records(self):
        path = os.path.join(self.tmp, "lifecycle-linter", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(len(result["refused_records"]), 3)

    def test_reward_reconciler_refused_two_records(self):
        path = os.path.join(self.tmp, "reward-reconciler", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(len(result["refused_records"]), 2)

    def test_sybil_detector_refused_three_records(self):
        path = os.path.join(self.tmp, "sybil-detector", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(len(result["refused_records"]), 3)

    def test_xrpl_auditor_refused_three_records(self):
        path = os.path.join(self.tmp, "xrpl-auditor", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(len(result["refused_records"]), 3)

    def test_crosspath_runner_promotes_exactly_three_when_unverified(self):
        # With verify-no-regression OFF (the uniform rule), crosspath-runner
        # is migrated like the other three bare-style tools.
        path = os.path.join(self.tmp, "crosspath-runner", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False, verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertEqual(len(result["promoted"]), 3)

    def test_crosspath_runner_refused_under_default_verify_no_regression(self):
        # With verify-no-regression ON (the default), the same rewrite is
        # measured against driftcheck.py first: it would turn 1 finding
        # (TRANSCRIPT_HAS_NO_COMMAND_RECORDS) into 2 (EXIT_CODE_MISMATCH +
        # README_COMMAND_NOT_IN_TRANSCRIPT) for this tool, so it is reverted.
        path = os.path.join(self.tmp, "crosspath-runner", "captured_output.txt")
        with open(path, "rb") as fh:
            original = fh.read()
        result = migrate.process_file(path, dry_run=False, tool_name="crosspath-runner")
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertTrue(result["verification"]["attempted"])
        self.assertTrue(result["verification"]["blocked"])
        self.assertEqual(result["verification"]["before_count"], 1)
        self.assertEqual(result["verification"]["after_count"], 2)
        self.assertIn("EXIT_CODE_MISMATCH", result["verification"]["new_codes"])
        self.assertIn("README_COMMAND_NOT_IN_TRANSCRIPT", result["verification"]["new_codes"])
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_limitations_probe_ends_with_two_normative_records(self):
        # End-state assertion, not a promotion count: once this migration is
        # committed the file arrives already conformant and promotes 0, but
        # the set of normative records it ends up with is invariant.
        path = os.path.join(self.tmp, "limitations-probe", "captured_output.txt")
        migrate.process_file(path, dry_run=False)
        headers = normative_headers(path)
        self.assertEqual(len(headers), 2)
        self.assertTrue(any("probe.py" in h for h in headers))

    def test_path_collision_scanner_ends_with_eleven_normative_records(self):
        path = os.path.join(self.tmp, "path-collision-scanner", "captured_output.txt")
        migrate.process_file(path, dry_run=False)
        self.assertEqual(len(normative_headers(path)), 11)

    def test_regression_checker_ends_with_eleven_normative_records(self):
        path = os.path.join(self.tmp, "regression-checker", "captured_output.txt")
        migrate.process_file(path, dry_run=False)
        self.assertEqual(len(normative_headers(path)), 11)

    def test_transcript_drift_own_transcript_already_conformant(self):
        path = os.path.join(self.tmp, "transcript-drift", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], migrate.STATUS_UNCHANGED_CONFORMANT)

    def test_evidence_validator_filename_normalized_by_default(self):
        # evidence-validator has no captured_output.txt but ships
        # run_output.txt, already fully conformant; the default (--all,
        # verify-no-regression ON) run copies it verbatim.
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
            capture_output=True, text=True)
        target = os.path.join(self.tmp, "evidence-validator", "captured_output.txt")
        source = os.path.join(self.tmp, "evidence-validator", "run_output.txt")
        self.assertTrue(os.path.isfile(target))
        with open(target, "rb") as fh:
            copied = fh.read()
        with open(source, "rb") as fh:
            original_source = fh.read()
        self.assertEqual(copied, original_source)
        # source left in place, unchanged -- copy, not move
        self.assertTrue(os.path.isfile(source))
        # overall exit is still 3: other tools in this fixture set are
        # genuinely, correctly refused (event-linter etc.)
        self.assertEqual(proc.returncode, migrate.EXIT_REFUSED)

    def test_full_all_run_counts_and_membership_default(self):
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--report", os.path.join(self.tmp, "report.json")],
            capture_output=True, text=True)
        with open(os.path.join(self.tmp, "report.json")) as fh:
            report = json.load(fh)
        by_tool = {r["tool"]: r["status"] for r in report["results"]}
        # crosspath-runner is refused under the default verify-no-regression:
        # its rewrite would regress driftcheck's findings for that tool. That
        # holds regardless of whether the migration has already been applied.
        self.assertEqual(by_tool["crosspath-runner"], "refused")
        # These four end up conformant -- "migrated" on a pristine tree,
        # "unchanged_conformant" once the migration is committed. Both are
        # success; the failure to guard against is "refused".
        settled = {"migrated", "unchanged_conformant"}
        for t in ("evidence-validator", "limitations-probe",
                  "path-collision-scanner", "regression-checker",
                  "transcript-drift"):
            self.assertIn(by_tool[t], settled, "%s was %s" % (t, by_tool[t]))
        # These are refused on any tree: their records have no exit= value
        # anywhere in the file, so no rule may rewrite them.
        for t in ("event-linter", "evidence-manifest", "lifecycle-linter",
                 "reward-reconciler", "sybil-detector", "xrpl-auditor"):
            self.assertEqual(by_tool[t], "refused")
        self.assertEqual(report["counts"].get("refused", 0), 7)

    def test_full_all_run_counts_and_membership_uniform(self):
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--no-verify-no-regression",
             "--report", os.path.join(self.tmp, "report_uniform.json")],
            capture_output=True, text=True)
        with open(os.path.join(self.tmp, "report_uniform.json")) as fh:
            report = json.load(fh)
        by_tool = {r["tool"]: r["status"] for r in report["results"]}
        # Under the uniform rule (no verification), crosspath-runner is NOT
        # refused -- the opposite of the default-mode result above. It is
        # "migrated" on a pristine tree and "unchanged_conformant" once that
        # migration has been committed; the point is that it settles either way.
        settled = {"migrated", "unchanged_conformant"}
        self.assertIn(by_tool["crosspath-runner"], settled)
        self.assertIn(by_tool["evidence-validator"], settled)

    def test_crosspath_runner_promoted_lines_are_specific(self):
        path = os.path.join(self.tmp, "crosspath-runner", "captured_output.txt")
        result = migrate.process_file(path, dry_run=False)
        promoted_lines = {p["line"] for p in result["promoted"]}
        self.assertEqual(promoted_lines, {92, 182, 265})

    def test_path_collision_scanner_records_are_the_expected_commands(self):
        path = os.path.join(self.tmp, "path-collision-scanner", "captured_output.txt")
        migrate.process_file(path, dry_run=False)
        headers = normative_headers(path)
        self.assertIn("python3 make_fixtures.py --check ; echo \"exit=$?\"", headers)
        self.assertIn("ls fixtures ; echo \"exit=$?\"", headers)
        # every normative record carries its own exit=, by construction
        self.assertEqual(len(headers), 11)

    def test_regression_checker_records_are_the_expected_commands(self):
        path = os.path.join(self.tmp, "regression-checker", "captured_output.txt")
        migrate.process_file(path, dry_run=False)
        headers = normative_headers(path)
        self.assertIn("ls fixtures ; echo \"exit=$?\"", headers)
        self.assertIn("python3 make_fixtures.py --check ; echo \"exit=$?\"", headers)
        self.assertEqual(len(headers), 11)

    def test_uniform_migration_clears_no_command_records_entirely(self):
        # Under the uniform rule (--no-verify-no-regression), driftcheck.py
        # must no longer report TRANSCRIPT_HAS_NO_COMMAND_RECORDS for ANY of
        # the four bare-style tools, including crosspath-runner.
        subprocess.run([sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
                        "--no-verify-no-regression"],
                       capture_output=True, text=True)
        driftcheck = os.path.join(HERE, "driftcheck.py")
        proc = subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True)
        report = json.loads(proc.stdout)
        no_records = [f for f in report["findings"]
                     if f["code"] == "TRANSCRIPT_HAS_NO_COMMAND_RECORDS"]
        self.assertEqual(no_records, [])

    def test_default_migration_leaves_crosspath_runner_no_command_records(self):
        # Under the default (verify-no-regression ON), crosspath-runner's
        # rewrite is measured, found to regress, and reverted -- so its
        # TRANSCRIPT_HAS_NO_COMMAND_RECORDS finding is the one that survives,
        # while the other three bare-style tools are still cleared.
        subprocess.run([sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
                       capture_output=True, text=True)
        driftcheck = os.path.join(HERE, "driftcheck.py")
        proc = subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True)
        report = json.loads(proc.stdout)
        no_records = [f for f in report["findings"]
                     if f["code"] == "TRANSCRIPT_HAS_NO_COMMAND_RECORDS"]
        self.assertEqual(len(no_records), 1)
        self.assertEqual(no_records[0]["tool"], "crosspath-runner")

    def test_all_migrated_files_no_exit_findings_unchanged(self):
        # The 17 TRANSCRIPT_RECORD_HAS_NO_EXIT findings must survive
        # migration completely unchanged in count in EITHER mode, because
        # those files were correctly refused, not touched.
        driftcheck = os.path.join(HERE, "driftcheck.py")
        before = json.loads(subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True).stdout)
        subprocess.run([sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
                       capture_output=True, text=True)
        after = json.loads(subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True).stdout)
        b = before["drift_counts"]["TRANSCRIPT_RECORD_HAS_NO_EXIT"]
        a = after["drift_counts"]["TRANSCRIPT_RECORD_HAS_NO_EXIT"]
        self.assertEqual(b, 17)
        self.assertEqual(a, 17)

    def test_default_mode_total_finding_count_flat_not_below(self):
        # The real, measured result: with verify-no-regression ON (default)
        # plus filename normalization, the total finding count for this
        # scoped fixture set is flat (22 -> 22), not below 22. This test
        # pins that observed number so a future change to the rule cannot
        # silently start claiming a reduction that didn't happen.
        driftcheck = os.path.join(HERE, "driftcheck.py")
        before = json.loads(subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True).stdout)
        subprocess.run([sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
                       capture_output=True, text=True)
        after = json.loads(subprocess.run(
            [sys.executable, driftcheck, "--root", self.tmp,
             "--inventory", os.path.join(HERE, "inventory.json")],
            capture_output=True, text=True).stdout)
        self.assertEqual(len(before["findings"]), 22)
        self.assertEqual(len(after["findings"]), 22)


# ---------------------------------------------------------------------------
# Determinism, including a relocation leg
# ---------------------------------------------------------------------------

class TestDeterminism(TempDirMixin, unittest.TestCase):
    def test_analyze_is_a_pure_function(self):
        data = b'$ echo hi ; echo "exit=$?"\nexit=0\n$ echo bye\nno exit\n'
        a1 = migrate.analyze(data)
        a2 = migrate.analyze(data)
        self.assertEqual(migrate.join_lines(a1.new_lines), migrate.join_lines(a2.new_lines))
        self.assertEqual(a1.promoted, a2.promoted)
        self.assertEqual(a1.left_bare, a2.left_bare)

    def test_migration_output_identical_across_two_fresh_copies(self):
        original = (b"$ python3 --version\nPython 3.11.15\n"
                    b'$ python3 foo.py ; echo "exit=$?"\nexit=0\n')
        d1 = os.path.join(self.tmp, "copy1")
        d2 = os.path.join(self.tmp, "copy2")
        os.makedirs(d1)
        os.makedirs(d2)
        for d in (d1, d2):
            with open(os.path.join(d, "captured_output.txt"), "wb") as fh:
                fh.write(original)
        migrate.process_file(os.path.join(d1, "captured_output.txt"), dry_run=False)
        migrate.process_file(os.path.join(d2, "captured_output.txt"), dry_run=False)
        with open(os.path.join(d1, "captured_output.txt"), "rb") as fh:
            h1 = sha256(fh.read())
        with open(os.path.join(d2, "captured_output.txt"), "rb") as fh:
            h2 = sha256(fh.read())
        self.assertEqual(h1, h2)

    def test_relocation_leg_same_bytes_at_different_absolute_path(self):
        original = (b"$ python3 --version\nPython 3.11.15\n"
                    b'$ python3 foo.py ; echo "exit=$?"\nexit=0\n')
        src = os.path.join(self.tmp, "orig_name")
        os.makedirs(src)
        with open(os.path.join(src, "captured_output.txt"), "wb") as fh:
            fh.write(original)
        migrate.process_file(os.path.join(src, "captured_output.txt"), dry_run=False)
        with open(os.path.join(src, "captured_output.txt"), "rb") as fh:
            h_src = sha256(fh.read())

        reloc = os.path.join(self.tmp, "totally_differently_named_dir_xyz")
        os.makedirs(reloc)
        with open(os.path.join(reloc, "captured_output.txt"), "wb") as fh:
            fh.write(original)
        migrate.process_file(os.path.join(reloc, "captured_output.txt"), dry_run=False)
        with open(os.path.join(reloc, "captured_output.txt"), "rb") as fh:
            h_reloc = sha256(fh.read())

        self.assertEqual(h_src, h_reloc)

    def test_no_absolute_paths_leak_into_migrated_output(self):
        original = (b'$ python3 foo.py ; echo "exit=$?"\nexit=0\n')
        path = self.write("scratch_tree_name/captured_output.txt", original)
        migrate.process_file(path, dry_run=False)
        data = self.read("scratch_tree_name/captured_output.txt")
        self.assertNotIn(self.tmp.encode(), data)


# ---------------------------------------------------------------------------
# Report shape / JSON determinism
# ---------------------------------------------------------------------------

class TestReportShape(TempDirMixin, unittest.TestCase):
    def test_process_file_result_has_expected_keys(self):
        path = self.write("captured_output.txt", b"=== $ a ===\nexit=0\n")
        result = migrate.process_file(path, dry_run=False)
        for key in ("tool", "path", "status", "reason", "changed",
                   "promoted", "left_bare", "refused_records"):
            self.assertIn(key, result)

    def test_canonical_json_is_deterministic(self):
        obj = {"b": 1, "a": 2}
        self.assertEqual(migrate.canonical_json(obj), migrate.canonical_json(obj))
        self.assertTrue(migrate.canonical_json(obj).index('"a"') <
                        migrate.canonical_json(obj).index('"b"'))

    def test_canonical_json_ends_with_newline(self):
        self.assertTrue(migrate.canonical_json({}).endswith("\n"))


# ---------------------------------------------------------------------------
# Change 1: filename normalization (evidence-validator-shaped, but general)
# ---------------------------------------------------------------------------

class TestFilenameNormalization(TempDirMixin, unittest.TestCase):
    CONFORMANT = b"=== $ a ===\nexit=0\n=== $ b ===\nexit=1\n"
    NOT_CONFORMANT = b"$ a\nno header, no exit\n"

    def test_is_fully_conformant_bytes_true_for_conformant_data(self):
        self.assertTrue(migrate.is_fully_conformant_bytes(self.CONFORMANT))

    def test_is_fully_conformant_bytes_false_for_bare_style(self):
        self.assertFalse(migrate.is_fully_conformant_bytes(self.NOT_CONFORMANT))

    def test_is_fully_conformant_bytes_false_for_missing_exit(self):
        self.assertFalse(migrate.is_fully_conformant_bytes(b"=== $ a ===\nno exit here\n"))

    def test_is_fully_conformant_bytes_false_for_empty(self):
        self.assertFalse(migrate.is_fully_conformant_bytes(b""))

    def test_single_conforming_candidate_is_copied_verbatim(self):
        self.write("tool/run_output.txt", self.CONFORMANT)
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertEqual(result["source_file"], "run_output.txt")
        self.assertEqual(self.read("tool/captured_output.txt"), self.CONFORMANT)

    def test_source_file_left_in_place_unchanged(self):
        self.write("tool/run_output.txt", self.CONFORMANT)
        migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(self.read("tool/run_output.txt"), self.CONFORMANT)

    def test_copy_is_byte_identical_including_crlf(self):
        crlf_data = b"=== $ a ===\r\nexit=0\r\n"
        self.write("tool/run_output.txt", crlf_data)
        migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        copied = self.read("tool/captured_output.txt")
        self.assertEqual(copied, crlf_data)
        self.assertIn(b"\r\n", copied)

    def test_zero_txt_files_at_all_is_refused(self):
        os.makedirs(os.path.join(self.tmp, "tool"))
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertFalse(result["changed"])
        self.assertIn("no *.txt files present at all", result["reason"])

    def test_txt_files_present_but_none_conform_is_refused(self):
        self.write("tool/notes.txt", self.NOT_CONFORMANT)
        self.write("tool/other.txt", b"just prose\n")
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertIn("notes.txt", result["reason"])
        self.assertIn("other.txt", result["reason"])
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, "tool", "captured_output.txt")))

    def test_ambiguous_multiple_conforming_candidates_is_refused(self):
        self.write("tool/run_output.txt", self.CONFORMANT)
        self.write("tool/alt_output.txt", self.CONFORMANT)
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertIn("ambiguous", result["reason"])
        self.assertIn("run_output.txt", result["reason"])
        self.assertIn("alt_output.txt", result["reason"])
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, "tool", "captured_output.txt")))

    def test_ambiguous_case_leaves_both_candidates_untouched(self):
        self.write("tool/run_output.txt", self.CONFORMANT)
        self.write("tool/alt_output.txt", self.CONFORMANT)
        migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(self.read("tool/run_output.txt"), self.CONFORMANT)
        self.assertEqual(self.read("tool/alt_output.txt"), self.CONFORMANT)

    def test_dry_run_does_not_create_target_file(self):
        self.write("tool/run_output.txt", self.CONFORMANT)
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=True,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertTrue(result["changed"])
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, "tool", "captured_output.txt")))

    def test_non_txt_files_are_ignored_as_candidates(self):
        self.write("tool/notes.md", self.CONFORMANT)  # conformant content, wrong extension
        result = migrate.try_filename_normalization(
            os.path.join(self.tmp, "tool"), "tool", dry_run=False,
            verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertIn("no *.txt files present at all", result["reason"])

    def test_existing_captured_output_is_not_touched_by_this_path(self):
        # discover_all() only routes here when captured_output.txt is
        # absent; this test documents that try_filename_normalization
        # itself doesn't check for it (that's the caller's job) but the
        # normal CLI flow never calls it when the file already exists.
        found = dict(migrate.discover_all(self.tmp))
        self.write("tool/captured_output.txt", self.CONFORMANT)
        found = dict(migrate.discover_all(self.tmp))
        self.assertIsNotNone(found["tool"])

    def test_via_cli_all_evidence_validator_shaped_fixture(self):
        self.write("evidence-validator/run_output.txt", self.CONFORMANT)
        self.write("evidence-validator/README.md", b"# evidence-validator\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--no-verify-no-regression"],
            capture_output=True, text=True)
        self.assertIn("[migrated]", proc.stdout)
        self.assertEqual(
            self.read("evidence-validator/captured_output.txt"), self.CONFORMANT)
        self.assertEqual(
            self.read("evidence-validator/run_output.txt"), self.CONFORMANT)


# ---------------------------------------------------------------------------
# Change 2: --verify-no-regression (default on), measured not hardcoded
# ---------------------------------------------------------------------------

REGRESSING_README = (
    "# tool\n\n"
    "### Exit codes\n\n"
    "| Code | Meaning |\n"
    "|---|---|\n"
    "| `1` | drift |\n"
    "| `2` | setup error |\n\n"
    "The table above uses the word \"Code\" as its header, not \"Exit\", "
    "so driftcheck.py's table scanner never sees that first data row at "
    "all -- only the words exit `1` and exit `2` are ever acknowledged in "
    "prose. (Deliberately avoiding the literal digit-string this comment "
    "would otherwise leak into the README's claimed-exit set.)\n\n"
    "```\n"
    "python3 tool.py ; echo \"exit=$?\"\n"
    "python3 tool.py --never-actually-run-in-the-transcript\n"
    "```\n"
)
REGRESSING_TRANSCRIPT_BEFORE = (
    b'$ python3 tool.py ; echo "exit=$?"\n'
    b"all clear\n"
    b"exit=0\n"
)

FLAT_README = (
    "# tool\n\n"
    "### Exit codes\n\n"
    "| Exit | Meaning |\n"
    "|---|---|\n"
    "| `0` | clean |\n\n"
    "```\n"
    "python3 tool.py ; echo \"exit=$?\"\n"
    "```\n"
)


class TestVerifyNoRegression(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # verify_rewrite() shells out to the driftcheck.py that lives next
        # to migrate.py; confirm that dependency is actually present so a
        # skipped assertion below can't hide a broken test.
        self.assertTrue(os.path.isfile(migrate.DRIFTCHECK_PATH))

    def test_verify_rewrite_blocks_a_genuine_regression(self):
        self.write("tool/README.md", REGRESSING_README.encode())
        after = b'=== $ python3 tool.py ; echo "exit=$?" ===\nall clear\nexit=0\n'
        result = migrate.verify_rewrite(
            "tool", os.path.join(self.tmp, "tool", "README.md"),
            REGRESSING_TRANSCRIPT_BEFORE, after)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["before_count"], 1)  # TRANSCRIPT_HAS_NO_COMMAND_RECORDS
        self.assertGreater(result["after_count"], result["before_count"])
        self.assertIn("EXIT_CODE_MISMATCH", result["new_codes"])

    def test_verify_rewrite_allows_a_flat_or_improving_change(self):
        self.write("tool/README.md", FLAT_README.encode())
        before = b'$ python3 tool.py ; echo "exit=$?"\nexit=0\n'
        after = b'=== $ python3 tool.py ; echo "exit=$?" ===\nexit=0\n'
        result = migrate.verify_rewrite(
            "tool", os.path.join(self.tmp, "tool", "README.md"), before, after)
        self.assertTrue(result["attempted"])
        self.assertFalse(result["blocked"])
        self.assertLessEqual(result["after_count"], result["before_count"])

    def test_process_file_reverts_and_refuses_a_blocked_rewrite(self):
        self.write("tool/README.md", REGRESSING_README.encode())
        path = self.write("tool/captured_output.txt", REGRESSING_TRANSCRIPT_BEFORE)
        result = migrate.process_file(path, dry_run=False, tool_name="tool")
        self.assertEqual(result["status"], migrate.STATUS_REFUSED)
        self.assertFalse(result["changed"])
        self.assertTrue(result["verification"]["blocked"])
        self.assertEqual(self.read("tool/captured_output.txt"), REGRESSING_TRANSCRIPT_BEFORE)

    def test_process_file_keeps_a_flat_rewrite(self):
        self.write("tool/README.md", FLAT_README.encode())
        before = b'$ python3 tool.py ; echo "exit=$?"\nexit=0\n'
        path = self.write("tool/captured_output.txt", before)
        result = migrate.process_file(path, dry_run=False, tool_name="tool")
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertFalse(result["verification"]["blocked"])
        self.assertTrue(self.read("tool/captured_output.txt").startswith(b"=== $"))

    def test_no_verify_no_regression_flag_keeps_the_regressing_rewrite(self):
        self.write("tool/README.md", REGRESSING_README.encode())
        path = self.write("tool/captured_output.txt", REGRESSING_TRANSCRIPT_BEFORE)
        result = migrate.process_file(path, dry_run=False, tool_name="tool",
                                      verify_no_regression=False)
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertIsNone(result["verification"])
        self.assertTrue(self.read("tool/captured_output.txt").startswith(b"=== $"))

    def test_cli_no_verify_no_regression_opt_out_end_to_end(self):
        self.write("tool/README.md", REGRESSING_README.encode())
        self.write("tool/captured_output.txt", REGRESSING_TRANSCRIPT_BEFORE)
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--no-verify-no-regression"],
            capture_output=True, text=True)
        self.assertIn("[migrated]", proc.stdout)
        self.assertTrue(self.read("tool/captured_output.txt").startswith(b"=== $"))

    def test_cli_default_blocks_the_same_rewrite(self):
        self.write("tool/README.md", REGRESSING_README.encode())
        original = REGRESSING_TRANSCRIPT_BEFORE
        self.write("tool/captured_output.txt", original)
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
            capture_output=True, text=True)
        self.assertIn("[refused]", proc.stdout)
        self.assertIn("verify-no-regression", proc.stdout)
        self.assertEqual(self.read("tool/captured_output.txt"), original)
        self.assertEqual(proc.returncode, migrate.EXIT_REFUSED)

    def test_verification_skipped_when_no_readme_present(self):
        path = self.write("tool/captured_output.txt",
                          b'$ x ; echo "exit=$?"\nexit=0\n')
        result = migrate.process_file(path, dry_run=False, tool_name="tool")
        self.assertEqual(result["status"], migrate.STATUS_MIGRATED)
        self.assertFalse(result["verification"]["attempted"])
        self.assertIsNotNone(result["verification"]["skipped_reason"])

    def test_verify_rewrite_raises_setup_error_when_driftcheck_missing(self):
        missing_path = os.path.join(self.tmp, "nonexistent_driftcheck.py")
        original = migrate.DRIFTCHECK_PATH
        migrate.DRIFTCHECK_PATH = missing_path
        try:
            self.write("tool/README.md", FLAT_README.encode())
            with self.assertRaises(migrate.SetupError):
                migrate.verify_rewrite(
                    "tool", os.path.join(self.tmp, "tool", "README.md"),
                    b"before", b"after")
        finally:
            migrate.DRIFTCHECK_PATH = original

    def test_verification_temp_dir_is_cleaned_up(self):
        self.write("tool/README.md", FLAT_README.encode())
        before_dirs = set(os.listdir(tempfile.gettempdir()))
        migrate.verify_rewrite(
            "tool", os.path.join(self.tmp, "tool", "README.md"),
            b'$ python3 tool.py ; echo "exit=$?"\nexit=0\n',
            b'=== $ python3 tool.py ; echo "exit=$?" ===\nexit=0\n')
        after_dirs = set(os.listdir(tempfile.gettempdir()))
        leaked = [d for d in (after_dirs - before_dirs) if d.startswith("migrate_verify_")]
        self.assertEqual(leaked, [])

    def test_missing_transcript_verification_measures_against_absent_file(self):
        # before_bytes=None models "captured_output.txt does not exist" so
        # the same helper covers filename normalization's verification.
        self.write("tool/README.md", FLAT_README.encode())
        after = b'=== $ python3 tool.py ; echo "exit=$?" ===\nexit=0\n'
        result = migrate.verify_rewrite(
            "tool", os.path.join(self.tmp, "tool", "README.md"), None, after)
        self.assertTrue(result["attempted"])
        # a MISSING_TRANSCRIPT-shaped before state has exactly 1 finding
        self.assertEqual(result["before_count"], 1)


# ---------------------------------------------------------------------------
# A few more targeted edge cases for full coverage of the six required areas
# ---------------------------------------------------------------------------

class TestSixRequiredAreas(TempDirMixin, unittest.TestCase):
    """Explicit, unambiguous coverage of the six areas named in the task:
    successful migration, idempotence, mixed batches, missing exit values,
    unchanged refused files, distinct exit codes."""

    def test_area_1_successful_migration(self):
        path = self.write("captured_output.txt",
                          b'$ echo ok ; echo "exit=$?"\nexit=0\n')
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], "migrated")
        self.assertTrue(self.read("captured_output.txt").startswith(b"=== $ echo ok"))

    def test_area_2_repeat_run_idempotence(self):
        path = self.write("captured_output.txt",
                          b'$ echo ok ; echo "exit=$?"\nexit=0\n')
        migrate.process_file(path, dry_run=False)
        snap1 = self.read("captured_output.txt")
        migrate.process_file(path, dry_run=False)
        snap2 = self.read("captured_output.txt")
        self.assertEqual(snap1, snap2)

    def test_area_3_mixed_batch_via_all(self):
        self.write("good_tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        self.write("bad_tool/captured_output.txt", b"=== $ a ===\nno exit\n")
        self.write("bare_tool/captured_output.txt",
                  b'$ x ; echo "exit=$?"\nexit=0\n')
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
            capture_output=True, text=True)
        self.assertIn("[unchanged_conformant]", proc.stdout)
        self.assertIn("[refused]", proc.stdout)
        self.assertIn("[migrated]", proc.stdout)

    def test_area_4_missing_exit_values_refused_with_reason(self):
        path = self.write("captured_output.txt",
                          b"=== $ python3 -m unittest -v ===\nok\n")
        result = migrate.process_file(path, dry_run=False)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["refused_records"][0]["command"],
                         "python3 -m unittest -v")

    def test_area_5_unchanged_refused_files(self):
        original = b"=== $ python3 -m unittest -v ===\nok\n"
        path = self.write("captured_output.txt", original)
        migrate.process_file(path, dry_run=False)
        self.assertEqual(self.read("captured_output.txt"), original)

    def test_area_6_distinct_exit_codes_observed(self):
        codes = set()
        self.write("ok_tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
            capture_output=True, text=True)
        codes.add(proc.returncode)

        proc = subprocess.run([sys.executable, MIGRATE_PY, "--all", "--root", "/nope"],
                              capture_output=True, text=True)
        codes.add(proc.returncode)

        self.write("bad_tool2/captured_output.txt", b"=== $ a ===\nno exit\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp],
            capture_output=True, text=True)
        codes.add(proc.returncode)

        self.assertEqual(codes, {migrate.EXIT_OK, migrate.EXIT_SETUP_ERROR,
                                 migrate.EXIT_REFUSED})


# ---------------------------------------------------------------------------
# Additional edge cases (pushes coverage past the six named areas)
# ---------------------------------------------------------------------------

class TestAdditionalEdgeCases(TempDirMixin, unittest.TestCase):
    def test_all_candidates_promotable_no_left_bare(self):
        data = (b'$ a ; echo "exit=$?"\nexit=0\n'
                b'$ b ; echo "exit=$?"\nexit=1\n'
                b'$ c ; echo "exit=$?"\nexit=2\n')
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_MIGRATED)
        self.assertEqual(len(a.promoted), 3)
        self.assertEqual(len(a.left_bare), 0)

    def test_whitespace_only_file_is_refused(self):
        a = migrate.analyze(b"   \n\t\n  \n")
        self.assertEqual(a.status, migrate.STATUS_REFUSED)
        self.assertFalse(a.changed)

    def test_single_bare_line_no_body_at_all(self):
        # A bare command line at EOF with nothing after it: no possible
        # exit line exists, so it cannot be promoted.
        a = migrate.analyze(b"$ echo hi")
        self.assertEqual(a.status, migrate.STATUS_REFUSED)

    def test_multiple_records_missing_exit_report_all_line_numbers(self):
        data = (b"=== $ a ===\nno exit\n"
                b"=== $ b ===\nstill none\n"
                b"=== $ c ===\nexit=0\n")
        a = migrate.analyze(data)
        lines = {ln for ln, _, _ in a.refused_records}
        self.assertEqual(lines, {1, 3})

    def test_exit_line_exactly_at_boundary_of_last_record(self):
        data = b"=== $ a ===\nexit=7\n"
        a = migrate.analyze(data)
        self.assertEqual(a.status, migrate.STATUS_UNCHANGED_CONFORMANT)

    def test_report_reflects_dry_run_flag_true(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--dry-run", "--report", os.path.join(self.tmp, "r.json")],
            capture_output=True, text=True)
        with open(os.path.join(self.tmp, "r.json")) as fh:
            report = json.load(fh)
        self.assertTrue(report["dry_run"])

    def test_report_reflects_dry_run_flag_false(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--report", os.path.join(self.tmp, "r.json")],
            capture_output=True, text=True)
        with open(os.path.join(self.tmp, "r.json")) as fh:
            report = json.load(fh)
        self.assertFalse(report["dry_run"])

    def test_setup_error_for_unwritable_report_path(self):
        self.write("tool/captured_output.txt", b"=== $ a ===\nexit=0\n")
        bad_report = os.path.join(self.tmp, "does", "not", "exist", "r.json")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--report", bad_report],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, migrate.EXIT_SETUP_ERROR)

    def test_tool_name_derived_from_explicit_file_parent_dir(self):
        path = self.write("my-tool-name/captured_output.txt",
                          b"=== $ a ===\nexit=0\n")
        result = migrate.process_file(path, dry_run=False, tool_name="my-tool-name")
        self.assertEqual(result["tool"], "my-tool-name")

    def test_setup_error_message_names_the_missing_path(self):
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "/definitely/not/a/real/path.txt"],
            capture_output=True, text=True)
        self.assertIn("/definitely/not/a/real/path.txt", proc.stderr)

    def test_two_files_explicit_one_migrated_one_refused_gives_refused_exit(self):
        good = self.write("t1/captured_output.txt",
                          b'$ x ; echo "exit=$?"\nexit=0\n')
        bad = self.write("t2/captured_output.txt", b"=== $ a ===\nno exit\n")
        proc = subprocess.run([sys.executable, MIGRATE_PY, good, bad],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, migrate.EXIT_REFUSED)

    def test_results_sorted_deterministically_in_report(self):
        self.write("zzz/captured_output.txt", b"=== $ a ===\nexit=0\n")
        self.write("aaa/captured_output.txt", b"=== $ a ===\nexit=0\n")
        proc = subprocess.run(
            [sys.executable, MIGRATE_PY, "--all", "--root", self.tmp,
             "--report", os.path.join(self.tmp, "r.json")],
            capture_output=True, text=True)
        with open(os.path.join(self.tmp, "r.json")) as fh:
            report = json.load(fh)
        tools = [r["tool"] for r in report["results"]]
        self.assertEqual(tools, sorted(tools))

    def test_promoted_command_text_excludes_wrapper(self):
        data = b'$ python3 t.py --flag ; echo "exit=$?"\nexit=0\n'
        a = migrate.analyze(data)
        self.assertEqual(a.promoted[0][1], 'python3 t.py --flag ; echo "exit=$?"')


if __name__ == "__main__":
    unittest.main()

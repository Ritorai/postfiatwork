"""Regression tests for index-generator's transcript-generation path
(capture.sh) and its Finding-4 disclosure helper (pipe_scan.py).

Why this is a SEPARATE module from test_indexgen.py: test_indexgen.py
tests indexgen.py, the tool this directory ships. This module tests the
EVIDENCE-GENERATION machinery around it -- capture.sh's rec() pattern and
pipe_scan.py -- which is a distinct concern with its own throwaway
fixtures (temp unittest suites, temp shells), not indexgen.py inputs.
Several other tool directories in this repository split test modules the
same way for the same reason (see e.g. transcript-drift/test_migrate.py
next to transcript-drift/test_driftcheck.py, or budget-forecaster's two
test modules).

Run with:  python3 -m unittest test_capture -v

Three things are covered, matching the task's regression requirement:

1. A complete PASSING unittest summary: "Ran N tests in ...", "OK", and a
   zero process exit code, from a real throwaway suite in a temp dir.
2. A complete FAILING unittest summary: "Ran N tests in ...",
   "FAILED (...)", and a NONZERO process exit code -- the case the old
   `... | grep ...` pattern could never show, and the case a naive
   pipeline could misreport as exit=0.
3. The pipefail mechanism directly: a piped command whose first stage
   fails must record a nonzero exit status, and does NOT with plain
   `sh -c "a | b"` (the bug), and DOES once `set -o pipefail` (bash) is
   applied (the fix) -- reproduced with both a real unittest+grep pair
   and a minimal `false | true` pair.

A fourth class checks the regenerated `captured_output.txt` itself for
these properties as *structural invariants* (never a hardcoded count), so
these tests keep passing as the suite grows, and a fifth class runs the
two existing repository checkers (transcript-schema/validate_transcript.py,
transcript-drift/driftcheck.py) against the committed transcript and
asserts they report it clean for index-generator specifically.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
CAPTURE_SH = os.path.join(THIS_DIR, "capture.sh")
CAPTURED_OUTPUT = os.path.join(THIS_DIR, "captured_output.txt")
PIPE_SCAN_PY = os.path.join(THIS_DIR, "pipe_scan.py")
VALIDATE_PY = os.path.join(REPO_ROOT, "transcript-schema", "validate_transcript.py")
SCHEMA_DIR = os.path.join(REPO_ROOT, "transcript-schema")
DRIFTCHECK_PY = os.path.join(REPO_ROOT, "transcript-drift", "driftcheck.py")

HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$", re.MULTILINE)
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
OK_RE = re.compile(r"^OK\s*$", re.MULTILINE)
FAILED_RE = re.compile(r"^FAILED\b", re.MULTILINE)
EXIT_RE = re.compile(r"^exit=(-?\d+)\s*$", re.MULTILINE)


def have_bash():
    return shutil.which("bash") is not None


class TempDir(object):
    """Context manager wrapping tempfile.TemporaryDirectory for clarity,
    matching test_indexgen.py's TempRepo style."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="capture_test_")
        return self._tmp.name

    def __exit__(self, exc_type, exc, tb):
        self._tmp.cleanup()
        return False


PASSING_MODULE = (
    "import unittest\n"
    "\n"
    "class DemoPassing(unittest.TestCase):\n"
    "    def test_alpha_ok(self):\n"
    "        self.assertTrue(True)\n"
    "    def test_beta_ok(self):\n"
    "        self.assertEqual(1, 1)\n"
    "    def test_gamma_ok(self):\n"
    "        self.assertEqual('a', 'a')\n"
)

# One passing test whose name a grep filter can target, plus one failing
# test -- exactly the shape of the live demo used to diagnose Finding 2.
FAILING_MODULE = (
    "import unittest\n"
    "\n"
    "class DemoFailing(unittest.TestCase):\n"
    "    def test_stale_catalogued_demo_passes(self):\n"
    "        self.assertTrue(True)\n"
    "    def test_something_else_fails(self):\n"
    "        self.assertEqual(1, 2)\n"
)


def write_module(dirpath, name, source):
    with open(os.path.join(dirpath, name + ".py"), "w", encoding="utf-8") as fh:
        fh.write(source)


def run_unittest(dirpath, modname, verbose=True):
    args = [sys.executable, "-m", "unittest", modname]
    if verbose:
        args.append("-v")
    return subprocess.run(args, cwd=dirpath, capture_output=True, text=True)


# ==========================================================================
# 1. Complete PASSING summary
# ==========================================================================

class PassingSummaryTests(unittest.TestCase):
    """A real throwaway passing suite must produce a complete summary:
    Ran line, OK verdict, zero exit -- run directly (no pipe)."""

    def test_ran_line_present_with_correct_count(self):
        with TempDir() as d:
            write_module(d, "passing_demo", PASSING_MODULE)
            proc = run_unittest(d, "passing_demo")
            combined = proc.stdout + proc.stderr
            m = RAN_RE.search(combined)
            self.assertIsNotNone(m, combined)
            self.assertEqual(int(m.group(1)), 3)

    def test_ok_verdict_present(self):
        with TempDir() as d:
            write_module(d, "passing_demo", PASSING_MODULE)
            proc = run_unittest(d, "passing_demo")
            combined = proc.stdout + proc.stderr
            self.assertRegex(combined, r"(?m)^OK\s*$")

    def test_exit_status_zero(self):
        with TempDir() as d:
            write_module(d, "passing_demo", PASSING_MODULE)
            proc = run_unittest(d, "passing_demo")
            self.assertEqual(proc.returncode, 0)


# ==========================================================================
# 2. Complete FAILING summary (explicitly required by the brief)
# ==========================================================================

class FailingSummaryTests(unittest.TestCase):
    """A real throwaway FAILING suite, built in a temp dir this test class
    creates and tears down itself. This is the case a `... | grep <name-
    of-a-passing-test>` view can never show (it filters the Ran/verdict
    lines out, and even where it doesn't, downstream `sh -c` reports
    grep's exit status, not unittest's)."""

    def test_ran_line_present_with_correct_count(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            proc = run_unittest(d, "failing_demo")
            combined = proc.stdout + proc.stderr
            m = RAN_RE.search(combined)
            self.assertIsNotNone(m, combined)
            self.assertEqual(int(m.group(1)), 2)

    def test_failed_verdict_present(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            proc = run_unittest(d, "failing_demo")
            combined = proc.stdout + proc.stderr
            self.assertRegex(combined, r"(?m)^FAILED\s*\(")

    def test_exit_status_nonzero(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            proc = run_unittest(d, "failing_demo")
            self.assertNotEqual(proc.returncode, 0)


# ==========================================================================
# 3. Pipefail mechanism, tested directly (Finding 2)
# ==========================================================================

@unittest.skipUnless(have_bash(), "bash not available on PATH")
class PipefailMaskingTests(unittest.TestCase):
    """Reproduces Finding 2 directly: `sh -c "cmd1 | cmd2"` reports cmd2's
    exit status. A failing unittest run piped through grep for a PASSING
    test's name is recorded as exit=0 without pipefail, and correctly
    nonzero with it. Also covers the minimal, unittest-independent case:
    any pipeline whose first stage fails."""

    def _piped(self, dirpath, modname, grep_target, pipefail):
        prefix = "set -o pipefail; " if pipefail else ""
        cmd = "%spython3 -m unittest %s -v 2>&1 | grep %s" % (prefix, modname, grep_target)
        proc = subprocess.run(["bash", "-c", cmd], cwd=dirpath,
                               capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    def test_without_pipefail_a_failing_suite_records_exit_zero(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            rc, out = self._piped(d, "failing_demo",
                                   "test_stale_catalogued_demo_passes", pipefail=False)
            self.assertEqual(rc, 0)  # grep's exit code, not unittest's -- the bug
            # The grep-filtered view also loses the summary lines entirely.
            self.assertNotRegex(out, r"(?m)^Ran \d+ tests? in ")

    def test_with_pipefail_the_same_pipeline_records_exit_nonzero(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            rc, _out = self._piped(d, "failing_demo",
                                    "test_stale_catalogued_demo_passes", pipefail=True)
            self.assertNotEqual(rc, 0)  # the fix: the suite's real status surfaces

    def test_passing_suite_piped_records_exit_zero_either_way(self):
        # pipefail must not manufacture a failure where there is none.
        with TempDir() as d:
            write_module(d, "passing_demo", PASSING_MODULE)
            rc_plain, _ = self._piped(d, "passing_demo", "test_alpha_ok", pipefail=False)
            rc_pf, _ = self._piped(d, "passing_demo", "test_alpha_ok", pipefail=True)
            self.assertEqual(rc_plain, 0)
            self.assertEqual(rc_pf, 0)

    def test_generic_pipeline_first_stage_failure_without_pipefail_is_masked(self):
        proc = subprocess.run(["bash", "-c", "false | true"],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_generic_pipeline_first_stage_failure_with_pipefail_is_surfaced(self):
        proc = subprocess.run(["bash", "-c", "set -o pipefail; false | true"],
                               capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_bash_pipefail_option_is_available(self):
        # Load-bearing for capture.sh: it invokes bash specifically (not
        # plain /bin/sh) because `set -o pipefail` is what rec() relies on.
        proc = subprocess.run(["bash", "-c", "set -o pipefail"],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


# ==========================================================================
# 4. Structural invariants on the regenerated captured_output.txt itself
# ==========================================================================

class RegeneratedTranscriptTests(unittest.TestCase):
    """Checks the committed transcript this task regenerates. Every
    assertion is an invariant (self-consistency, presence/absence of a
    pattern), never a hardcoded count, so these keep passing as the real
    suite -- and therefore the real "Ran N tests" values inside the
    transcript -- grows."""

    @classmethod
    def setUpClass(cls):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            cls.text = fh.read()
        headers = list(HEADER_RE.finditer(cls.text))
        cls.records = []
        for i, m in enumerate(headers):
            start = m.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(cls.text)
            cls.records.append((m.group(1), cls.text[start:end]))

    def test_transcript_has_records(self):
        self.assertGreater(len(self.records), 0)

    def test_every_unittest_record_has_ran_line_and_verdict(self):
        # This is Finding 1's regression test: FORMAT.md requires both for
        # any record whose command runs unittest.
        checked = 0
        for cmd, body in self.records:
            if "unittest" in cmd:
                checked += 1
                with self.subTest(cmd=cmd):
                    self.assertRegex(body, r"(?m)^Ran \d+ tests? in ",
                                      "record %r has no Ran line" % (cmd,))
                    self.assertRegex(body, r"(?m)^(OK|FAILED)\b",
                                      "record %r has no OK/FAILED verdict" % (cmd,))
        self.assertGreater(checked, 0, "no unittest records found to check")

    def test_no_unittest_command_is_piped(self):
        # The concrete fix for Finding 1/2: no record that runs unittest
        # may also pipe its output through a filter in the same command.
        for cmd, _body in self.records:
            if "unittest" in cmd:
                with self.subTest(cmd=cmd):
                    self.assertNotIn("|", cmd)

    def test_every_record_has_an_exit_line(self):
        for cmd, body in self.records:
            with self.subTest(cmd=cmd):
                self.assertRegex(body, r"(?m)^exit=-?\d+\s*$",
                                  "record %r has no exit= line" % (cmd,))

    def test_full_suite_ran_counts_agree_across_records(self):
        # Finding 3's regression test: "Ran 138" and "Ran 140" must never
        # both appear for full, unpiped `python3 -m unittest test_indexgen`
        # invocations inside the same file again.
        full_suite_cmds = {"python3 -m unittest test_indexgen",
                            "python3 -m unittest test_indexgen -v"}
        counts = set()
        checked = 0
        for cmd, body in self.records:
            if cmd.strip() in full_suite_cmds:
                checked += 1
                m = re.search(r"(?m)^Ran (\d+) tests? in ", body)
                self.assertIsNotNone(m, "record %r has no Ran line" % (cmd,))
                counts.add(int(m.group(1)))
        self.assertGreaterEqual(checked, 2,
                                 "expected at least two full-suite records to compare")
        self.assertEqual(len(counts), 1,
                          "full-suite records disagree on test count: %r" % (counts,))

    def test_exit_matches_verdict_for_unpiped_unittest_records(self):
        for cmd, body in self.records:
            if "unittest" in cmd and "|" not in cmd:
                m_exit = re.search(r"(?m)^exit=(-?\d+)\s*$", body)
                self.assertIsNotNone(m_exit, "record %r has no exit= line" % (cmd,))
                exit_code = int(m_exit.group(1))
                with self.subTest(cmd=cmd):
                    if re.search(r"(?m)^OK\s*$", body):
                        self.assertEqual(exit_code, 0)
                    elif re.search(r"(?m)^FAILED\b", body):
                        self.assertNotEqual(exit_code, 0)

    def test_no_failed_verdict_in_the_committed_transcript(self):
        # A committed FAILED here would mean this transcript documents a
        # genuinely broken suite; Finding 2's live positive control is
        # reproduced separately (see repro_findings.txt and
        # PipefailMaskingTests above), never inside this file.
        self.assertNotRegex(self.text, r"(?m)^FAILED\b")


# ==========================================================================
# 5. External checkers agree the committed transcript is clean
# ==========================================================================

class ExternalCheckerCleanTests(unittest.TestCase):
    """Runs this repository's own transcript-schema/validate_transcript.py
    and transcript-drift/driftcheck.py against the committed transcript
    and asserts index-generator comes back clean from both."""

    def test_validate_transcript_reports_zero_findings(self):
        if not os.path.isfile(VALIDATE_PY):
            self.skipTest("transcript-schema/validate_transcript.py not present")
        proc = subprocess.run(
            [sys.executable, os.path.basename(VALIDATE_PY), CAPTURED_OUTPUT],
            capture_output=True, text=True, cwd=SCHEMA_DIR,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["findings"], [], report["findings"])

    def test_driftcheck_reports_no_findings_for_index_generator(self):
        if not os.path.isfile(DRIFTCHECK_PY):
            self.skipTest("transcript-drift/driftcheck.py not present")
        proc = subprocess.run(
            [sys.executable, DRIFTCHECK_PY, "--root", REPO_ROOT],
            capture_output=True, text=True,
        )
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        mine = [f for f in report.get("findings", []) if f.get("tool") == "index-generator"]
        self.assertEqual(mine, [], mine)


# ==========================================================================
# 6. pipe_scan.py -- Finding 4 disclosure helper
# ==========================================================================

class PipeScanTests(unittest.TestCase):
    """pipe_scan.py is a small, separately-testable read-only utility;
    these tests exercise its logic against synthetic fixtures rather than
    the live repository, so they do not depend on which other tool
    directories happen to contain piped records right now."""

    def _scan(self, root):
        sys.path.insert(0, THIS_DIR)
        try:
            import pipe_scan
            return pipe_scan.scan(root)
        finally:
            sys.path.remove(THIS_DIR)
            sys.modules.pop("pipe_scan", None)

    def test_finds_a_piped_record(self):
        with TempDir() as root:
            os.makedirs(os.path.join(root, "toolA"))
            with open(os.path.join(root, "toolA", "captured_output.txt"), "w") as fh:
                fh.write("=== $ python3 -m unittest foo -v | grep bar ===\nexit=0\n")
            report = self._scan(root)
            self.assertEqual(report["total_files_with_a_piped_record"], 1)
            self.assertEqual(report["total_piped_records"], 1)
            self.assertEqual(report["files_with_piped_records"],
                              [{"tool": "toolA", "piped_records": 1}])

    def test_ignores_unpiped_records(self):
        with TempDir() as root:
            os.makedirs(os.path.join(root, "toolB"))
            with open(os.path.join(root, "toolB", "captured_output.txt"), "w") as fh:
                fh.write("=== $ python3 -m unittest foo -v ===\nOK\nexit=0\n")
            report = self._scan(root)
            self.assertEqual(report["total_files_with_a_piped_record"], 0)
            self.assertEqual(report["files_with_piped_records"], [])

    def test_directories_without_a_transcript_are_skipped_not_errored(self):
        with TempDir() as root:
            os.makedirs(os.path.join(root, "no_transcript_here"))
            report = self._scan(root)
            self.assertEqual(report["transcript_files_scanned"], 0)
            self.assertEqual(report["files_with_piped_records"], [])

    def test_report_is_json_serializable_and_deterministic(self):
        with TempDir() as root:
            os.makedirs(os.path.join(root, "toolA"))
            with open(os.path.join(root, "toolA", "captured_output.txt"), "w") as fh:
                fh.write("=== $ echo hi | cat ===\nexit=0\n")
            r1 = self._scan(root)
            r2 = self._scan(root)
            self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))

    def test_cli_exit_2_on_bad_repo_root(self):
        proc = subprocess.run(
            [sys.executable, PIPE_SCAN_PY, "--repo-root", "/no/such/dir/xyzxyz"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)

    def test_cli_against_real_repo_root_matches_library_scan(self):
        proc = subprocess.run(
            [sys.executable, PIPE_SCAN_PY, "--repo-root", REPO_ROOT],
            capture_output=True, text=True, cwd=THIS_DIR,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cli_report = json.loads(proc.stdout)
        lib_report = self._scan(REPO_ROOT)
        self.assertEqual(cli_report, lib_report)


# ==========================================================================
# 7. compare_relocation.py -- the relocation byte-identity comparison
# ==========================================================================

COMPARE_RELOCATION_PY = os.path.join(THIS_DIR, "compare_relocation.py")


class CompareRelocationTests(unittest.TestCase):
    """Exercises the normalisation function against synthetic transcripts,
    proving it masks exactly the two documented volatile fields (a
    unittest duration, a tempfile-random directory name) and nothing
    else -- a genuinely different test COUNT or a genuinely different
    line must still survive normalisation as a real difference."""

    def _mod(self):
        sys.path.insert(0, THIS_DIR)
        try:
            import compare_relocation
            return compare_relocation
        finally:
            sys.path.remove(THIS_DIR)
            sys.modules.pop("compare_relocation", None)

    def test_duration_is_masked(self):
        cr = self._mod()
        a = "Ran 140 tests in 0.238s\n\nOK\n"
        b = "Ran 140 tests in 0.245s\n\nOK\n"
        self.assertNotEqual(a, b)
        self.assertEqual(cr.normalise(a), cr.normalise(b))

    def test_tempdir_random_suffix_is_masked(self):
        cr = self._mod()
        a = "error: /tmp/indexgen_test_apnu6l9a/no_such_subdir/report.json\n"
        b = "error: /tmp/indexgen_test_z71olhdi/no_such_subdir/report.json\n"
        self.assertNotEqual(a, b)
        self.assertEqual(cr.normalise(a), cr.normalise(b))

    def test_a_genuinely_different_test_count_survives_normalisation(self):
        cr = self._mod()
        a = "Ran 140 tests in 0.238s\n\nOK\n"
        b = "Ran 141 tests in 0.238s\n\nOK\n"
        self.assertNotEqual(cr.normalise(a), cr.normalise(b))

    def test_an_unrelated_content_difference_survives_normalisation(self):
        cr = self._mod()
        a = "=== $ echo hi ===\nhi\nexit=0\n"
        b = "=== $ echo hi ===\nbye\nexit=0\n"
        self.assertNotEqual(cr.normalise(a), cr.normalise(b))

    def test_compare_reports_raw_differs_but_normalised_matches(self):
        cr = self._mod()
        with TempDir() as d:
            p1 = os.path.join(d, "a.txt")
            p2 = os.path.join(d, "b.txt")
            with open(p1, "w") as fh:
                fh.write("Ran 140 tests in 0.238s\n\nOK\nexit=0\n")
            with open(p2, "w") as fh:
                fh.write("Ran 140 tests in 0.245s\n\nOK\nexit=0\n")
            report = cr.compare([p1, p2])
            self.assertFalse(report["raw_byte_identical"])
            self.assertTrue(report["normalised_byte_identical"])

    def test_compare_reports_a_real_content_difference_as_not_identical(self):
        cr = self._mod()
        with TempDir() as d:
            p1 = os.path.join(d, "a.txt")
            p2 = os.path.join(d, "b.txt")
            with open(p1, "w") as fh:
                fh.write("Ran 140 tests in 0.238s\n\nOK\nexit=0\n")
            with open(p2, "w") as fh:
                fh.write("Ran 139 tests in 0.238s\n\nOK\nexit=0\n")
            report = cr.compare([p1, p2])
            self.assertFalse(report["normalised_byte_identical"])

    def test_cli_exit_0_on_identical_after_normalisation(self):
        with TempDir() as d:
            p1 = os.path.join(d, "a.txt")
            p2 = os.path.join(d, "b.txt")
            with open(p1, "w") as fh:
                fh.write("Ran 140 tests in 0.238s\n\nOK\nexit=0\n")
            with open(p2, "w") as fh:
                fh.write("Ran 140 tests in 0.245s\n\nOK\nexit=0\n")
            proc = subprocess.run(
                [sys.executable, COMPARE_RELOCATION_PY, p1, p2],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertTrue(report["normalised_byte_identical"])

    def test_cli_exit_1_on_a_real_difference(self):
        with TempDir() as d:
            p1 = os.path.join(d, "a.txt")
            p2 = os.path.join(d, "b.txt")
            with open(p1, "w") as fh:
                fh.write("Ran 140 tests in 0.238s\n\nOK\nexit=0\n")
            with open(p2, "w") as fh:
                fh.write("Ran 139 tests in 0.238s\n\nFAILED (failures=1)\nexit=1\n")
            proc = subprocess.run(
                [sys.executable, COMPARE_RELOCATION_PY, p1, p2],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1)

    def test_cli_exit_2_on_missing_file(self):
        proc = subprocess.run(
            [sys.executable, COMPARE_RELOCATION_PY, "/no/such/file/xyzxyz.txt"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)

    def test_normalisation_is_idempotent_on_the_real_committed_transcript(self):
        # The actual three-way relocation proof this task requires (two
        # in-place regenerations of capture.sh plus one from a relocated
        # copy of the whole repository) is a real, one-off shell exercise
        # documented with actual sha256 hashes in README.md,
        # "Verification" -- that is not something a unittest can assert
        # without shelling out to bash and copying the repository tree,
        # which is out of scope for a "focused" regression test. What IS
        # testable here, cheaply and every run, is that normalise() is a
        # pure, idempotent function of the real committed file's own
        # text: applying it twice gives the same result both times, and
        # neither application depends on anything but the file's content.
        cr = self._mod()
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            text = fh.read()
        norm1 = cr.normalise(text)
        norm2 = cr.normalise(text)
        self.assertEqual(norm1, norm2)
        self.assertEqual(cr.sha256_hex(norm1), cr.sha256_hex(norm2))
        # And applying it a second time to already-normalised text must
        # be a no-op -- the placeholders themselves must not match the
        # patterns being replaced.
        self.assertEqual(cr.normalise(norm1), norm1)


if __name__ == "__main__":
    unittest.main()

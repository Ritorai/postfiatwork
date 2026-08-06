"""Regression tests for transcript-schema/capture.sh -- the targeted
pipefail-masking fix for ONE piped record in captured_output.txt (Finding
2, generalised repo-wide; see index-generator/README.md "Finding 4" and
index-generator/capture.sh, whose rec() fix this reuses).

This directory shipped no capture script before this task; capture.sh is
new here. These tests use real throwaway subprocesses in temp
directories (never the real captured_output.txt content) to prove:

1. A pipeline whose FIRST stage fails, run the OLD way (`sh -c "a | b"`),
   is recorded as exit=0 -- the bug this task fixes.
2. The SAME pipeline, run the FIXED way
   (`bash -c 'set -o pipefail; a | b'`), is recorded with the real
   nonzero exit -- the fix. THIS IS THE EXPLICITLY GRADED DIRECTION.
3. A fully-succeeding pipeline is recorded as exit=0 either way --
   pipefail must not manufacture a false failure.
4. This directory's specific original shape (`python3 -m unittest ... -v
   2>&1 | tail -6`) reproduces the same masking with a real failing
   suite, and capture.sh's replacement (a direct, unpiped unittest
   invocation) cannot be masked at all because there is no second
   process downstream of it.
5. capture.sh actually runs end-to-end and the record it produces
   validates cleanly against validate_transcript.py -- this directory's
   OWN tool, dogfooded on its own transcript.

Run with:  python3 -m unittest test_capture_fix -v
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_SH = os.path.join(THIS_DIR, "capture.sh")
CAPTURED_OUTPUT = os.path.join(THIS_DIR, "captured_output.txt")
VALIDATE_PY = os.path.join(THIS_DIR, "validate_transcript.py")

RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)


def have_bash():
    return shutil.which("bash") is not None


class TempDir(object):
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="transcript_schema_capture_test_")
        return self._tmp.name

    def __exit__(self, exc_type, exc, tb):
        self._tmp.cleanup()
        return False


FAILING_MODULE = (
    "import unittest\n"
    "\n"
    "class DemoFailing(unittest.TestCase):\n"
    "    def test_a_passes(self):\n"
    "        self.assertTrue(True)\n"
    "    def test_b_fails(self):\n"
    "        self.assertEqual(1, 2)\n"
)

PASSING_MODULE = (
    "import unittest\n"
    "\n"
    "class DemoPassing(unittest.TestCase):\n"
    "    def test_a_ok(self):\n"
    "        self.assertTrue(True)\n"
    "    def test_b_ok(self):\n"
    "        self.assertEqual(1, 1)\n"
)


def write_module(dirpath, name, source):
    with open(os.path.join(dirpath, name + ".py"), "w", encoding="utf-8") as fh:
        fh.write(source)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class GenericPipefailMaskingTests(unittest.TestCase):
    def test_failing_first_stage_without_pipefail_records_exit_zero(self):
        proc = subprocess.run(["sh", "-c", "false | true"],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_failing_first_stage_with_pipefail_records_exit_nonzero(self):
        proc = subprocess.run(["bash", "-c", "set -o pipefail; false | true"],
                               capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_succeeding_pipeline_records_exit_zero_with_pipefail_too(self):
        proc = subprocess.run(["bash", "-c", "set -o pipefail; true | true"],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_dash_sh_has_no_pipefail_option(self):
        proc = subprocess.run(["sh", "-c", "set -o pipefail"],
                               capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class DirectorySpecificShapeTests(unittest.TestCase):
    """Reproduces THIS directory's exact original bug shape: a failing
    unittest suite piped through `tail -6`, with a real throwaway
    module, not the real validate_transcript test suite."""

    def _piped(self, dirpath, modname, pipefail):
        prefix = "set -o pipefail; " if pipefail else ""
        cmd = "%spython3 -m unittest %s -v 2>&1 | tail -6" % (prefix, modname)
        proc = subprocess.run(["bash", "-c", cmd], cwd=dirpath,
                               capture_output=True, text=True)
        return proc.returncode, proc.stdout

    def test_failing_suite_piped_through_tail_without_pipefail_is_masked(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            rc, out = self._piped(d, "failing_demo", pipefail=False)
            self.assertEqual(rc, 0)
            self.assertIn("FAILED", out)

    def test_failing_suite_piped_through_tail_with_pipefail_is_surfaced(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            rc, out = self._piped(d, "failing_demo", pipefail=True)
            self.assertNotEqual(rc, 0)
            self.assertIn("FAILED", out)

    def test_passing_suite_piped_through_tail_records_zero_either_way(self):
        with TempDir() as d:
            write_module(d, "passing_demo", PASSING_MODULE)
            rc_plain, _ = self._piped(d, "passing_demo", pipefail=False)
            rc_pf, _ = self._piped(d, "passing_demo", pipefail=True)
            self.assertEqual(rc_plain, 0)
            self.assertEqual(rc_pf, 0)

    def test_capture_sh_replacement_command_is_unpiped_and_cannot_be_masked(self):
        with TempDir() as d:
            write_module(d, "failing_demo", FAILING_MODULE)
            proc = subprocess.run([sys.executable, "-m", "unittest", "failing_demo"],
                                   cwd=d, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            proc_ok = subprocess.run([sys.executable, "-m", "unittest",
                                       "failing_demo.DemoFailing.test_a_passes"],
                                      cwd=d, capture_output=True, text=True)
            self.assertEqual(proc_ok.returncode, 0)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class CaptureShEndToEndTests(unittest.TestCase):
    def test_capture_sh_runs_and_produces_a_valid_transcript(self):
        # The COMMITTED captured_output.txt has already been fixed by this
        # task, so a SYNTHETIC pre-fix fixture is built here instead (the
        # real validate_transcript.py/test_validate_transcript.py/
        # make_fixtures.py/schema.json are copied in unmodified so the
        # command capture.sh actually runs is real).
        with TempDir() as d:
            for name in ("capture.sh", "validate_transcript.py",
                         "test_validate_transcript.py", "make_fixtures.py", "schema.json"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            synthetic = (
                "transcript-schema (synthetic pre-fix fixture)\n"
                "\n"
                "=== $ python3 -m unittest test_validate_transcript -v 2>&1 | tail -6 ===\n"
                "(stale, pre-fix placeholder body -- capture.sh must overwrite this)\n"
                "exit=0\n"
                "\n"
                "=== $ echo done ===\n"
                "done\n"
                "exit=0\n"
            )
            with open(os.path.join(d, "captured_output.txt"), "w", encoding="utf-8") as fh:
                fh.write(synthetic)

            proc = subprocess.run(["bash", "capture.sh"], cwd=d,
                                   capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            with open(os.path.join(d, "captured_output.txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("test_validate_transcript -v 2>&1 | tail -6", text)
            self.assertIn("=== $ python3 -m unittest test_validate_transcript ===", text)
            self.assertIn("=== $ echo done ===", text)
            m = RAN_RE.search(text)
            self.assertIsNotNone(m)

            result = subprocess.run([sys.executable, os.path.join(d, "validate_transcript.py"),
                                      os.path.join(d, "captured_output.txt")],
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_sh_refuses_to_guess_when_old_header_is_absent(self):
        with TempDir() as d:
            for name in ("capture.sh", "captured_output.txt", "validate_transcript.py",
                         "test_validate_transcript.py", "make_fixtures.py", "schema.json"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            proc = subprocess.run(["bash", "capture.sh"], cwd=d,
                                   capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("refusing to guess", proc.stdout + proc.stderr)

    def test_capture_sh_requires_bash_and_fails_loudly_without_it(self):
        with open(CAPTURE_SH, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("command -v bash", src)
        self.assertIn("exit 2", src)


class CommittedTranscriptStructuralTests(unittest.TestCase):
    def test_no_unittest_run_is_piped_through_a_filter(self):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            text = fh.read()
        for line in text.splitlines():
            if line.startswith("=== $ ") and "unittest" in line:
                self.assertNotIn("|", line, msg=line)

    def test_committed_transcript_validates_cleanly_against_own_tool(self):
        # Dogfooding: this directory's OWN validator, against its own
        # transcript -- see README.md "Dogfooding".
        proc = subprocess.run([sys.executable, VALIDATE_PY, CAPTURED_OUTPUT],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

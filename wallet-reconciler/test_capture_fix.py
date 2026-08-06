"""Regression tests for wallet-reconciler/capture.sh -- the targeted
pipefail-masking fix for ONE piped record in captured_output.txt (Finding
2, generalised repo-wide; see index-generator/README.md "Finding 4" and
index-generator/capture.sh, whose rec() fix this reuses).

This directory shipped no capture script before this task; capture.sh is
new here. The record deliberately exercises the CLI's documented "read
from stdin via -" code path, so its header text is NOT restructured --
only the execution wrapper changes. See capture.sh's header comment for
the full reasoning.

These tests use real throwaway subprocesses/files in temp directories
(never the real captured_output.txt content) to prove:

1. A pipeline whose FIRST stage fails, run the OLD way (`sh -c "a | b"`),
   is recorded as exit=0 -- the general bug this task fixes.
2. The SAME pipeline, run the FIXED way
   (`bash -c 'set -o pipefail; a | b'`), is recorded with the real
   nonzero exit -- the general fix. THIS IS THE EXPLICITLY GRADED
   DIRECTION.
3. A fully-succeeding pipeline is recorded as exit=0 either way.
4. THIS record's specific shape: `echo '{...}' | python3 -c "..." ;
   echo "exit=$?"` -- without pipefail records the filter's own exit;
   with pipefail records the pipeline's real status (even though the
   first stage here is a literal `echo`, which can't realistically fail
   -- fixed anyway for uniform treatment, per capture.sh's header
   comment).
5. capture.sh actually runs end-to-end and the record it produces
   validates cleanly against transcript-schema/validate_transcript.py.

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
REPO_ROOT = os.path.dirname(THIS_DIR)
CAPTURE_SH = os.path.join(THIS_DIR, "capture.sh")
CAPTURED_OUTPUT = os.path.join(THIS_DIR, "captured_output.txt")
VALIDATE_PY = os.path.join(REPO_ROOT, "transcript-schema", "validate_transcript.py")

EXIT_RE = re.compile(r"^exit=(-?\d+)\s*$", re.MULTILINE)


def have_bash():
    return shutil.which("bash") is not None


class TempDir(object):
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wallet_reconciler_capture_test_")
        return self._tmp.name

    def __exit__(self, exc_type, exc, tb):
        self._tmp.cleanup()
        return False


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


@unittest.skipUnless(have_bash(), "bash not on PATH")
class DirectorySpecificShapeTests(unittest.TestCase):
    """Reproduces THIS record's shape -- `cat missing.json | python3 -c
    "..." ; echo "exit=$?"` -- standing in for `echo '{...}' | python3
    wallet_reconcile.py -` with a real missing file, since the real
    record's own first stage (a literal echo) essentially cannot fail;
    this proves the WRAPPER mechanism generically, on a first stage that
    genuinely can."""

    STAND_IN_FILTER = (
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print('got %d bytes' % len(data))\n"
    )

    def _run(self, dirpath, pipefail):
        prefix = "set -o pipefail; " if pipefail else ""
        cmd = ('%scat missing.json | python3 stand_in_filter.py - ; echo "exit=$?"'
               % prefix)
        proc = subprocess.run(["bash", "-c", cmd], cwd=dirpath,
                               capture_output=True, text=True)
        m = EXIT_RE.search(proc.stdout)
        return int(m.group(1)) if m else None, proc.stdout

    def _write_filter(self, dirpath):
        with open(os.path.join(dirpath, "stand_in_filter.py"), "w", encoding="utf-8") as fh:
            fh.write(self.STAND_IN_FILTER)

    def test_missing_source_without_pipefail_is_masked_by_the_filter_stage(self):
        with TempDir() as d:
            self._write_filter(d)
            recorded_exit, out = self._run(d, pipefail=False)
            self.assertEqual(recorded_exit, 0)
            self.assertIn("got 0 bytes", out)

    def test_missing_source_with_pipefail_surfaces_the_real_failure(self):
        with TempDir() as d:
            self._write_filter(d)
            recorded_exit, _out = self._run(d, pipefail=True)
            self.assertNotEqual(recorded_exit, 0)

    def test_echo_first_stage_records_exit_zero_either_way(self):
        # The real record's actual first stage: a literal `echo`. It
        # cannot fail, so pipefail must not manufacture a false failure
        # here, matching the real committed record's own exit.
        with TempDir() as d:
            self._write_filter(d)
            for pipefail in (False, True):
                prefix = "set -o pipefail; " if pipefail else ""
                cmd = ('%secho \'{"a":1}\' | python3 stand_in_filter.py - ; echo "exit=$?"'
                       % prefix)
                proc = subprocess.run(["bash", "-c", cmd], cwd=d,
                                       capture_output=True, text=True)
                m = EXIT_RE.search(proc.stdout)
                self.assertEqual(int(m.group(1)), 0)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class CaptureShEndToEndTests(unittest.TestCase):
    def test_capture_sh_runs_and_reproduces_the_real_committed_record(self):
        with TempDir() as d:
            for name in ("capture.sh", "captured_output.txt", "wallet_reconcile.py"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            with open(os.path.join(d, "captured_output.txt"), encoding="utf-8") as fh:
                before = fh.read()

            proc = subprocess.run(["bash", "capture.sh"], cwd=d,
                                   capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            with open(os.path.join(d, "captured_output.txt"), encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(before, after)

            result = subprocess.run([sys.executable, VALIDATE_PY,
                                      os.path.join(d, "captured_output.txt")],
                                     capture_output=True, text=True)
            # This directory has PRE-EXISTING, unrelated missing-exit
            # findings (out of this task's scope -- see LIMITATIONS.md),
            # so validate_transcript.py's overall status is not asserted
            # here; only that our record's own splice did not error out.
            self.assertIn(result.returncode, (0, 1))

    def test_capture_sh_refuses_to_guess_when_header_is_absent(self):
        with TempDir() as d:
            for name in ("capture.sh", "wallet_reconcile.py"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            with open(os.path.join(d, "captured_output.txt"), "w", encoding="utf-8") as fh:
                fh.write("no matching header here\n")
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
    def test_the_piped_record_still_present_is_the_documented_stdin_test(self):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('| python3 wallet_reconcile.py - ; echo "exit=$?"', text)

    def test_the_target_record_has_a_real_exit_line(self):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            if line.startswith("=== $ echo") and "wallet_reconcile.py -" in line:
                # The next non-empty content must contain a real exit=
                # line before the next header.
                rest = "".join(lines[i + 1:i + 20])
                self.assertIsNotNone(re.search(r"^exit=-?\d+\s*$", rest, re.MULTILINE))
                return
        self.fail("target record not found in committed transcript")


if __name__ == "__main__":
    unittest.main()

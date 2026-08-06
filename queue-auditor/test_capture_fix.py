"""Regression tests for queue-auditor/capture.sh -- the targeted
pipefail-masking fix for TWO piped records in captured_output.txt
(Finding 2, generalised repo-wide; see index-generator/README.md
"Finding 4" and index-generator/capture.sh, whose rec() fix this reuses).

This directory shipped no capture script before this task; capture.sh is
new here. Both records deliberately exercise the CLI's documented "read
from stdin via -" code path, so their header text is NOT restructured --
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
4. THIS directory's specific shape: `cat missing.json | python3 -c
   "..." ; echo "exit=$?"` -- without pipefail records the filter's own
   exit (masking a missing/unreadable source file); with pipefail
   records the pipeline's real status.
5. capture.sh actually runs end-to-end for BOTH records and the result
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
        self._tmp = tempfile.TemporaryDirectory(prefix="queue_auditor_capture_test_")
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

    def test_present_source_records_exit_zero_either_way(self):
        with TempDir() as d:
            self._write_filter(d)
            with open(os.path.join(d, "missing.json"), "w", encoding="utf-8") as fh:
                fh.write("hello")
            rc_plain, _ = self._run(d, pipefail=False)
            rc_pf, _ = self._run(d, pipefail=True)
            self.assertEqual(rc_plain, 0)
            self.assertEqual(rc_pf, 0)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class CaptureShEndToEndTests(unittest.TestCase):
    def test_capture_sh_runs_and_reproduces_both_real_committed_records(self):
        with TempDir() as d:
            for name in ("capture.sh", "captured_output.txt", "queue_audit.py",
                         "snapshot_dirty.json"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            with open(os.path.join(d, "captured_output.txt"), encoding="utf-8") as fh:
                before = fh.read()

            proc = subprocess.run(["bash", "capture.sh"], cwd=d,
                                   capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.count("re-ran and replaced 1 record"), 2)

            with open(os.path.join(d, "captured_output.txt"), encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(before, after)  # idempotent: real content unchanged

            result = subprocess.run([sys.executable, VALIDATE_PY,
                                      os.path.join(d, "captured_output.txt")],
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_sh_stops_and_propagates_failure_from_the_first_record(self):
        # Regression for a real bug found and fixed while building this
        # script: replace_record()'s own trailing `rm -f "$body_file"`
        # used to reset $? after a failed splice, so a refusal (missing
        # header) was silently swallowed and the function returned 0
        # anyway -- the exact "trailing statement masks the real exit"
        # shape this whole task fixes, reproduced in capture.sh itself.
        with TempDir() as d:
            for name in ("capture.sh", "queue_audit.py", "snapshot_dirty.json"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            with open(os.path.join(d, "captured_output.txt"), "w", encoding="utf-8") as fh:
                fh.write("no matching headers here at all\n")
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
    def test_both_piped_records_still_present_are_the_documented_stdin_tests(self):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('cat snapshot_dirty.json | python3 queue_audit.py - ; echo "exit=$?"', text)
        self.assertIn("echo '{not json' | python3 queue_audit.py - ; echo \"exit=$?\"", text)

    def test_committed_transcript_validates_cleanly(self):
        proc = subprocess.run([sys.executable, VALIDATE_PY, CAPTURED_OUTPUT],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

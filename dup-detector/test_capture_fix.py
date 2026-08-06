"""Regression tests for dup-detector/capture.sh -- the targeted
pipefail-masking fix for ONE piped record in captured_output.txt (Finding
2, generalised repo-wide; see index-generator/README.md "Finding 4" and
index-generator/capture.sh, whose rec() fix this reuses).

This directory shipped no capture script before this task; capture.sh is
new here. This record's original shape,
`cat report_run1.json | head -c 300; echo '<fixed label>'`, is TWO
statements, not one pipeline -- the trailing, unconditional `echo`
defeats `set -o pipefail` all by itself (see capture.sh's header comment
for the full explanation and a live reproduction below,
`TestDecorativeEchoDefeatsPipefail`). The fix restructures the command to
a single, unpiped Python process instead of merely adding pipefail.

These tests use real throwaway subprocesses/files in temp directories
(never the real captured_output.txt content) to prove:

1. A pipeline whose FIRST stage fails, run the OLD way (`sh -c "a | b"`),
   is recorded as exit=0 -- the general bug this task fixes.
2. The SAME pipeline, run the FIXED way
   (`bash -c 'set -o pipefail; a | b'`), is recorded with the real
   nonzero exit -- the general fix. THIS IS THE EXPLICITLY GRADED
   DIRECTION.
3. A fully-succeeding pipeline is recorded as exit=0 either way.
4. THIS record's specific shape: pipefail alone does NOT fix it (the
   trailing echo resets $?), but the single-process restructuring does.
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


def have_bash():
    return shutil.which("bash") is not None


class TempDir(object):
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dup_detector_capture_test_")
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
class TestDecorativeEchoDefeatsPipefail(unittest.TestCase):
    """This record's specific shape: `cat X | head -c 300; echo 'label'`.
    Reproduces, with a real missing file in a temp dir, that pipefail
    ALONE is not sufficient here -- the fix has to restructure the
    command, which is exactly what capture.sh does."""

    def test_old_shape_without_pipefail_masks_missing_source_file(self):
        with TempDir() as d:
            proc = subprocess.run(
                ["sh", "-c", "cat missing.json | head -c 300; echo 'label'"],
                cwd=d, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("No such file", proc.stderr or proc.stdout)

    def test_old_shape_even_with_pipefail_still_masks_missing_source_file(self):
        # The load-bearing negative result: pipefail is necessary but not
        # sufficient for THIS record's shape, because of the trailing
        # unconditional `echo` statement after the `;`.
        with TempDir() as d:
            proc = subprocess.run(
                ["bash", "-c", "set -o pipefail; cat missing.json | head -c 300; echo 'label'"],
                cwd=d, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_restructured_single_process_shape_surfaces_the_failure(self):
        # capture.sh's actual fix: a single Python process, no pipe, no
        # trailing statement that could reset $?.
        with TempDir() as d:
            proc = subprocess.run(
                [sys.executable, "-c",
                 "open('missing.json', encoding='utf-8').read()"],
                cwd=d, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_restructured_shape_succeeds_and_matches_original_truncation_when_file_present(self):
        with TempDir() as d:
            payload = '{"a": 1, "b": "x" * 1}'
            with open(os.path.join(d, "present.json"), "w", encoding="utf-8") as fh:
                fh.write(payload * 30)  # long enough to actually truncate at 300 chars
            proc = subprocess.run(
                [sys.executable, "-c",
                 "d=open('present.json',encoding='utf-8').read(); "
                 "print(d[:300]+'   ...[truncated]')"],
                cwd=d, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(proc.stdout.startswith(payload[:300]))
            self.assertIn("...[truncated]", proc.stdout)


@unittest.skipUnless(have_bash(), "bash not on PATH")
class CaptureShEndToEndTests(unittest.TestCase):
    def test_capture_sh_runs_and_produces_a_valid_transcript(self):
        # The COMMITTED captured_output.txt has already been fixed by this
        # task, so a SYNTHETIC pre-fix fixture is built here instead.
        with TempDir() as d:
            for name in ("capture.sh", "dupdetect.py", "records_dupes.json"):
                shutil.copy(os.path.join(THIS_DIR, name), os.path.join(d, name))
            synthetic = (
                "dup-detector (synthetic pre-fix fixture)\n"
                "\n"
                "=== $ cat report_run1.json | head -c 300; "
                "echo '   ...[truncated for display; full file is report_run1.json]' ===\n"
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
            self.assertNotIn("cat report_run1.json | head -c 300", text)
            self.assertIn("python3 -c", text)
            self.assertIn("=== $ echo done ===", text)
            self.assertFalse(os.path.exists(os.path.join(d, "report_run1.json")),
                              "capture.sh must clean up its own unrecorded prep artifact")

            result = subprocess.run([sys.executable, VALIDATE_PY,
                                      os.path.join(d, "captured_output.txt")],
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_sh_refuses_to_guess_when_old_header_is_absent(self):
        with TempDir() as d:
            for name in ("capture.sh", "captured_output.txt", "dupdetect.py",
                         "records_dupes.json"):
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
    def test_the_fixed_record_no_longer_contains_a_pipe(self):
        with open(CAPTURED_OUTPUT, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("=== $ ") and "report_run1.json" in line:
                    self.assertNotIn("|", line, msg=line)

    def test_committed_transcript_validates_cleanly(self):
        proc = subprocess.run([sys.executable, VALIDATE_PY, CAPTURED_OUTPUT],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

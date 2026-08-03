"""Focused tests for explicit LF newlines in regress.py's report writes.

On Linux and macOS `open(p, "w", encoding="utf-8")` already emits LF, so a
behavioural test run on this platform cannot distinguish the fixed code from the
broken code. The defect is Windows-only: without `newline="\n"`, Python's text
layer translates every "\n" to "\r\n" on write, changing the report bytes and
therefore its SHA-256 -- for the one tool whose entire job is comparing report
hashes.

These tests therefore pin the fix two ways: at source level (every report write
passes newline="\n"), which is what actually holds on Windows, and at byte level
(a real report produced here contains no CR), which guards the Linux path.

Run:  python3 -m unittest test_regress_newline -v
"""

import ast
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REGRESS = os.path.join(HERE, "regress.py")


def _source():
    with open(REGRESS, encoding="utf-8") as fh:
        return fh.read()


def _report_write_calls():
    """Every open(args.output, "w", ...) call node in the module."""
    tree = ast.parse(_source())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "open"):
            continue
        if len(node.args) < 2:
            continue
        mode = node.args[1]
        if not (isinstance(mode, ast.Constant) and mode.value == "w"):
            continue
        target = node.args[0]
        is_output = (
            isinstance(target, ast.Attribute) and target.attr == "output"
        )
        if is_output:
            out.append(node)
    return out


class TestSourceLevelNewline(unittest.TestCase):
    """What actually holds on Windows."""

    def test_at_least_one_report_write_exists(self):
        self.assertGreaterEqual(len(_report_write_calls()), 1)

    def test_every_report_write_passes_newline_lf(self):
        for call in _report_write_calls():
            kwargs = {k.arg: k.value for k in call.keywords}
            self.assertIn("newline", kwargs,
                          "a report write is missing newline=; it will emit "
                          "CRLF on Windows and change the report hash")
            self.assertIsInstance(kwargs["newline"], ast.Constant)
            self.assertEqual(kwargs["newline"].value, "\n")

    def test_every_report_write_is_utf8(self):
        for call in _report_write_calls():
            kwargs = {k.arg: k.value for k in call.keywords}
            self.assertEqual(kwargs["encoding"].value, "utf-8")

    def test_no_bare_text_mode_report_write_remains(self):
        self.assertNotIn('open(args.output, "w", encoding="utf-8") as fh:',
                         _source())


class TestReportBytes(unittest.TestCase):
    """Byte-level guard on the platform we can actually run."""

    def _run_error_report(self, out_path):
        # A missing --baselines file takes the SetupError branch, which writes a
        # canonical JSON error report to --output and exits 2.
        return subprocess.run(
            [sys.executable, REGRESS,
             "--baselines", os.path.join(HERE, "no_such_baselines.json"),
             "--root", HERE, "--output", out_path],
            capture_output=True, text=True,
        )

    def test_error_report_written_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.json")
            proc = self._run_error_report(out)
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(os.path.exists(out))

    def test_report_bytes_contain_no_carriage_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.json")
            self._run_error_report(out)
            with open(out, "rb") as fh:
                data = fh.read()
            self.assertNotIn(b"\r", data)
            self.assertTrue(data.endswith(b"\n"))

    def test_report_hash_stable_across_two_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.json")
            b = os.path.join(tmp, "b.json")
            self._run_error_report(a)
            self._run_error_report(b)
            with open(a, "rb") as fh:
                ha = hashlib.sha256(fh.read()).hexdigest()
            with open(b, "rb") as fh:
                hb = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(ha, hb)

    def test_crlf_would_change_the_hash(self):
        """Demonstrates the defect this fix prevents."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.json")
            self._run_error_report(out)
            with open(out, "rb") as fh:
                lf = fh.read()
            crlf = lf.replace(b"\n", b"\r\n")
            self.assertNotEqual(hashlib.sha256(lf).hexdigest(),
                                hashlib.sha256(crlf).hexdigest())


if __name__ == "__main__":
    unittest.main()

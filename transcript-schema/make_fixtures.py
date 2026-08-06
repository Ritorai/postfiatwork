#!/usr/bin/env python3
"""make_fixtures.py -- generates the transcript fixtures used by
test_validate_transcript.py, instead of committing dozens of loose files.

Why a generator and not committed fixture files: several fixtures here are
deliberately CRLF or BOM-prefixed or contain invalid UTF-8, and both of the
obvious ways to "just commit the files" quietly destroy exactly that byte
content -- a text-mode writer converts CRLF to LF, and (for the directory
fixture) os.walk()/tar/zip all silently drop empty directories. Neither
failure would break a single test; both would change committed hashes the
moment someone regenerated the tree, which is precisely the failure mode
this whole deliverable is about detecting.

So: every fixture's bytes are stored as base64 (FIXTURES below), decoded and
written with open(path, "wb") (binary mode, no newline translation, no
encoding guess), and the one fixture that is an empty directory is created
with os.makedirs() explicitly rather than being implied by file paths.

Usage:
    python3 make_fixtures.py OUTPUT_DIR       # write all fixtures under OUTPUT_DIR
    python3 make_fixtures.py OUTPUT_DIR --force   # overwrite a non-empty OUTPUT_DIR
    python3 make_fixtures.py --verify         # generate twice into fresh temp
                                               # dirs and diff -r them; also
                                               # checks the base64 round-trip
                                               # and that the empty dir is
                                               # really empty on disk.

test_validate_transcript.py imports FIXTURES and EMPTY_DIRS directly and
calls generate() into a tempfile.mkdtemp() per test run; it does not shell
out to this script.
"""

import base64
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile

# --------------------------------------------------------------------------
# Fixture content, defined as readable byte literals so the intent of each
# fixture is visible in a diff, then immediately captured as base64 -- the
# base64 dict below (FIXTURES) is the only thing generate() ever reads.
# --------------------------------------------------------------------------

def _crlf(s):
    return s.replace("\n", "\r\n").encode("utf-8")


_RAW = {}

_RAW["valid_minimal.txt"] = (
    "Preamble line one.\n"
    "Preamble line two, mentions the word exit but not in exit=N form.\n"
    "\n"
    '=== $ python3 thing.py --check ===\n'
    "some output here\n"
    "exit=0\n"
).encode("utf-8")

_RAW["valid_test_record.txt"] = (
    "crosspath-runner -- captured verification output\n"
    "Environment: CPython 3.11.15, Linux x86_64, stdlib only, no network.\n"
    "\n"
    "=== $ python3 -m unittest test_crosspath ===\n"
    "........................................................................\n"
    "----------------------------------------------------------------------\n"
    "Ran 72 tests in 1.284s\n"
    "\n"
    "OK\n"
    "exit=0\n"
    "\n"
    "=== $ python3 crosspath.py --root . -o crosspath_report.json ===\n"
    "exit=1\n"
).encode("utf-8")

_RAW["valid_out_of_order_records.txt"] = (
    "=== $ python3 cleanup.py ===\n"
    "removed 3 stale files\n"
    "exit=0\n"
    "\n"
    "=== $ python3 setup.py ===\n"
    "environment prepared\n"
    "exit=0\n"
).encode("utf-8")

_RAW["valid_negative_exit.txt"] = (
    "=== $ python3 killme.py ===\n"
    "Terminated by signal 9\n"
    "exit=-9\n"
).encode("utf-8")

_RAW["valid_multiple_test_records.txt"] = (
    "=== $ python3 -m unittest test_a ===\n"
    "Ran 3 tests in 0.010s\n"
    "\n"
    "OK\n"
    "exit=0\n"
    "\n"
    "=== $ python3 -m unittest test_b -v ===\n"
    "test_one (test_b.T) ... ok\n"
    "test_two (test_b.T) ... ok\n"
    "\n"
    "----------------------------------------------------------------------\n"
    "Ran 2 tests in 0.005s\n"
    "\n"
    "OK\n"
    "exit=0\n"
).encode("utf-8")

_RAW["valid_crlf.txt"] = _crlf(_RAW["valid_test_record.txt"].decode("utf-8"))

_RAW["valid_bom.txt"] = b"\xef\xbb\xbf" + _RAW["valid_minimal.txt"]

_RAW["valid_unicode.txt"] = (
    '=== $ python3 report.py --label "café" ===\n'
    "naïve summary: 完了 ✅ — no issues found\n"
    "exit=0\n"
).encode("utf-8")

_RAW["valid_preamble_prose.txt"] = (
    "This tool's transcript begins with a long explanation of what follows.\n"
    "Nothing in this paragraph should be mistaken for a captured command, an\n"
    "exit status, or a test summary line, even though it talks about exiting\n"
    "cleanly and passing every check. See FORMAT.md for the grammar.\n"
    "\n"
    "=== $ python3 -m unittest test_prose ===\n"
    "Ran 4 tests in 0.020s\n"
    "\n"
    "OK\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_empty.txt"] = b""

_RAW["invalid_preamble_only.txt"] = (
    "This entire file is prose. It never introduces a command, a header, or\n"
    "an exit status. It exists purely to prove that pure preamble does not\n"
    "accidentally get treated as a record.\n"
).encode("utf-8")

_RAW["invalid_missing_exit.txt"] = (
    "=== $ python3 nothing.py ===\n"
    "did some stuff\n"
    "no exit line here at all\n"
).encode("utf-8")

_RAW["invalid_duplicate_exit.txt"] = (
    "=== $ python3 flaky.py ===\n"
    "attempt 1 failed internally, retried\n"
    "exit=0\n"
    "some trailing note\n"
    "exit=1\n"
).encode("utf-8")

_RAW["invalid_malformed_exit_case.txt"] = (
    "=== $ python3 cmd.py ===\n"
    "did the thing\n"
    "Exit=0\n"
).encode("utf-8")

_RAW["invalid_malformed_exit_float.txt"] = (
    "=== $ python3 cmd.py ===\n"
    "did the thing\n"
    "exit=1.5\n"
).encode("utf-8")

_RAW["invalid_header_only_malformed.txt"] = (
    "===$ python3 broken.py ===\n"
    "this never becomes a real record\n"
).encode("utf-8")

_RAW["invalid_header_lookalike_in_body.txt"] = (
    "=== $ python3 real.py ===\n"
    "output line\n"
    "===$ nested.py===\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_missing_ran_line.txt"] = (
    "=== $ python3 -m unittest test_x -v ===\n"
    "test_one (test_x.T) ... ok\n"
    "\n"
    "OK\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_missing_verdict.txt"] = (
    "=== $ python3 -m unittest test_y ===\n"
    "Ran 5 tests in 0.100s\n"
    "\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_missing_both_ran_and_verdict.txt"] = (
    "=== $ python3 -m unittest test_z ===\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_ran_line_malformed.txt"] = (
    "=== $ python3 -m unittest test_case_sensitivity ===\n"
    "ran 9 tests in 0.050s\n"
    "\n"
    "OK\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_verdict_before_ran.txt"] = (
    "=== $ python3 -m unittest test_reordered ===\n"
    "OK\n"
    "Ran 6 tests in 0.030s\n"
    "\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_test_failure.txt"] = (
    "=== $ python3 -m unittest test_broken ===\n"
    "test_one (test_broken.T) ... FAIL\n"
    "\n"
    "======================================================================\n"
    "FAIL: test_one (test_broken.T)\n"
    "----------------------------------------------------------------------\n"
    "AssertionError\n"
    "\n"
    "----------------------------------------------------------------------\n"
    "Ran 1 test in 0.004s\n"
    "\n"
    "FAILED (failures=1)\n"
    "exit=1\n"
).encode("utf-8")

_RAW["invalid_test_failure_in_preamble.txt"] = (
    "FAILED (failures=1)\n"
    "\n"
    "=== $ python3 -m unittest test_elsewhere ===\n"
    "Ran 2 tests in 0.010s\n"
    "\n"
    "OK\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_bad_utf8.bin"] = (
    b"=== $ python3 x.py ===\n\xff\xfe not valid utf-8 \x80\x81\nexit=0\n"
)

_RAW["invalid_exit_twice_same_value.txt"] = (
    "=== $ python3 idempotent.py ===\n"
    "exit=0\n"
    "did some cleanup after\n"
    "exit=0\n"
).encode("utf-8")

_RAW["invalid_multi_record_one_bad.txt"] = (
    "=== $ python3 first.py ===\n"
    "fine\n"
    "exit=0\n"
    "\n"
    "=== $ python3 second.py ===\n"
    "missing its exit line entirely\n"
    "\n"
    "=== $ python3 third.py ===\n"
    "fine\n"
    "exit=0\n"
).encode("utf-8")

# A small multi-directory tree for --root scan tests: a clean tool, a
# broken one, a headerless one, a hidden dir, a __pycache__ dir (both must
# be skipped by discover()), and a stray non-directory file at the root
# (also skipped). "root_demo/tool-empty" has NO captured_output.txt and is
# listed in EMPTY_DIRS below, not here.
_RAW["root_demo/tool-clean/captured_output.txt"] = _RAW["valid_test_record.txt"]
_RAW["root_demo/tool-broken/captured_output.txt"] = _RAW["invalid_missing_exit.txt"]
_RAW["root_demo/tool-nohead/captured_output.txt"] = _RAW["invalid_preamble_only.txt"]
_RAW["root_demo/.hidden-tool/captured_output.txt"] = _RAW["valid_minimal.txt"]
_RAW["root_demo/__pycache__/captured_output.txt"] = _RAW["valid_minimal.txt"]
_RAW["root_demo/stray_file.txt"] = b"not a directory, should be ignored by discover()\n"

# Directories that must exist after generation but contain no files of
# their own. os.walk()-based copies and most archive formats drop these
# silently, which is exactly the corruption this generator is written to
# avoid -- see generate() below.
EMPTY_DIRS = ["root_demo/tool-empty"]

# base64 is the only representation generate() actually reads from.
FIXTURES = {name: base64.b64encode(data).decode("ascii") for name, data in _RAW.items()}


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def generate(output_dir, force=False):
    """Write every fixture under output_dir. Binary mode, no encoding
    guesses, empty directories created explicitly. Returns the sorted list
    of relative paths written (files only, not the empty dirs)."""
    if os.path.isdir(output_dir) and os.listdir(output_dir) and not force:
        raise FileExistsError(
            "%s already exists and is not empty (pass force=True to overwrite)"
            % output_dir)
    if os.path.isdir(output_dir) and force:
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    written = []
    for rel_path in sorted(FIXTURES):
        full = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = base64.b64decode(FIXTURES[rel_path])
        with open(full, "wb") as fh:  # binary mode -- no newline translation
            fh.write(data)
        written.append(rel_path)

    for rel_dir in sorted(EMPTY_DIRS):
        full = os.path.join(output_dir, rel_dir)
        os.makedirs(full, exist_ok=True)

    return written


def round_trip_ok():
    """Every fixture's base64 form decodes back to exactly the bytes it was
    built from. This is necessarily true by construction (FIXTURES is
    derived from _RAW by encoding, never typed by hand), but is checked
    explicitly rather than assumed -- see the module docstring."""
    for name, raw in _RAW.items():
        if base64.b64decode(FIXTURES[name]) != raw:
            return False, name
    return True, None


# --------------------------------------------------------------------------
# --verify: generate twice into independent temp dirs, diff -r them, and
# confirm the empty directory is really empty on disk in both.
# --------------------------------------------------------------------------

def verify():
    ok, bad_name = round_trip_ok()
    if not ok:
        print("FAIL: base64 round-trip mismatch for %s" % bad_name)
        return 1

    d1 = tempfile.mkdtemp(prefix="fixtures_verify_a_")
    d2 = tempfile.mkdtemp(prefix="fixtures_verify_b_")
    try:
        w1 = generate(d1, force=True)
        w2 = generate(d2, force=True)
        if w1 != w2:
            print("FAIL: two generate() calls produced different file lists")
            return 1

        for rel_dir in EMPTY_DIRS:
            for d in (d1, d2):
                p = os.path.join(d, rel_dir)
                if not os.path.isdir(p):
                    print("FAIL: empty directory fixture missing on disk: %s" % p)
                    return 1
                if os.listdir(p):
                    print("FAIL: %s should be empty, contains %r" % (p, os.listdir(p)))
                    return 1

        proc = subprocess.run(["diff", "-r", d1, d2], capture_output=True, text=True)
        if proc.returncode != 0:
            print("FAIL: diff -r found a difference between two independent generate() calls")
            print(proc.stdout)
            print(proc.stderr)
            return 1

        cmp_result = filecmp.dircmp(d1, d2)

        def assert_dircmp_clean(dc):
            if dc.left_only or dc.right_only or dc.diff_files or dc.funny_files:
                return False
            for sub in dc.subdirs.values():
                if not assert_dircmp_clean(sub):
                    return False
            return True

        if not assert_dircmp_clean(cmp_result):
            print("FAIL: filecmp.dircmp found a structural difference")
            return 1

        print("OK: %d fixture files + %d empty dir(s), base64 round-trip clean, "
              "diff -r identical across two independent generate() calls"
              % (len(w1), len(EMPTY_DIRS)))
        return 0
    finally:
        shutil.rmtree(d1)
        shutil.rmtree(d2)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--verify" in argv:
        return verify()
    force = "--force" in argv
    positional = [a for a in argv if not a.startswith("--")]
    if not positional:
        print("usage: make_fixtures.py OUTPUT_DIR [--force] | make_fixtures.py --verify",
              file=sys.stderr)
        return 2
    written = generate(positional[0], force=force)
    print("wrote %d fixture files + %d empty dir(s) under %s"
          % (len(written), len(EMPTY_DIRS), positional[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

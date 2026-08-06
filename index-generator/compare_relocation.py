#!/usr/bin/env python3
"""compare_relocation.py -- the byte-identity comparison for the
relocation proof (see README.md, "Relocation and determinism").

`capture.sh` is run three times to produce three real, independently
generated `captured_output.txt` files: twice in place, once from a
relocated copy of the repository at a differently-named absolute path.
Those three files are NOT expected to be byte-identical as raw files,
because two things inside them are genuinely, honestly non-deterministic
across separate runs on the same machine:

  1. `Ran <N> tests in <duration>s` -- unittest's own real wall-clock
     measurement of how long the run took. <N> (the test COUNT) must
     still agree across all three files; only the floating-point
     <duration> varies.
  2. `/tmp/indexgen_test_<random>` -- `tempfile.TemporaryDirectory`
     names embedded verbatim in two of test_indexgen.py's own test
     methods (`test_unwritable_output_exit_2`,
     `test_unwritable_write_index_exit_2`), which print an error message
     containing the full temp path to stderr; `capture.sh` faithfully
     captures that stderr into the transcript, random suffix and all.
     This is pre-existing behaviour of test_indexgen.py (unmodified by
     this task) and would affect any transcript of this suite, not just
     ones produced by capture.sh.

This module normalises exactly those two things -- nothing else -- and
hashes both the raw and the normalised text, so a reviewer can see
precisely what was masked and confirm nothing else differs.

Usage:
    python3 compare_relocation.py FILE1 FILE2 [FILE3 ...]
    python3 compare_relocation.py FILE1 FILE2 FILE3 -o relocation_report.json

Exit codes:
    0  all inputs normalise to the same bytes
    1  at least one file's normalised form differs from the others
    2  setup error (a given path does not exist / cannot be read)
"""
import argparse
import hashlib
import json
import os
import re
import sys

# Group 1 keeps "Ran <N> tests in "; the trailing floating-point duration
# (and its 's' suffix) is replaced. The test COUNT is deliberately NOT
# normalised -- two files disagreeing on N is a real difference, not
# volatility, and must still break byte-identity after normalisation.
_RAN_LINE_RE = re.compile(r"(Ran \d+ tests? in )[0-9.]+s")

# tempfile.TemporaryDirectory(prefix="indexgen_test_") in test_indexgen.py
# produces a random suffix (letters/digits/underscore); it appears inside
# an error message string, e.g.
#   .../tmp/indexgen_test_apnu6l9a/no_such_subdir/report.json
_TMP_DIR_RE = re.compile(r"/tmp/indexgen_test_[A-Za-z0-9_]+")

_DURATION_PLACEHOLDER = r"\1<DURATION>s"
_TMPDIR_PLACEHOLDER = "/tmp/indexgen_test_<RANDOM>"


def normalise(text):
    """Return `text` with exactly the two documented volatile fields
    masked. Pure function, no filesystem access, so it is directly
    unit-testable against synthetic strings."""
    text = _RAN_LINE_RE.sub(_DURATION_PLACEHOLDER, text)
    text = _TMP_DIR_RE.sub(_TMPDIR_PLACEHOLDER, text)
    return text


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compare(paths):
    """Return a report dict. Raises OSError if a path cannot be read."""
    entries = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
        norm = normalise(raw)
        entries.append({
            "path": p,
            "raw_sha256": sha256_hex(raw),
            "normalised_sha256": sha256_hex(norm),
            "bytes": len(raw.encode("utf-8")),
        })
    raw_hashes = {e["raw_sha256"] for e in entries}
    norm_hashes = {e["normalised_sha256"] for e in entries}
    return {
        "files": entries,
        "raw_byte_identical": len(raw_hashes) == 1,
        "normalised_byte_identical": len(norm_hashes) == 1,
        "normalisation_applied": [
            r"(Ran \d+ tests? in )[0-9.]+s -> \1<DURATION>s",
            r"/tmp/indexgen_test_[A-Za-z0-9_]+ -> /tmp/indexgen_test_<RANDOM>",
        ],
    }


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="compare_relocation.py")
    ap.add_argument("paths", nargs="+", help="two or more captured_output.txt files to compare")
    ap.add_argument("-o", "--output", default=None, help="write the report JSON here")
    args = ap.parse_args(argv)

    for p in args.paths:
        if not os.path.isfile(p):
            sys.stderr.write("compare_relocation.py: not a file: %s\n" % p)
            return 2

    try:
        report = compare(args.paths)
    except OSError as exc:
        sys.stderr.write("compare_relocation.py: %s\n" % exc)
        return 2

    text = canonical_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)

    return 0 if report["normalised_byte_identical"] else 1


if __name__ == "__main__":
    sys.exit(main())

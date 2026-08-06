#!/usr/bin/env python3
"""pipe_scan.py -- Finding 4 disclosure utility (read-only, repo-wide).

Scans every ``captured_output.txt`` one level under ``--repo-root`` (the
same layout ``transcript-drift/driftcheck.py`` and
``transcript-schema/validate_transcript.py`` use) and reports which
command records contain a pipe (``|``) in their header.

This exists because the shared capture convention used across this
repository (see e.g. ``env-leak-scanner/capture.sh``,
``readme-index/capture.sh``, ``transcript-drift/capture.sh``) is:

    rec() {
        printf '\n=== $ %s ===\n' "$*" >> "$OUT"
        sh -c "$*" >> "$OUT" 2>&1
        printf 'exit=%s\n' "$?" >> "$OUT"
    }

``sh -c`` on a pipeline reports the LAST command's exit status. So any
recorded command containing a pipe masks the real exit status of every
earlier stage in that pipeline -- index-generator's Finding 2 (a failing
``unittest`` piped through ``grep`` is recorded as ``exit=0``),
generalised to every tool directory that follows this convention.

This tool is READ-ONLY: it does not modify, and this task does not fix,
any directory other than index-generator (see index-generator/README.md,
"Finding 4" -- deliberately out of scope here). It only counts and names
the affected files, for honest disclosure.

Usage:
    python3 pipe_scan.py --repo-root ..
    python3 pipe_scan.py --repo-root .. -o pipe_scan_report.json

Exit codes:
    0  scan completed (piped records may or may not have been found --
       this is a report, not a pass/fail check)
    2  --repo-root is not a directory
"""
import argparse
import json
import os
import re
import sys

HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$")


def scan(repo_root):
    """Return a deterministic report dict. No wall-clock reads; every list
    is sorted so two runs over the same tree produce identical bytes."""
    files_with_pipe = []
    total_piped_records = 0
    total_files = 0
    total_records = 0
    for name in sorted(os.listdir(repo_root)):
        d = os.path.join(repo_root, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        path = os.path.join(d, "captured_output.txt")
        if not os.path.isfile(path):
            continue
        total_files += 1
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        piped_here = 0
        for line in text.splitlines():
            m = HEADER_RE.match(line)
            if not m:
                continue
            total_records += 1
            if "|" in m.group(1):
                piped_here += 1
        if piped_here:
            total_piped_records += piped_here
            files_with_pipe.append({"tool": name, "piped_records": piped_here})
    files_with_pipe.sort(key=lambda d: d["tool"])
    return {
        "transcript_files_scanned": total_files,
        "total_command_records": total_records,
        "total_files_with_a_piped_record": len(files_with_pipe),
        "total_piped_records": total_piped_records,
        "files_with_piped_records": files_with_pipe,
    }


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipe_scan.py")
    ap.add_argument("--repo-root", default="..",
                     help="repository root containing the tool directories (default: ..)")
    ap.add_argument("-o", "--output", default=None, help="write the report JSON here")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.repo_root):
        sys.stderr.write("pipe_scan.py: --repo-root is not a directory: %s\n" % args.repo_root)
        return 2

    report = scan(args.repo_root)
    text = canonical_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

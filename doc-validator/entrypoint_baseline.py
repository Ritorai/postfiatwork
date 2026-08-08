#!/usr/bin/env python3
"""entrypoint_baseline.py -- run docval over every tool directory of a
repository and print the entrypoint picture for the whole tree.

Usage: python3 entrypoint_baseline.py <repo-root>

Why this exists at all, rather than one `docval.py --root <repo>` run:
docval's tool discovery stops at the FIRST directory on each descent path
that qualifies as a tool, and this repository's root qualifies -- it has a
`README.md`. So `docval.py --root <repo>` finds exactly one tool, the root,
and reports the single `DOC008_NO_CLI` that the repository's other gates
use as their baseline. Scanning the whole tree therefore means one docval
run per top-level directory, aggregated. That is all this script does.

It takes no options on purpose. It builds no argparse parser, so it is not
itself a "CLI" for docval or optioncheck to scan, and it adds no options to
`option_report.json`.

`--no-run` is passed to every child run: this is a static census of which
files README command lines name, and running 44 tools' documented commands
to answer that question would be both slow and beside the point.

Exit codes:
  0 - swept the tree, no DOC009 findings anywhere
  1 - swept the tree, at least one DOC009 finding
  2 - usage error (missing or non-directory argument, or a child run that
      could not be parsed)

Nothing here is committed as a report. A baseline JSON would go stale the
moment anyone edits a README, and this repository has enough of those; the
point-in-time listing lives in the delivery transcript instead.
"""

import collections
import json
import os
import subprocess
import sys

PROG = "entrypoint_baseline.py"
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

HERE = os.path.dirname(os.path.abspath(__file__))
DOCVAL = os.path.join(HERE, "docval.py")
SKIP_DIR_NAMES = {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache"}


def tool_dirs(repo_root):
    """Top-level directories of the repository, sorted, dot-dirs skipped."""
    out = []
    for name in sorted(os.listdir(repo_root)):
        if name in SKIP_DIR_NAMES or name.startswith("."):
            continue
        full = os.path.join(repo_root, name)
        if os.path.isdir(full):
            out.append((name, full))
    return out


def scan(directory):
    """One docval --no-run run. Returns its parsed report."""
    proc = subprocess.run(
        [sys.executable, DOCVAL, "--root", directory, "--no-run"],
        capture_output=True, text=True, shell=False,
    )
    if proc.returncode == EXIT_ERROR:
        raise ValueError("docval exited 2 for %r: %s"
                         % (directory, (proc.stderr or "").strip()))
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ValueError("could not parse the report for %r (%s)"
                         % (directory, exc))


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("%s: usage: %s <repo-root>\n" % (PROG, PROG))
        return EXIT_ERROR
    repo_root = argv[1]
    if not os.path.isdir(repo_root):
        sys.stderr.write("%s: error: %r is not a directory\n" % (PROG, repo_root))
        return EXIT_ERROR

    codes = collections.Counter()
    entrypoint_rows = []
    tools_scanned = 0

    for name, full in tool_dirs(repo_root):
        try:
            report = scan(full)
        except ValueError as exc:
            sys.stderr.write("%s: error: %s\n" % (PROG, exc))
            return EXIT_ERROR
        tools_scanned += report["tool_count"]
        for finding in report["findings"]:
            codes[finding["code"]] += 1
            if finding["code"] == "DOC009_BROKEN_ENTRYPOINT":
                # docval reports paths relative to ITS --root, which was
                # this directory; re-prefix so the row names the file from
                # the repository root and nothing here is absolute.
                entrypoint_rows.append(
                    (name, finding["detail"].replace("./", name + "/", 1)))

    print("tool directories swept: %d   docval tool_count total: %d"
          % (len(tool_dirs(repo_root)), tools_scanned))
    print()
    for code in sorted(codes):
        print("%-34s %4d" % (code, codes[code]))
    print("%-34s %4d" % ("TOTAL", sum(codes.values())))
    print()
    if entrypoint_rows:
        print("DOC009_BROKEN_ENTRYPOINT, every finding:")
        for name, detail in sorted(entrypoint_rows):
            print("  %s" % detail)
    else:
        print("DOC009_BROKEN_ENTRYPOINT: none in this tree.")
    return EXIT_FINDINGS if entrypoint_rows else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))

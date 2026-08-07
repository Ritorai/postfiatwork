#!/usr/bin/env python3
"""stale_demo.py -- one-line-invocable demo for capture_counts.sh.

Copies this directory's checkable files to a throwaway path, edits ONE
number in the copied README, runs readmecounts.py against the copy, and
prints what happened plus the real exit code. The copy is removed by its
exact full path; nothing outside it is touched.

It exists because this repository's transcript grammar puts the whole
command on one `=== $ ... ===` header line, and a multi-line
`python3 -c '...'` would break that. Same reason as
regen-preflight/fixture_demo.py.

Usage:
    python3 stale_demo.py                 # default: the case_count claim
    python3 stale_demo.py --claim test_count
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import readmecounts as rc  # noqa: E402

FILES = ("README.md", "test_adversarial.py", "make_fixtures.py",
         "expected_results.json", "test_readmecounts.py")

#: The claims this demo can make stale. The MUTATION IS DERIVED from
#: readmecounts.LOCATORS at run time (see rc.bump_claim), so nothing here
#: names a number or a sentence -- a demo that hardcoded "Ran 122 tests"
#: would exit 2 on the day the README was correctly updated, and bake two
#: exit-2 records into the committed transcript.
CLAIMS = ["case_count", "test_count", "fixture_file_count",
          "empty_dir_count", "own_test_count"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stale_demo.py")
    ap.add_argument("--claim", default="case_count", choices=sorted(CLAIMS))
    args = ap.parse_args(argv)

    parent = tempfile.mkdtemp(prefix="stale_demo_")
    try:
        root = os.path.join(parent, "adversarial-suite")
        os.makedirs(root)
        for name in FILES:
            shutil.copy2(os.path.join(HERE, name), os.path.join(root, name))

        readme = os.path.join(root, "README.md")
        with open(readme, "r", encoding="utf-8") as fh:
            text = fh.read()
        try:
            new_text, old_value, new_value = rc.bump_claim(text, args.claim)
        except ValueError as exc:
            sys.stderr.write("stale_demo.py: %s\n" % exc)
            return 2
        with open(readme, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)

        print("claim under test: %s" % args.claim)
        print("changed ONE occurrence in the copied README: %s -> %s"
              % (old_value, new_value))
        print("")

        out = os.path.join(parent, "report.json")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "readmecounts.py"),
             "--readme", readme, "--root", root, "-o", out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        print("readmecounts.py exit code: %d" % proc.returncode)

        with open(out, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        for c in report["claims"]:
            if c["claim"] == args.claim:
                print("the %s row, verbatim from the JSON report:" % args.claim)
                print(json.dumps(
                    {k: c[k] for k in sorted(c)
                     if k in ("claim", "state", "claimed_distinct",
                              "measured")},
                    sort_keys=True, indent=1))
        print("failing claims: %s" % report["failing"])
        return 0
    finally:
        shutil.rmtree(parent)     # created above, by this exact full path


if __name__ == "__main__":
    sys.exit(main())

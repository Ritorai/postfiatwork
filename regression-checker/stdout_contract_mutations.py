#!/usr/bin/env python3
"""Measure what the stdout-contract tests actually catch.

A test that never fails is not evidence, so this harness breaks
``coverage_audit.py`` on purpose -- one small, realistic regression at a
time -- and reports whether the CLI stdout tests notice. It is the
measurement behind the "1 of 5 / 5 of 5" claim in README.md; the claim is
not typed by hand anywhere.

Each mutation is applied to a COPY of this directory made by this script
and removed by this script, by the same path. The working tree is never
written to, which is checkable: run this, then ``git status``.

    python3 stdout_contract_mutations.py                 # the whole matrix
    python3 stdout_contract_mutations.py --rev HEAD~1    # against a parent
    python3 stdout_contract_mutations.py --list

Exit codes:
  0 = every mutation was caught by at least one test, and the unmutated
      control passed
  1 = at least one mutation went unnoticed, or the control failed
  2 = usage or setup error (bad --rev, git missing, anchor not found)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROG = "stdout_contract_mutations.py"
EXIT_OK = 0
EXIT_UNCAUGHT = 1
EXIT_ERROR = 2

HERE = os.path.dirname(os.path.abspath(__file__))
DIRNAME = os.path.basename(HERE)
#: Default: the whole CLI class. --target narrows it to one test, which
#: is how "what did THAT test catch" is answered without crediting it for
#: a property a sibling test already covered.
TEST_TARGET = "test_coverage_audit.TestCLI"

#: unittest's summary line. Used to prove the target actually ran tests.
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)

#: unittest's marker for "that name did not resolve to a test".
LOADER_FAILURE_RE = re.compile(
    r"unittest\.loader\._FailedTest|ModuleNotFoundError|"
    r"has no attribute", re.M)

#: The single line every mutation rewrites: the success path's only write
#: to stdout, and its return of the audit's exit code.
ANCHOR = "            sys.stdout.write(text)\n        return exit_code"

#: (name, replacement, what a reader should conclude when it is missed)
MUTATIONS = [
    ("banner_before_json",
     "            sys.stdout.write('AUDIT REPORT\\n')\n"
     "            sys.stdout.write(text)\n        return exit_code",
     "a human-readable banner printed before the report"),
    ("leading_blank_line",
     "            sys.stdout.write('\\n' + text)\n        return exit_code",
     "a blank line before the report -- json.loads skips leading "
     "whitespace, so parsing alone cannot see this"),
    ("no_trailing_newline",
     "            sys.stdout.write(text.rstrip('\\n'))\n        return exit_code",
     "the trailing newline dropped"),
    ("nonzero_exit_on_success",
     "            sys.stdout.write(text)\n        return exit_code or 3",
     "a nonzero exit code on a fully reproducing run"),
    ("chatter_on_stderr",
     "            sys.stderr.write('coverage_audit: 1 tool audited\\n')\n"
     "            sys.stdout.write(text)\n        return exit_code",
     "progress chatter written to stderr"),
]


class SetupError(Exception):
    """A condition that must exit 2, never 1."""


def source_tree(rev, workdir):
    """Return a directory holding this tool's source at `rev`.

    With no --rev that is this directory itself. With one, the directory
    is materialised from Git so the harness can be pointed at a parent
    commit and show what the OLD tests caught.
    """
    if rev is None:
        return HERE
    dest = os.path.join(workdir, "at_rev")
    os.makedirs(dest)
    try:
        # The pathspec is resolved against the directory git is run
        # from, so git runs at the checkout root and the pathspec is
        # this directory's name. Running it with -C HERE and a pathspec
        # of HERE's own basename fails, which is easy to write and
        # easier to miss.
        top = subprocess.run(
            ["git", "-C", HERE, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if top.returncode != 0:
            raise SetupError("--rev given but %s is not inside a Git "
                             "checkout" % HERE)
        root = top.stdout.decode("utf-8", "replace").strip()
        archive = subprocess.run(
            ["git", "-C", root, "archive", rev, DIRNAME],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except FileNotFoundError:
        raise SetupError("git executable not found on PATH")
    if archive.returncode != 0:
        raise SetupError("git archive %s failed: %s"
                         % (rev, archive.stderr.decode("utf-8", "replace").strip()))
    tar = subprocess.run(["tar", "-x", "-C", dest],
                         input=archive.stdout,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if tar.returncode != 0:
        raise SetupError("tar failed: %s"
                         % tar.stderr.decode("utf-8", "replace").strip())
    return os.path.join(dest, DIRNAME)


def run_one(src, name, replacement, target=TEST_TARGET):
    """Apply one mutation to a copy of `src` and run the CLI tests.

    Returns (caught, detail). `caught` is True when the tests failed,
    which for a deliberately broken tool is the desired outcome.
    """
    parent = tempfile.mkdtemp(prefix="stdout_contract_")   # created here
    dest = os.path.join(parent, DIRNAME)
    try:
        shutil.copytree(src, dest,
                        ignore=shutil.ignore_patterns("__pycache__"))
        if replacement is not None:
            path = os.path.join(dest, "coverage_audit.py")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if text.count(ANCHOR) != 1:
                raise SetupError(
                    "the mutation anchor matched %d times in %s; the "
                    "harness cannot claim to have broken anything"
                    % (text.count(ANCHOR), name))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(ANCHOR, replacement))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", target],
            cwd=dest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, timeout=900)
        out = proc.stdout.decode("utf-8", "replace")

        # A --target naming a test that does not exist does not make
        # unittest report zero tests: it synthesises a _FailedTest and
        # reports "Ran 1 test ... FAILED". "Nonzero exit" is exactly how
        # this harness recognises "the mutation was caught", so without
        # this check a stale --target credits a NONEXISTENT test with
        # catching every mutation and prints "caught 5 of 5". That is the
        # same shape of silent pass this submission is about, so it is a
        # setup error rather than a result.
        ran = RAN_RE.search(out)
        if ran is None or int(ran.group(1)) == 0:
            raise SetupError(
                "target %r collected no tests in the %s copy; a target "
                "that runs nothing cannot catch anything" % (target, name))
        if LOADER_FAILURE_RE.search(out):
            raise SetupError(
                "target %r did not resolve to a real test (unittest "
                "synthesised a loader failure for it); a nonexistent test "
                "would otherwise be credited with catching every mutation"
                % target)

        failing = sorted(
            line.split(" ", 2)[1]
            for line in out.splitlines()
            if line.startswith("FAIL: ") or line.startswith("ERROR: "))
        return proc.returncode != 0, failing
    finally:
        shutil.rmtree(parent)                              # created above


def main(argv=None):
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    ap.add_argument("--rev", help="Measure the source at this Git revision "
                                  "instead of the working tree.")
    ap.add_argument("--target", default=TEST_TARGET,
                    help="unittest target to run against each mutation "
                         "(default: %s). Narrow it to a single test to "
                         "measure that test alone rather than everything "
                         "its class happens to cover." % TEST_TARGET)
    ap.add_argument("--list", action="store_true",
                    help="Print the mutation names and exit 0.")
    args = ap.parse_args(argv)

    if args.list:
        for name, _, why in MUTATIONS:
            print("%-24s %s" % (name, why))
        return EXIT_OK

    workdir = tempfile.mkdtemp(prefix="stdout_contract_src_")   # created here
    try:
        try:
            src = source_tree(args.rev, workdir)
        except SetupError as exc:
            sys.stderr.write("%s: error: %s\n" % (PROG, exc))
            return EXIT_ERROR

        print("subject: %s" % (args.rev or "the working tree"))
        print("tests:   %s" % args.target)
        print()

        try:
            control_failed, _ = run_one(src, "none", None, args.target)
        except SetupError as exc:
            sys.stderr.write("%s: error: %s\n" % (PROG, exc))
            return EXIT_ERROR
        if control_failed:
            # Every "CAUGHT" below would be unattributable: the tests were
            # already failing before anything was broken on purpose.
            sys.stderr.write(
                "%s: error: the unmutated control run FAILED for target %r. "
                "Nothing can be attributed to a mutation when the suite is "
                "red before any mutation is applied.\n" % (PROG, args.target))
            return EXIT_ERROR
        print("%-24s %s" % ("(unmutated control)", "passed, as it must"))

        caught = 0
        for name, replacement, _why in MUTATIONS:
            try:
                was_caught, failing = run_one(src, name, replacement,
                                             args.target)
            except SetupError as exc:
                sys.stderr.write("%s: error: %s\n" % (PROG, exc))
                return EXIT_ERROR
            caught += bool(was_caught)
            if was_caught:
                print("%-24s CAUGHT by %s" % (name, ", ".join(failing) or "?"))
            else:
                print("%-24s MISSED -- no test noticed" % name)

        print()
        print("caught %d of %d mutations" % (caught, len(MUTATIONS)))
        if caught != len(MUTATIONS):
            return EXIT_UNCAUGHT
        return EXIT_OK
    finally:
        shutil.rmtree(workdir)                                  # created above


if __name__ == "__main__":
    sys.exit(main())

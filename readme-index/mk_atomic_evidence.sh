#!/usr/bin/env bash
# Regenerates ATOMIC_WRITE_EVIDENCE.txt. Run from inside readme-index/.
#
# Deliberately NOT named capture.sh. regen-preflight discovers regenerators by
# that exact name and re-runs each one inside a copy of the tree made with
# copytree(..., ignore=ignore_patterns(".git", "__pycache__")). This script
# reads Git history -- it needs the pre-fix source to run the suite against --
# so in that copy it could not work, and naming it capture.sh would turn a
# correct script into a preflight failure.
#
# PARENT is the tree this fix landed on. Hard-coded rather than HEAD~1 so the
# "before" column keeps naming the same source no matter what is committed
# afterwards. Override it to re-measure against a different tree.
PARENT=${PARENT:-abb45e149cb08e285320f600415e2a8975115cf5}
out=ATOMIC_WRITE_EVIDENCE.txt
scratch=_atomic_scratch

# bash, not sh: `set -o pipefail` is what makes a piped record's exit= line
# report the failing command rather than the exit status of `tail`. /bin/sh
# here is dash and has no pipefail, which would silently turn a failing suite
# into exit=0.
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  bash -c "set -o pipefail; $1" >> "$out" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$out"
}

# unittest tracebacks name modules by __file__, which Python makes absolute.
# This repository ships an environment-leak scanner, and an absolute path in a
# committed transcript is a finding, so the leading directories are trimmed
# back to the scratch directory's own name. The pattern contains no path and
# no double quote, so it survives being passed through rec() unchanged.
STRIP="sed 's|/[^ ]*/$scratch/|$scratch/|g'"

rm -rf "$scratch"
mkdir -p "$scratch"
cat > "$out" <<'HDR'
readme-index -- atomic write evidence
=====================================

Records use transcript-drift/FORMAT.md's grammar: a "=== $ command ===" header,
the verbatim output, and an exit= line. All paths are relative. Records run
under `bash -c 'set -o pipefail; ...'`, so a piped record's exit= line reports
the command that failed rather than the exit status of `tail`.

This file DEPARTS from FORMAT.md in one respect, deliberately: it commits
`FAILED` verdicts. FORMAT.md classifies a committed FAILED as drift by
definition, and it is right to -- for captured_output.txt, which is a claim
that the tool works. This file is the opposite claim: that the tool did NOT
work before the fix. The repository's validators only ever read
captured_output.txt, so nothing here is being smuggled past a gate; the
departure is stated because a file claiming conformance while violating a rule
is worse than one that says which rule it breaks and why.

What this proves, in order:

  1. the same test file, run against the PRE-FIX source, fails -- and fails by
     finding a destination truncated where the previous bytes should still be,
     not merely by missing a helper;
  2. a direct demonstration of that truncation, outside unittest;
  3. the same test file, run against the fixed source, passes;
  4. the same demonstration against the fixed source leaves the destination
     byte-identical and no temp file behind.

PARENT is the commit this fix landed on. The pre-fix source is read out of Git
rather than kept as a second copy in the tree, because a committed copy of a
superseded file is exactly the kind of thing that goes stale unnoticed.

The one number here that is not reproducible is the wall-clock in unittest's
`Ran N tests in ...s` line. Everything else -- counts, verdicts, byte sizes,
exit codes -- is fixed by construction.

HDR
rec "echo $PARENT"
rec "git cat-file -t $PARENT"
rec "git show $PARENT:readme-index/readmeindex.py > $scratch/readmeindex.py && wc -l $scratch/readmeindex.py"
rec "grep -n 'open(args.rewrite\|open(args.output\|write_text_atomically' $scratch/readmeindex.py"
rec "grep -n 'open(args.rewrite\|open(args.output\|write_text_atomically' readmeindex.py"
rec "cp test_readmeindex.py demo_partial_write.py $scratch/ && ls $scratch"
rec "cd $scratch && python3 -m unittest test_readmeindex 2>&1 | $STRIP"
rec "cd $scratch && python3 -m unittest test_readmeindex 2>&1 | tail -4"
rec "cd $scratch && python3 demo_partial_write.py"
rec "python3 demo_partial_write.py"
rec "python3 -m unittest test_readmeindex 2>&1 | tail -4"
rec "find . -maxdepth 1 -name '.readmeindex-*' -print | wc -l"
rm -rf "$scratch"
rec "ls -d $scratch 2>&1; echo scratch-removed"

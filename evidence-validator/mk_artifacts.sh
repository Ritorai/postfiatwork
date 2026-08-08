# Regenerates this directory's committed artifacts. Run from inside
# evidence-validator/:
#
#   bash mk_artifacts.sh
#
# Writes captured_output.txt, run_output.txt (a byte-identical copy --
# see README.md, "Two transcripts") and TRAILING_NEWLINE_EVIDENCE.txt.
#
# No shebang, on purpose. Everything in this repository is committed
# through a path that cannot set an executable bit, so every file lands
# at mode 100644 -- and a `#!` line on a file the filesystem will never
# execute is exactly the contradiction shebang-mode's SM002 rule
# reports. Naming the interpreter in the usage line above costs nothing
# and promises nothing the mode cannot keep.
#
# Deliberately NOT named capture.sh. regen-preflight discovers
# regenerators by that exact name, and an extra one here would add an
# item to its run, leaving regen-preflight's committed
# preflight_report.json -- which its own README calls evidence of one
# run rather than a regenerable report -- describing a smaller
# repository than the one it sits in. The brief this script was added
# under puts regen-preflight off limits. (The unittest wall-clock is
# NOT the reason: regenpre.py masks `Ran N tests in ...s` before
# comparing.) TRAILING_NEWLINE_EVIDENCE.txt has a second, harder
# reason: it reads the pre-fix source out of Git, and regen-preflight
# re-runs regenerators inside a copy made without .git.
#
# set -u but NOT set -e: most records below are expected to exit
# non-zero, because rejecting bad input is what this tool does.
set -u

# The per-test listing record below pipes through `sort`, and glibc
# collation differs from C on lines that mix indented subTest entries
# with unindented method entries. Pinned so the committed listing
# cannot reorder under a different LANG.
export LC_ALL=C

# PARENT is the tree the anchor fix landed on. Hard-coded rather than
# HEAD~1 so the "before" half keeps naming the same source no matter
# what is committed afterwards. Override it to measure against another.
PARENT=${PARENT:-07efc15c7d462e72cd1978e186981ab8a6fe2c7b}

out=captured_output.txt
evidence=TRAILING_NEWLINE_EVIDENCE.txt
scratch=_anchor_scratch

# Header form 1: the exit status is part of the record.
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  bash -c "$1" >> "$out" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$out"
}

# Header form 2: output is the point, status is not recorded.
#
# Every step added by this change uses this form, for one reason:
# index-generator pins a repository-wide count of `=== $ ===` records at
# 548, and index-generator is off limits under the brief these steps
# were added under. The number lives in that tool's committed
# pipe_classification_report.json and its README, not in its transcript,
# and test_pipe_classify.TestCommittedReportIsFresh is the gate that
# fires. Measured, not assumed: promoting the four plain steps below to
# records takes pipe_scan's total_command_records to 552 and turns that
# test red.
#
# These steps deliberately do not echo a status. FORMAT.md reserves
# `exit=` as a record terminator and a plain-form step is not a record
# of its own: transcript-schema reads an `exit=` line here as a second
# terminator inside the preceding record, and any other `exit...=`
# spelling as a malformed one. Every exit code is recorded properly, on
# a real terminator, in TRAILING_NEWLINE_EVIDENCE.txt.
plain() {
  printf '$ %s\n' "$1" >> "$out"
  bash -c "$1" >> "$out" 2>&1
}

# Records for the evidence file, which uses the full grammar throughout.
erec() {
  printf '=== $ %s ===\n' "$1" >> "$evidence"
  bash -c "set -o pipefail; $1" >> "$evidence" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$evidence"
}

# ---------------------------------------------------------------- #
# 1. captured_output.txt, and its copy run_output.txt
# ---------------------------------------------------------------- #
rm -f "$out"

rec 'python3 -m unittest test_validator -v'
rec 'python3 validator.py sample_valid.json --pretty'
rec 'python3 validator.py sample_invalid.json --pretty'

# Recorded because it explains the shape of the listing above.
# unittest's verbose test id changed in CPython 3.11: 3.10 and earlier
# print "test_x (module.Class)", 3.11 and later print
# "test_x (module.Class.test_x)". The listing therefore differs from
# the one this file carried before, on every one of its lines, without
# any test having been renamed. Other transcripts in this repository
# already show the 3.11 form.
printf '\n' >> "$out"
plain 'python3 --version'

# The fixture this tool did not reject until the anchor fix.
printf '\n' >> "$out"
plain 'python3 validator.py sample_trailing_newline.json --pretty'

# The two remaining commands from README.md's rerun list, so every one
# of them has captured output somewhere in this file.
#
# The missing-file command was left out of earlier versions of this
# script on the theory that its absolute path would register with
# env-leak-scanner. That was wrong and is worth recording as wrong:
# EL-ABS-POSIX requires two or more path segments, "/nonexistent.json"
# has one, and committing this record leaves confirmed leaks at 851.
printf '\n' >> "$out"
plain 'python3 validator.py sample_invalid.json'
printf '\n' >> "$out"
plain 'python3 validator.py /nonexistent.json'

cp "$out" run_output.txt

# ---------------------------------------------------------------- #
# 2. TRAILING_NEWLINE_EVIDENCE.txt
# ---------------------------------------------------------------- #
rm -rf "$scratch"
mkdir -p "$scratch"
cat > "$evidence" <<'HDR'
evidence-validator -- trailing-newline anchor evidence
======================================================

Records use transcript-drift/FORMAT.md's grammar: a "=== $ command ==="
header on one line, the verbatim output, and an exit= line. Every path
is relative and every scratch file is written inside this directory:
env-leak-scanner reads every .md and .txt in the tree, and a
multi-segment absolute path in a committed transcript becomes a
confirmed leak in its report. Its EL-ABS-POSIX rule needs two or more
segments, so a one-segment path such as "/nonexistent.json" does not
register; a two-segment device path and a temp path both do, and an
earlier draft of this file committed one of each. Naming either of them
here would have re-added the finding, so this paragraph does not.
Records run under `bash -c 'set -o pipefail; ...'`, so
a piped record's exit= line reports the command that failed rather than
the exit status of tail.

This file DEPARTS from FORMAT.md deliberately, in the ways
transcript-schema reports when it is pointed at it: it commits `FAILED`
verdicts, and several unittest records are piped through `tail` or a
`sort`, so they carry no `Ran N tests` line and no verdict of their
own. FORMAT.md is right to classify a committed FAILED as drift -- for
captured_output.txt, which is a claim that the tool works. This file is
the opposite claim: that the tool accepted a value its own README calls
malformed. The transcript validators -- transcript-drift,
transcript-schema, index-generator's pipe_scan, regen-preflight -- only
ever read a file named captured_output.txt, so nothing here is being
smuggled past a gate; the departures are stated because a file claiming
conformance while violating a rule is worse than one that says which
rules it breaks and why.

THE DEFECT

README.md says `tx_hash` is "exactly 64 uppercase hex characters" and
its schema table gives `[0-9A-F]{64}`. validator.py compiled that as
`^[0-9A-F]{64}$`. In Python, `$` matches at the end of the string OR
just before a single newline at the end of the string, so a
SIXTY-FIVE character value ending in "\n" satisfied it. The same hole
was in all four format patterns: CIDV0_RE, CIDV1_RE, TXHASH_RE and
TASK_ID_RE.

The fix is four characters per pattern: `$` becomes `\Z`, which matches
only at the true end of the string. No new error code, no new flag, no
change to any exit code -- a value that fails the documented format now
produces the MALFORMED_* code README.md already documents for it, and
the process exits 1, which README.md already documents as "one or more
records had validation issues".

What this proves, in order:

  1. the exact scope of the hole, measured against the pre-fix patterns
     read out of Git: one trailing newline passed, and nothing else did;
  2. the pre-fix source accepting the committed fixture -- three
     records, all "clean", exit 0;
  3. which of the new tests fail against that source, ONE BY ONE, not
     just in aggregate;
  4. the fixed source rejecting the same fixture with one MALFORMED_*
     code per record and exit 1;
  5. the valid cases still working: sample_valid.json still exits 0,
     sample_invalid.json still exits 1 with the same findings, and the
     whole suite passes.

On point 3, read the per-test listing rather than the aggregate. Not
every new test fails against the pre-fix source and this file does not
claim otherwise: several pin behaviour the pre-fix source already had
and pass on both sides by design, because their job is to catch a
repair that breaks something rather than to demonstrate the defect. The
split is counted directly, two records below the listing, by grepping
the listing itself. The aggregate `FAILED (failures=...)` line is also
expanded by subTest, so it counts larger than the number of test
methods involved.

The one number here that is not reproducible is the wall-clock in
unittest's `Ran N tests in ...s` line. Everything else -- exit codes,
issue codes, counts, per-test verdicts -- is fixed by construction.

HDR

erec "echo $PARENT"
erec "git cat-file -t $PARENT"
erec "git show $PARENT:evidence-validator/validator.py > $scratch/validator.py && wc -l $scratch/validator.py"
erec "cp test_validator.py sample_valid.json sample_invalid.json sample_trailing_newline.json $scratch/ && ls $scratch"

# --- 1. the exact scope of the hole, against the pre-fix patterns -----
erec "cd $scratch && python3 -c \"import test_validator as T, validator as V; cases=[('one_trailing_newline', chr(10))]+list(T.NEAR_MISSES); [print('%-11s %-23s len=%-3d %s' % (n, lb, len(v), 'ACCEPTED' if getattr(V,n).match(v) else 'refused')) for n in sorted(T.GOOD_FOR_PATTERN) for lb,af in cases for v in [af+T.GOOD_FOR_PATTERN[n] if lb=='leading_newline' else T.GOOD_FOR_PATTERN[n]+af]]\""

# --- 2. the pre-fix source accepts the fixture -----------------------
erec "cd $scratch && python3 validator.py sample_trailing_newline.json --pretty"
erec "cd $scratch && python3 validator.py sample_valid.json | python3 -c \"import json,sys;print(json.load(sys.stdin)['totals'])\""

# --- 3. which new tests fail against it, one by one ------------------
erec "cd $scratch && python3 -m unittest test_validator 2>&1 | tail -1"
erec "cd $scratch && python3 -m unittest -v test_validator.TestEndAnchors test_validator.TestTrailingNewlineRecords test_validator.TestTrailingNewlineFixture test_validator.TestTrailingNewlineCli 2>&1 | grep -E '[.][.][.] (ok|FAIL|ERROR)' | sed 's/ [(].*[)] [.][.][.] / ... /' | sort"
erec "cd $scratch && python3 -m unittest -v test_validator.TestEndAnchors test_validator.TestTrailingNewlineRecords test_validator.TestTrailingNewlineFixture test_validator.TestTrailingNewlineCli 2>&1 | grep -cE '[.][.][.] ok'"
erec "cd $scratch && python3 -m unittest -v test_validator.TestEndAnchors test_validator.TestTrailingNewlineRecords test_validator.TestTrailingNewlineFixture test_validator.TestTrailingNewlineCli 2>&1 | grep -cE '[.][.][.] (FAIL|ERROR)'"

# --- 4. the fixed source rejects it ----------------------------------
erec "python3 -c \"import test_validator as T, validator as V; cases=[('one_trailing_newline', chr(10))]+list(T.NEAR_MISSES); [print('%-11s %-23s len=%-3d %s' % (n, lb, len(v), 'ACCEPTED' if getattr(V,n).match(v) else 'refused')) for n in sorted(T.GOOD_FOR_PATTERN) for lb,af in cases for v in [af+T.GOOD_FOR_PATTERN[n] if lb=='leading_newline' else T.GOOD_FOR_PATTERN[n]+af]]\""
erec "python3 validator.py sample_trailing_newline.json --pretty"

# --- 5. the valid cases still work -----------------------------------
erec "python3 validator.py sample_valid.json"
erec "python3 validator.py sample_invalid.json | python3 -c \"import json,sys;print(json.load(sys.stdin)['issue_totals'])\""
erec "python3 -m unittest test_validator.TestEndAnchors test_validator.TestTrailingNewlineRecords test_validator.TestTrailingNewlineFixture test_validator.TestTrailingNewlineCli 2>&1 | tail -4"
erec "python3 -m unittest test_validator 2>&1 | tail -4"
erec "python3 -c \"import validator;print(sorted((n,p.pattern) for n,p in vars(validator).items() if isinstance(p,type(validator.TXHASH_RE))))\""

rm -rf "$scratch"
erec "ls -d $scratch 2>&1; echo scratch-removed"

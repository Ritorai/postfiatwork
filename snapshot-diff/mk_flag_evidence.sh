# Regenerates SINGLE_USE_FLAG_EVIDENCE.txt. Run from inside snapshot-diff/:
#
#   bash mk_flag_evidence.sh
#
# No shebang, on purpose -- see the note at the top of
# mk_captured_output.sh. Every file here lands at mode 100644, and a
# `#!` line on a file that can never be executable is the contradiction
# shebang-mode's SM002 rule exists to report.
#
# Deliberately NOT named capture.sh. regen-preflight discovers
# regenerators by that exact name and re-runs each one inside
# copytree(root, dest, ignore=ignore_patterns(".git", "__pycache__")) --
# a copy with no Git history, where this script cannot work at all,
# because reading the pre-fix source out of Git is the whole point of
# the "before" half.
#
# Note that the unittest wall-clock is NOT part of that reason:
# regenpre.py masks `Ran N tests in ...s` before comparing, so a
# wall-clock alone yields volatile_only, which regen-preflight counts as
# a pass. captured_output.txt's regenerator, mk_captured_output.sh, is
# unnamed for a different reason again -- see the note at its head.
#
# PARENT is the tree this fix landed on. Hard-coded rather than HEAD~1
# so the "before" column keeps naming the same source no matter what is
# committed afterwards. Override it to measure against another tree.
set -u

# argparse wraps its usage line to the terminal width, and six of the
# records below capture that line. Pinned to 80 -- the same value
# shutil.get_terminal_size() falls back to when output is a pipe -- so
# the committed transcript cannot move with the width of whatever
# terminal regenerated it.
export COLUMNS=80

PARENT=${PARENT:-c7c507b080c8de6cf7aaec1375e860492e80aa6a}
out=SINGLE_USE_FLAG_EVIDENCE.txt
scratch=_singleuse_scratch
# A second scratch directory, kept alive past the first one because the
# readme-index records at the end write their reports into it.
scratch2=_singleuse_scratch2

# bash, not sh: `set -o pipefail` is what makes a piped record's exit=
# line report the failing command rather than the exit status of tail.
# /bin/sh here is dash and has no pipefail.
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  bash -c "set -o pipefail; $1" >> "$out" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$out"
}

rm -rf "$scratch" "$scratch2"
mkdir -p "$scratch" "$scratch2"
cat > "$out" <<'HDR'
snapshot-diff -- conflicting duplicate -o/--output evidence
===========================================================

Records use transcript-drift/FORMAT.md's grammar: a "=== $ command ==="
header on one line, the verbatim output, and an exit= line. All paths
are relative. Records run under `bash -c 'set -o pipefail; ...'`, so a
piped record's exit= line reports the command that failed rather than
the exit status of tail.

This file DEPARTS from FORMAT.md in three respects, deliberately. Run
transcript-schema's validator over it and it reports exactly these:

  * TRANSCRIPT_SHOWS_TEST_FAILURE -- it commits `FAILED` verdicts.
    FORMAT.md classifies a committed FAILED as drift by definition, and
    it is right to for captured_output.txt, which is a claim that the
    tool works. This file is the opposite claim: that the tool silently
    discarded a caller's output path before the fix.
  * TRANSCRIPT_RECORD_MISSING_RAN_LINE (6 records) and
  * TRANSCRIPT_RECORD_MISSING_VERDICT (3 records) -- several unittest
    records are piped through `tail -1`, `grep -c` or `sort`, because
    what is being recorded is a count or a per-test verdict, not a
    whole run. The full runs, with their `Ran N tests` and `OK` lines
    intact, are here too.

The transcript validators -- transcript-drift, transcript-schema,
index-generator's pipe_scan, regen-preflight -- only ever read a file
named captured_output.txt, so nothing here is being smuggled past a
gate. (env-leak-scanner does read this file, along with every other .md
and .txt in the tree, which is why every path below is relative and
every scratch file is written inside this directory.) The departures
are stated because a file claiming conformance while violating a rule
is worse than one that says which rules it breaks and why.

What this proves, in order:

  1. the pre-fix source, read out of Git, accepts `-o A -o B`, exits 1
     as though nothing were wrong, writes B, and never creates A --
     the caller asked for two reports and got one, with nothing on
     stderr to say so;
  2. which of the new tests fail against that source, ONE BY ONE, not
     just in aggregate;
  3. the fixed source refuses the same invocation with exit 2, names
     both spellings argparse resolved, and creates NEITHER file;
  4. the cases that must keep working still do: a single -o, a single
     --output, no -o at all, and --ignore repeated three times.

On point 2, read the per-test listing rather than the aggregate. Not
every new test fails against the pre-fix source and this file does not
claim otherwise: some of them pin behaviour the pre-fix source already
got right -- a single -o, a single --output, no -o at all, --ignore
repeated, --ignore repeated alongside one -o, and the usage line at two
terminal widths -- and they pass on both sides by design, because their
job is to catch a repair that breaks something rather than to
demonstrate the defect. The exact split is not restated here in prose;
it is counted directly, two records below the listing, by grepping the
listing itself. The aggregate `FAILED (failures=..., errors=...)` line
is also expanded by subTest -- three methods times seven spellings --
so it counts larger than the number of test methods involved. The
`... ok` / `... FAIL` listing is the honest form of the claim.

The one number here that is not reproducible is the wall-clock in
unittest's `Ran N tests in ...s` line. Everything else -- exit codes,
error text, file existence, counts, per-test verdicts -- is fixed by
construction.

HDR
rec "echo $PARENT"
rec "git cat-file -t $PARENT"
rec "git show $PARENT:snapshot-diff/snapdiff.py > $scratch/snapdiff.py && wc -l $scratch/snapdiff.py"
rec "cp test_snapdiff.py snapshot_before.json snapshot_after_same.json snapshot_after_changed.json $scratch/ && ls $scratch"

# --- BEFORE: the pre-fix source, last-one-wins ------------------------
rec "cd $scratch && rm -f A.json B.json && python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json -o B.json"
rec "cd $scratch && ls A.json B.json 2>&1"
rec "cd $scratch && rm -f A.json B.json && python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --output A.json -o B.json 2>&1 | wc -c"
rec "cd $scratch && python3 -m unittest test_snapdiff 2>&1 | tail -1"
# Per-test, not aggregate. Sorted so the listing does not depend on the
# order unittest happens to run subTest expansions in.
rec "cd $scratch && python3 -m unittest -v test_snapdiff.TestSingleUseOutputOption test_snapdiff.TestSingleUseAction 2>&1 | grep -E '[.][.][.] (ok|FAIL|ERROR)' | sed 's/ [(].*[)] [.][.][.] / ... /' | sort"
rec "cd $scratch && python3 -m unittest -v test_snapdiff.TestSingleUseOutputOption test_snapdiff.TestSingleUseAction 2>&1 | grep -cE '[.][.][.] ok'"
rec "cd $scratch && python3 -m unittest -v test_snapdiff.TestSingleUseOutputOption test_snapdiff.TestSingleUseAction 2>&1 | grep -cE '[.][.][.] (FAIL|ERROR)'"

# --- AFTER: the fixed source ------------------------------------------
rec "rm -f A.json B.json && python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json -o B.json"
rec "ls A.json B.json 2>&1"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --output A.json -o B.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --output=A.json --output=B.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -oA.json -oB.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json -o A.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json --ignore reward -o B.json"

# --- AFTER: the cases that must keep working --------------------------
rec "rm -f A.json && python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json"
rec "wc -c A.json"
rec "rm -f A.json && python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --output A.json"
rec "wc -c A.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_same.json"
rec "python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --ignore reward --ignore status --ignore summary -o A.json"
rec "python3 -c \"import json;print(json.load(open('A.json'))['ignored_fields'])\""
rec "python3 -m unittest test_snapdiff.TestSingleUseOutputOption test_snapdiff.TestSingleUseAction 2>&1 | tail -4"
rec "python3 -m unittest test_snapdiff 2>&1 | tail -4"
# The suite must not depend on the terminal width it is run at; the
# usage-line assertions are the reason that is worth a record.
rec "COLUMNS=40 python3 -m unittest test_snapdiff 2>&1 | tail -1"
rec "COLUMNS=200 python3 -m unittest test_snapdiff 2>&1 | tail -1"

# --- what this change leaves for readme-index --------------------------
# snapshot-diff's own README states a new test count where the root
# index table and readme-index's pinned corpus still state the old one.
# The brief for this change forbids altering readme-index, and editing
# the root README alone does not resolve it -- it trades readme-index's
# live-tree discrepancy for a corpus-to-root-README one. Both states,
# and the counterfactual, are recorded here so whoever owns readme-index
# can see what is outstanding and what each option costs.
#
# Every path below is relative, and every scratch file is written inside
# this directory: env-leak-scanner reads .md and .txt files across the
# whole tree, so an absolute path in a committed transcript becomes a
# confirmed leak in its report.
rec "grep -n 'snapshot-diff' ../README.md"
rec "grep -n '^[0-9][0-9]* tests:' README.md"
rec "cd ../readme-index && python3 readmeindex.py --corpus corpus.tsv --root-readme root_readme_after.md -o ../snapshot-diff/$scratch2/a.json"
rec "cd ../readme-index && python3 readmeindex.py --corpus corpus.tsv --root-readme ../README.md -o ../snapshot-diff/$scratch2/b.json"
rec "cd ../readme-index && python3 readmeindex.py --root .. --root-readme ../README.md -o ../snapshot-diff/$scratch2/c.json ; python3 -c \"import json,collections;d=json.load(open('../snapshot-diff/$scratch2/c.json'))['index_differences'];print(sorted(collections.Counter(e['kind'] for e in d).items()));print([e for e in d if e.get('tool')=='snapshot-diff'])\""

# The counterfactual: the same two runs against a root README whose
# snapshot-diff row has been updated to the live count. The live-tree
# discrepancy goes away and a corpus-vs-root-README one appears, which
# is why the row is not edited on its own.
rec "sed \"s/(snapshot-diff) | 222 |/(snapshot-diff) | \$(sed -n 's/^\\([0-9][0-9]*\\) tests:.*/\\1/p' README.md | head -1) |/\" ../README.md > $scratch2/root_readme_edited.md && grep -n 'snapshot-diff' $scratch2/root_readme_edited.md"
rec "cd ../readme-index && python3 readmeindex.py --corpus corpus.tsv --root-readme ../snapshot-diff/$scratch2/root_readme_edited.md -o ../snapshot-diff/$scratch2/d.json ; python3 -c \"import json;d=json.load(open('../snapshot-diff/$scratch2/d.json'))['index_differences'];print([e for e in d if e.get('tool')=='snapshot-diff'])\""
rec "cd ../readme-index && python3 readmeindex.py --root .. --root-readme ../snapshot-diff/$scratch2/root_readme_edited.md -o ../snapshot-diff/$scratch2/e.json ; python3 -c \"import json,collections;d=json.load(open('../snapshot-diff/$scratch2/e.json'))['index_differences'];print(sorted(collections.Counter(e['kind'] for e in d).items()));print([e for e in d if e.get('tool')=='snapshot-diff'])\""
rm -rf "$scratch2"
rec "ls -d $scratch2 2>&1; echo scratch2-removed"

rm -f A.json B.json
rm -rf "$scratch"
rec "ls -d $scratch 2>&1; echo scratch-removed"

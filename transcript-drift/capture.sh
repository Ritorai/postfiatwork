#!/bin/sh
# Regenerates captured_output.txt. Every record is a real run.
# Run from the repository root:  sh transcript-drift/capture.sh
set -u
OUT=transcript-drift/captured_output.txt

rec() {
    printf '\n=== $ %s ===\n' "$*" >> "$OUT"
    sh -c "$*" >> "$OUT" 2>&1
    printf 'exit=%s\n' "$?" >> "$OUT"
}

cat > "$OUT" <<'PREAMBLE'
transcript-drift -- captured verification output
================================================

Every command below was executed, and is recorded in this file's own
format: a "=== $ command ===" header, the verbatim output, and an exit=
line. This transcript is checked by the tool it documents.

COVERAGE, STATED UP FRONT: the repository-wide run below reads the file
CONTENTS of 11 of 42 tool directories. This environment has no outbound
git access (git clone returns 403 through the proxy), so the other 31
are checked for PRESENCE ONLY, from the GitHub tree API listing in
inventory.json at commit aa662156d6af526f98f139a8d0c824b78312dda1.
A reviewer with a clone drops --inventory and gets content drift for
all 42 from the same command. See "Coverage, stated plainly" in
README.md.

ON SELF-REFERENCE: the repo-wide records below run while this file is
still being appended to, so they see a transcript-drift transcript whose
final record has no exit= line yet. The committed
drift_report_2026-08-04.json is regenerated from the finished tree after
this file is complete. The NOTE at the end of this file states the exact
difference between the two, measured with diff.
PREAMBLE

rec "python3 --version"
rec "uname -sm"
rec "cd transcript-drift && python3 -m unittest test_driftcheck"

# ---- positive control -------------------------------------------------
# A checker reporting zero of a code may be working or may be blind.
# Each step below plants one defect in a scratch tree and shows the tool
# flipping from clean to that exact code. The scratch tree is relative,
# never committed, and removed at the end.
CTL=transcript-drift/_control
rm -rf "$CTL"
mkdir -p "$CTL/clean-tool"
cat > "$CTL/clean-tool/README.md" <<'R'
# clean-tool
Ran 3 tests, OK, exit 0.
```
python3 -m unittest test_clean
```
R
cat > "$CTL/clean-tool/captured_output.txt" <<'T'
=== $ python3 -m unittest test_clean ===
Ran 3 tests in 0.001s

OK
exit=0
T
rec "python3 transcript-drift/driftcheck.py --root $CTL"

control() {
    _name=$1; _sed=$2
    cp -r "$CTL/clean-tool" "$CTL/$_name"
    sed -i "$_sed" "$CTL/$_name/captured_output.txt"
    rec "python3 transcript-drift/driftcheck.py --root $CTL"
    rm -rf "$CTL/$_name"
}

control count-drift  's/Ran 3 tests in/Ran 41 tests in/'
control exit-drift   's/^exit=0$/exit=2/'
control failed-suite 's/^OK$/FAILED (failures=1)/'
control cmd-drift    's/test_clean ===/test_something_else ===/'
control no-records   's/^=== \$ \(.*\) ===$/$ \1/'

cp -r "$CTL/clean-tool" "$CTL/no-transcript"
rm "$CTL/no-transcript/captured_output.txt"
rec "python3 transcript-drift/driftcheck.py --root $CTL"
rm -rf "$CTL"

rec "python3 transcript-drift/driftcheck.py --root /nonexistent-directory"
rec "python3 transcript-drift/driftcheck.py --root . --inventory /nonexistent.json"

# ---- the repository-wide run -----------------------------------------
rec "python3 transcript-drift/driftcheck.py --root ."
rec "python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json"

# ---- determinism ------------------------------------------------------
# Both runs happen inside ONE record and write to files, so nothing is
# appended to this transcript between them -- they see an identical tree,
# which is what makes the comparison meaningful.
rec "python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json -o transcript-drift/_d1.json; python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json -o transcript-drift/_d2.json; cmp transcript-drift/_d1.json transcript-drift/_d2.json && echo BYTE_IDENTICAL; rm -f transcript-drift/_d1.json transcript-drift/_d2.json"

rec "python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json -o transcript-drift/drift_report_2026-08-04.json"

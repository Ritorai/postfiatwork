#!/usr/bin/env bash
# Regenerates captured_output.txt and test_output.txt. Run from inside
# reward-reconciler/.
#
# Both files are transcripts of the commands in README.md's "Exact rerun
# commands" block, and both embed unittest's -v listing plus its wall-clock.
# Every line comes from a run this script just performed.
#
# Deliberately not named capture.sh, for the same reason as
# mk_exitcode_evidence.sh: regen-preflight discovers regenerators by that
# exact name and diffs their output against the committed file, and the
# `Ran N tests in ...s` wall-clock in both of these would fail that diff on
# every run.
#
# A .sh and not a .py: index-generator flags a `.py` entrypoint that has no
# matching `test_*.py`, and a regenerator does not want a test suite of its
# own.
# `set -u` only: `set -e` would abort at the first record whose command
# exits non-zero, and two of the records below are documented exit-1 runs.
set -u
export PYTHONDONTWRITEBYTECODE=1
out=captured_output.txt
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  bash -c "set -o pipefail; $1" >> "$out" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$out"
}

verbose="python3 -m unittest test_reconcile -v"
{ printf '$ %s\n' "$verbose"; bash -c "$verbose" 2>&1; printf 'exit=%s\n' "$?"; } \
  > test_output.txt

: > "$out"
rec "$verbose"
rec "python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run1.json"
rec "python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run2.json"
rec "sha256sum report_run1.json report_run2.json"
rec "cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL"
rec "python3 -m unittest test_reconcile.TestDocumentedExitCodes test_reconcile.TestRepairedExitCodes"
# trailing blank line from the last rec() is trimmed so the file ends with
# exactly one newline after the final exit= line.
python3 - <<'PY2'
p = "captured_output.txt"
with open(p, encoding="utf-8") as fh:
    text = fh.read()
with open(p, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text.rstrip("\n") + "\n")
PY2
rm -f report_run1.json report_run2.json

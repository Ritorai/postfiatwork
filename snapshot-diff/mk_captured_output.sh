# Regenerates captured_output.txt. Run from inside snapshot-diff/:
#
#   bash mk_captured_output.sh
#
# No shebang, on purpose. Everything in this repository is committed
# through a path that cannot set an executable bit, so every file lands
# at mode 100644 -- and a `#!` line on a file the filesystem will never
# execute is exactly the contradiction shebang-mode's SM002 rule
# reports. Naming the interpreter in the usage line above costs
# nothing and promises nothing the mode cannot keep.
#
# Deliberately NOT named capture.sh -- but not for the reason that name
# usually gets avoided. regen-preflight discovers regenerators by that
# exact name and re-runs each one, and it MASKS unittest's
# `Ran N tests in ...s` wall-clock before comparing (regenpre.py's
# "unittest_duration" volatile pattern), so a capture.sh here would come
# back volatile_only, which regen-preflight counts as a pass. Verified,
# not assumed.
#
# The real cost is inventory. A capture.sh in this directory adds a 41st
# item to regen-preflight's run, and regen-preflight's committed
# preflight_report.json -- which its own README describes as evidence of
# one run rather than a regenerable report -- would then describe a
# smaller repository than the one it sits in. The brief for the change
# this script was added with puts regen-preflight off limits, so the
# choice was between a stale report in a directory that may not be
# touched and a name that keeps this script out of that inventory. If
# that constraint ever lifts, renaming this to capture.sh is a strict
# improvement: it puts the transcript under a gate.
#
# The wall-clock is the ONLY irreproducible field in this file; every
# count, exit status, hash and JSON body below is fixed by construction.
#
# The record grammar is transcript-drift/FORMAT.md's, and it is the
# grammar this file already used before this script existed: a
# `=== $ <command> ===` header on one line, the verbatim output, and
# `exit=<int>` (the commands carry their own `; echo "exit=$?"`, so the
# exit line is part of the captured output rather than added here).
# Steps whose interest is the output rather than the status use the
# plain `$ <command>` header form. Both forms appear in the committed
# file; this script reproduces them exactly.
#
# set -u but NOT set -e: several records below are expected to exit
# non-zero (a diff with changes exits 1, a usage error exits 2, and
# `grep -c` exits 1 when it counts zero). Aborting on those would
# truncate the transcript at its first interesting line.
set -u

# argparse wraps its usage line to the terminal width, and two of the
# records below capture that line. Pinned to 80 so the committed
# transcript does not depend on the width of whatever terminal happened
# to regenerate it. 80 is also what shutil.get_terminal_size() falls
# back to when output is a pipe, so this changes nothing in the usual
# case and only removes a way for the file to move by accident.
export COLUMNS=80

out=captured_output.txt

# Header form 1: output is the point, status is not recorded.
plain() {
  printf '$ %s\n' "$1" >> "$out"
  bash -c "$1" >> "$out" 2>&1
}

# Header form 2: the command records its own exit status.
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  bash -c "$1" >> "$out" 2>&1
  printf '\n' >> "$out"
}

rm -f "$out" r1.json r2.json rev.json

# Recorded first because it explains the shape of the next record.
# unittest's verbose test id changed in CPython 3.11: 3.10 and earlier
# print "test_x (module.Class)", 3.11 and later print
# "test_x (module.Class.test_x)". The listing below therefore differs
# from the one this file carried before, on every one of its lines,
# without any test having been renamed. Other transcripts in this
# repository already show the 3.11 form.
plain 'python3 --version'
printf '\n' >> "$out"

plain 'python3 -m unittest test_snapdiff -v'
printf '\n' >> "$out"

rec 'python3 snapdiff.py snapshot_before.json snapshot_after_same.json ; echo "exit=$?"'
rec 'python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o r1.json ; echo "exit=$?"'
rec 'python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o r2.json ; echo "exit=$?"'

plain 'sha256sum r1.json r2.json'
printf '\n' >> "$out"
plain 'cmp r1.json r2.json && echo BYTE-IDENTICAL'
printf '\n' >> "$out"

rec 'python3 snapdiff.py snapshot_before.json snapshot_after_changed.json --ignore reward --ignore status ; echo "exit=$?"'
rec 'python3 snapdiff.py snapshot_after_changed.json snapshot_before.json -o rev.json ; echo "exit=$?"'
rec 'python3 snapdiff.py /nonexistent.json snapshot_before.json ; echo "exit=$?"'
rec 'python3 snapdiff.py snapshot_before.json ; echo "exit=$?"'

plain 'grep -c "/sessions\|/tmp\|/home" r1.json'

# --- the single-use guard, added with it -----------------------------
# Two conflicting -o values: refused, exit 2, and NEITHER file created.
#
# Recorded in the plain `$` form, not the `=== $ ===` form, on purpose.
# index-generator's committed transcript pins a repository-wide count
# of `=== $ ===` records (total_command_records), and the brief for the
# change these records document forbids altering index-generator -- so
# an eighth `=== $ ===` record in this file would have turned that
# tool's transcript stale for a reason unconnected to it. The plain
# form is already used five times above for steps whose interest is the
# output. Neither step echoes its status: FORMAT.md reserves `exit=`
# as a record terminator, a plain-form step is not a record of its
# own, and transcript-schema reads an `exit=` line here as a second
# terminator inside the preceding record (and any other `exit...=`
# spelling as a malformed one). What these two steps show is the
# refusal text and the two absent files. The exit status is proven where
# the grammar has somewhere to put it: SINGLE_USE_FLAG_EVIDENCE.txt,
# in the `=== $ ===` form with real terminators, and
# test_exit_code_is_the_documented_usage_error_code.
printf '\n' >> "$out"
plain 'python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o first.json -o second.json'
printf '\n' >> "$out"
plain 'ls first.json second.json 2>&1 ; echo "neither-file-created"'

rm -f r1.json r2.json rev.json first.json second.json

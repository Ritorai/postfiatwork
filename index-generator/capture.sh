#!/usr/bin/env bash
# Regenerates captured_output.txt. Every record is a real run.
# Run from this directory:  bash capture.sh   (or: sh capture.sh -- see below)
#
# FIX (this task): the shared repo convention for rec() is
#
#     rec() {
#         printf '\n=== $ %s ===\n' "$*" >> "$OUT"
#         sh -c "$*" >> "$OUT" 2>&1
#         printf 'exit=%s\n' "$?" >> "$OUT"
#     }
#
# `sh -c` on a pipeline reports the LAST stage's exit status, not the
# first. That silently launders a failing `unittest` run's exit code
# through a downstream `grep`/`tail` filter -- see README.md, "Finding 2".
# This script fixes that two ways:
#   1. rec() is rewritten below to run under `bash -c` with
#      `set -o pipefail`, so ANY record's recorded exit= is the real
#      exit of the pipeline (the rightmost stage that actually failed, or
#      0 if every stage succeeded) -- not just the last stage's.
#      `/bin/sh` on this box is dash, which has no `set -o pipefail` at
#      all (verified: `sh -c 'set -o pipefail'` exits nonzero with
#      "Illegal option"), so this script requires bash and says so if it
#      is missing (see the interpreter check right after `set -u`).
#   2. No record whose command runs `unittest` is ALSO piped through a
#      filter. Where earlier versions of this file used
#      `... unittest ... | grep <name>` to spotlight specific tests, this
#      script instead runs exactly those tests directly via unittest's
#      own dotted test-selection syntax -- a real, unpiped, unfiltered
#      `unittest` invocation that produces its own genuine "Ran N tests"
#      line, verdict, and exit code. Where a filtered *view* is still
#      useful for readability, it operates on a file already written by a
#      complete, unpiped record -- never on unittest's live output -- and
#      is never the only evidence that a suite ran.
set -u

if ! command -v bash >/dev/null 2>&1; then
    echo "capture.sh: bash is required (for 'set -o pipefail'); not found on PATH" >&2
    exit 2
fi

cd "$(dirname "$0")" || exit 2
OUT=captured_output.txt

rec() {
    printf '\n=== $ %s ===\n' "$*" >> "$OUT"
    bash -c "set -o pipefail; $*" >> "$OUT" 2>&1
    printf 'exit=%s\n' "$?" >> "$OUT"
}

# --------------------------------------------------------------------------
# Scratch setup -- silent (not recorded), matching the rest of this
# repository's convention that a fixture's creation is prep, not evidence
# (see e.g. env-leak-scanner/capture.sh's "$CTL" setup, or this repo's own
# original committed transcript, which never recorded fixture_repo's
# initial creation either). Idempotent: removes any leftovers from a
# previous run first.
# --------------------------------------------------------------------------
rm -rf fixture_repo clean_repo badenc_repo p_a p_zzzzzzzzzz _rt_fixture_repo \
       det_run1_report.json det_run1_INDEX.md det_run2_report.json det_run2_INDEX.md \
       clean_report.json fixture_INDEX.md fixture_report.json \
       fixture_report_check1.json fixture_report_check2.json \
       missing_check_report.json missing_readme_report.json badenc_report.json \
       INDEX.md report.json _self_check_snapshot.txt _drift_snapshot.json

mkdir -p clean_repo/only_tool
printf '# only_tool\n\na single healthy tool\n\n1 test.\n' > clean_repo/only_tool/README.md
printf 'def main():\n    pass\n' > clean_repo/only_tool/only_tool.py
printf 'def test_x():\n    assert True\n' > clean_repo/only_tool/test_only_tool.py
printf 'ok\n' > clean_repo/only_tool/captured_output.txt

# --------------------------------------------------------------------------
cat > "$OUT" <<'PREAMBLE'
index-generator -- captured verification output
=================================================

Records follow transcript-drift/FORMAT.md: a "=== $ command ===" header,
the verbatim output, and an exit= line. Environment: CPython 3.11.15,
Linux x86_64, stdlib only, no network. All paths below are relative to
this directory on purpose -- no absolute path is recorded anywhere in
this file, which is also what makes the byte-identical relocation proof
possible (see README.md, "Relocation and determinism").

WHAT WAS BROKEN, AND HOW THIS FILE FIXES IT (see README.md for the full
writeup):

  Finding 1 -- a record whose command piped `unittest -v` through `grep`
  had NEITHER a "Ran N tests in ..." line NOR an OK/FAILED verdict,
  because grep filtered both out. transcript-schema/validate_transcript.py
  flagged this as TRANSCRIPT_RECORD_MISSING_RAN_LINE and
  TRANSCRIPT_RECORD_MISSING_VERDICT -- the only two such findings in the
  whole repository. Fixed: every unittest record below is a complete,
  unpiped, unfiltered run.

  Finding 2 -- the recorded exit= for that same record was grep's exit
  status, not unittest's. A failing suite piped through a grep that
  happens to match a PASSING test's name would have been recorded as
  exit=0. rec() below runs every record under `bash -c 'set -o pipefail;
  ...'`, so a pipeline's recorded exit is the real status of whichever
  stage actually failed. See test_capture.py's PipefailMaskingTests for a
  live, isolated reproduction of both the bug and the fix (a throwaway
  failing suite, piped through grep, with and without pipefail).

  Finding 3 -- this file used to contain BOTH "Ran 138 tests" (a stale
  count, left over from before two tests were added) and "Ran 140 tests"
  (the correct count at the time) for the same suite. Fixed: every
  full-suite record below is a live run made in this single invocation of
  this script, so they agree by construction; test_capture.py's
  RegeneratedTranscriptTests.test_full_suite_ran_counts_agree_across_records
  makes that an enforced invariant, not a one-time fix.

  Finding 4 (repo-wide, disclosed but NOT fixed here -- out of scope for
  this task) -- the shared rec() convention this repository's other
  capture.sh scripts still use pipes a command through `sh -c`, which has
  the same masking bug as Finding 2 for ANY recorded command containing a
  pipe, in any tool directory. See the pipe_scan.py record near the end
  of this file, and README.md, "Finding 4", for the count.

ON SELF-REFERENCE: two records near the end of this file
(transcript-schema/validate_transcript.py and transcript-drift/
driftcheck.py, run against THIS file) necessarily see a snapshot of this
transcript taken mid-write -- everything up to that point, not the whole
finished file -- because the record recording that very check cannot
contain its own completed self. See the note at that point in this file
for exactly what is excluded, and README.md, "Verification", for the
authoritative post-completion re-run.
PREAMBLE

rec "python3 --version"
rec "uname -sm"

printf '\n############################################################\n# 0. Build the fixture repo used by every CLI section below\n############################################################\n' >> "$OUT"
rec "python3 make_fixture_repo.py fixture_repo"

printf '\n############################################################\n# 1. Verbose test run\n############################################################\n' >> "$OUT"
rec "python3 -m unittest test_indexgen -v"

printf '\n############################################################\n# 2. CLI invocations -- clean repo (expect exit 0)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root clean_repo -o clean_report.json"
rec "cat clean_report.json"

printf '\n############################################################\n# 3. CLI invocations -- fixture repo with root-readme cross-check (expect exit 1)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root fixture_repo --root-readme root_readme_sample.md --write-index fixture_INDEX.md -o fixture_report.json"
rec "cat fixture_INDEX.md"
rec "cat fixture_report.json"

printf '\n############################################################\n# 4. --check-index: no drift against the index just written (expect: no INDEX_DRIFT findings)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root fixture_repo --check-index fixture_INDEX.md -o fixture_report_check1.json"
rec "python3 -c \"import json; d=json.load(open('fixture_report_check1.json')); print([f for f in d['findings'] if f['code']=='INDEX_DRIFT'])\""

printf '\n############################################################\n# 5. --check-index: introduce real drift (add a 7th tool), re-check (expect INDEX_DRIFT)\n############################################################\n' >> "$OUT"
rec "mkdir -p fixture_repo/eta"
rec "printf 'def main():\\n    pass\\n' > fixture_repo/eta/eta.py"
rec "printf 'def test_x():\\n    assert True\\n' > fixture_repo/eta/test_eta.py"
rec "printf '# eta - a newly added tool\\n\\n2 tests.\\n' > fixture_repo/eta/README.md"
rec "printf 'ok\\n' > fixture_repo/eta/captured_output.txt"
rec "python3 indexgen.py --root fixture_repo --check-index fixture_INDEX.md -o fixture_report_check2.json"
rec "python3 -c \"import json; d=json.load(open('fixture_report_check2.json')); print([f for f in d['findings'] if f['code']=='INDEX_DRIFT'])\""
rec "rm -rf fixture_repo/eta"

printf '\n############################################################\n# 6. Error path -- bad --root (expect exit 2)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root does_not_exist_at_all"

printf '\n############################################################\n# 7. Error path -- missing required --root argument (expect exit 2)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py"

printf '\n############################################################\n# 8. Error path -- unknown CLI flag (expect exit 2)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root fixture_repo --not-a-real-flag"

printf '\n############################################################\n# 9. Error path -- unwritable --write-index target directory (expect exit 2)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root fixture_repo --write-index no_such_dir/INDEX.md"

printf '\n############################################################\n# 10. Non-fatal error path -- missing --check-index file (reported as UNREADABLE_FILE, not fatal)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root clean_repo --check-index does_not_exist_index.md -o missing_check_report.json"
rec "python3 -c \"import json; d=json.load(open('missing_check_report.json')); print(d['findings'])\""

printf '\n############################################################\n# 11. Non-fatal error path -- missing --root-readme file (reported as UNREADABLE_FILE, not fatal)\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root clean_repo --root-readme does_not_exist_readme.md -o missing_readme_report.json"
rec "python3 -c \"import json; d=json.load(open('missing_readme_report.json')); print(d['findings'])\""

printf '\n############################################################\n# 12. Non-fatal error path -- malformed (invalid utf-8) README skipped, not fatal\n############################################################\n' >> "$OUT"
rec "mkdir -p badenc_repo/weird"
rec "printf 'def main():\\n    pass\\n' > badenc_repo/weird/weird.py"
rec "printf 'def test_x():\\n    assert True\\n' > badenc_repo/weird/test_weird.py"
rec "printf 'ok\\n' > badenc_repo/weird/captured_output.txt"
rec "python3 -c \"open('badenc_repo/weird/README.md','wb').write(b'\\xff\\xfe not valid utf8 \\x80\\x81')\""
rec "python3 indexgen.py --root badenc_repo -o badenc_report.json"
rec "python3 -c \"import json; d=json.load(open('badenc_report.json')); print([f for f in d['findings'] if f['code']=='UNREADABLE_FILE'])\""
rec "rm -rf badenc_repo"

printf '\n############################################################\n# 13. DETERMINISM PROOF -- same dir, two independent runs\n############################################################\n' >> "$OUT"
rec "python3 indexgen.py --root fixture_repo --root-readme root_readme_sample.md --write-index det_run1_INDEX.md -o det_run1_report.json"
rec "python3 indexgen.py --root fixture_repo --root-readme root_readme_sample.md --write-index det_run2_INDEX.md -o det_run2_report.json"
rec "sha256sum det_run1_report.json det_run2_report.json det_run1_INDEX.md det_run2_INDEX.md"

printf '\n############################################################\n# 14. RELOCATION PROOF (indexgen.py itself) -- copy fixture to a different absolute path with a different name, run again\n############################################################\n' >> "$OUT"
rec "rm -rf p_a p_zzzzzzzzzz"
rec "mkdir -p p_a p_zzzzzzzzzz"
rec "cp -r fixture_repo p_a/fx"
rec "cp -r fixture_repo p_zzzzzzzzzz/fx_totally_different_name"
rec "python3 indexgen.py --root p_a/fx --root-readme root_readme_sample.md --write-index p_a/INDEX.md -o p_a/report.json"
rec "python3 indexgen.py --root p_zzzzzzzzzz/fx_totally_different_name --root-readme root_readme_sample.md --write-index p_zzzzzzzzzz/INDEX.md -o p_zzzzzzzzzz/report.json"
rec "sha256sum p_a/report.json p_zzzzzzzzzz/report.json p_a/INDEX.md p_zzzzzzzzzz/INDEX.md"
rec "sha256sum det_run1_report.json det_run2_report.json p_a/report.json p_zzzzzzzzzz/report.json det_run1_INDEX.md det_run2_INDEX.md p_a/INDEX.md p_zzzzzzzzzz/INDEX.md"
rm -rf p_a p_zzzzzzzzzz

printf '\n############################################################\n# 15. Sanity check -- no forbidden wall-clock substrings, no absolute paths leaked\n############################################################\n' >> "$OUT"
rec "grep -n 'time\\.time\\|utcnow\\|now()' indexgen.py; echo grep_exit=\$?"
rec "grep -c '/tmp/build_indexgen' det_run1_report.json det_run1_INDEX.md; echo grep_exit=\$?"

printf '\n############################################################\n# END OF TRANSCRIPT (main CLI proof sections)\n############################################################\n' >> "$OUT"

cat >> "$OUT" <<'NOTE1'

--- fixture regeneration round trip ---
fixture_repo/ is not committed as loose files; it is generated by
make_fixture_repo.py, which stores contents base64-encoded and writes
them in binary mode so the deliberate CRLF fixture survives. The round
trip below regenerates it AGAIN into a second, differently-named relative
directory and diffs the two trees for real -- this is a genuine `diff -r`
run, not a placeholder.

A note on a bug in this generator, caught and fixed before commit: the
first version stored the fixture as TEXT. That silently converted
zeta/README.md from CRLF to LF, destroying the line-ending test case -
while STILL producing matching report and index hashes, because indexgen
normalises line endings when it extracts a description. The hashes
agreeing is exactly why it was nearly missed; only diff -r on the trees
caught it. The generator now stores base64 and writes binary, and
make_fixture_repo.py carries a comment saying not to simplify it back.
NOTE1

rec "python3 make_fixture_repo.py _rt_fixture_repo"
rec "diff -r fixture_repo _rt_fixture_repo"
rec "python3 indexgen.py --root fixture_repo --write-index INDEX.md --root-readme root_readme_sample.md -o report.json"
rec "sha256sum report.json sample_report.json INDEX.md sample_INDEX.md"
rm -rf _rt_fixture_repo INDEX.md report.json

cat >> "$OUT" <<'NOTE2'

--- regression coverage: the stale-catalogued-test-count fix (Findings 1-3) ---
Earlier versions of this file spotlighted these two regression tests with
`python3 -m unittest test_indexgen -v 2>&1 | grep stale_catalogued`,
which lost the Ran/verdict lines to the filter and recorded grep's exit
status instead of unittest's (Findings 1 and 2). Below, the same two
tests are run DIRECTLY via unittest's own dotted test-selection syntax --
no pipe, no filter -- which is a real, complete, unpiped unittest
invocation with its own genuine summary and exit code. A second,
independent full-suite run follows to reinforce (not just assert once)
that the test count is stable within this file, replacing the old
`... | tail -3` record, which had the same masking problem.
NOTE2

rec "python3 -m unittest test_indexgen.RunEndToEndTests.test_stale_catalogued_test_count_alone_exits_1_end_to_end test_indexgen.RunEndToEndTests.test_stale_catalogued_test_count_exits_1_via_subprocess -v"
rec "python3 -m unittest test_indexgen"

cat >> "$OUT" <<'NOTE3'

--- regression coverage: the transcript-generation path itself ---
test_capture.py covers the fix directly: a complete passing summary, a
complete failing summary (built in a throwaway temp-dir suite), the
pipefail mechanism itself (with and without it, plus a minimal
`false | true` case independent of unittest), and pipe_scan.py's own
logic against synthetic fixtures. Run here is every class in that module
EXCEPT RegeneratedTranscriptTests and ExternalCheckerCleanTests -- those
two read the actual committed captured_output.txt (this file) and run
transcript-schema/driftcheck against it by its real on-disk name, which
this very record cannot do without the same self-reference paradox noted
above (the file cannot check its own not-yet-written ending). Both
classes run as part of the normal `python3 -m unittest test_capture -v`
invocation against the FINISHED file -- see README.md, "Verification",
for that run's real output.
NOTE3

rec "python3 -m unittest test_capture.PassingSummaryTests test_capture.FailingSummaryTests test_capture.PipefailMaskingTests test_capture.PipeScanTests -v"

cat >> "$OUT" <<'NOTE4'

--- Finding 4 disclosure (repo-wide, read-only; NOT fixed here -- see README.md) ---
pipe_scan.py counts, per tool directory, how many committed
captured_output.txt command records contain a pipe in their header -- the
same masking bug as Finding 2, generalised. This run scans the whole
repository, INCLUDING this very directory's own captured_output.txt --
which, at the moment this record is produced, is this file, mid-write,
via bash's own open file description for $OUT; the count for
"index-generator" in this particular invocation is therefore a partial,
self-referential snapshot, not the finished file's count. The finished
file's real count (0 piped unittest records, by construction) is stated
in README.md, "Finding 4", computed by running this same command again
after this file is complete.
NOTE4

rec "python3 pipe_scan.py --repo-root .."

cat >> "$OUT" <<'NOTE5'

--- self-check: transcript-schema/validate_transcript.py against a frozen snapshot ---
Checking THIS file while it is still being appended to would mean the
record recording the check can never include itself (see "ON
SELF-REFERENCE" in the preamble). To sidestep that without faking
anything, a snapshot is taken first (a real `cp`, not a hand edit) and
the checker runs against the frozen copy; the FINAL authoritative
validation, run against the completed file after this script exits, is
reported in README.md, "Verification".
NOTE5

cp "$OUT" _self_check_snapshot.txt
rec "python3 ../transcript-schema/validate_transcript.py _self_check_snapshot.txt"
rm -f _self_check_snapshot.txt

cat >> "$OUT" <<'NOTE6'

--- self-check: transcript-drift/driftcheck.py, repo-wide, filtered to this tool ---
Same self-reference caveat as immediately above applies (driftcheck.py
reads the actual file at the time it runs, not a snapshot, since it
compares many directories at once and none of the others need freezing).
NOTE6

rec "python3 ../transcript-drift/driftcheck.py --root .. -o _drift_snapshot.json"
rec "python3 -c \"import json; r=json.load(open('_drift_snapshot.json')); print([f for f in r['findings'] if f['tool']=='index-generator'])\""
rm -f _drift_snapshot.json

printf '\n############################################################\n# END OF TRANSCRIPT\n############################################################\n' >> "$OUT"

# --------------------------------------------------------------------------
# Cleanup -- leave only the committed files behind.
# --------------------------------------------------------------------------
rm -rf fixture_repo clean_repo badenc_repo p_a p_zzzzzzzzzz _rt_fixture_repo \
       det_run1_report.json det_run1_INDEX.md det_run2_report.json det_run2_INDEX.md \
       clean_report.json fixture_INDEX.md fixture_report.json \
       fixture_report_check1.json fixture_report_check2.json \
       missing_check_report.json missing_readme_report.json badenc_report.json \
       INDEX.md report.json _self_check_snapshot.txt _drift_snapshot.json \
       __pycache__

echo "capture.sh: wrote $OUT"

# Regenerates STALE_NUMBER_EVIDENCE.txt. Run it as:
#
#     bash mk_stale_evidence.sh
#
# No shebang on purpose. Files land at mode 100644 through GitHub's web
# upload, and this repository's shebang-mode scanner counts a shebang on a
# file that is not executable as a finding; every script here is invoked
# through its interpreter instead.
#
# Steps are written in the plain `$ ` form for readability. What keeps
# this file out of index-generator's pinned record count is the file NAME:
# pipe_scan.py and pipe_classify.py open one filename per tool directory,
# captured_output.txt, so nothing written here is counted whatever form it
# takes. A record added to captured_output.txt would be, and would make an
# untouched tool's committed report stale.
#
# Two lines of the output are volatile: the unittest duration, and the
# python3 --version line on a different interpreter. PARENT below is the
# commit the committed copy was produced against, not a reading of the
# checkout.
#
# LC_ALL and COLUMNS are pinned so a different locale or terminal width
# cannot change the bytes. PYTHONDONTWRITEBYTECODE keeps __pycache__ out of
# the tree; PYTHONUNBUFFERED is cleared because it reorders interleaved
# stdout and stderr.
set -u
export LC_ALL=C
export COLUMNS=80
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONUNBUFFERED

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/STALE_NUMBER_EVIDENCE.txt
PARENT=${PARENT:-4cc433950c5b7a4dd5d657efa22e84c4fe5dbbc6}

# The scratch tree is made by mktemp and removed by name at the end. No
# path from it is ever printed: this file is scanned by env-leak-scanner,
# and a temporary directory path would register as a leak.
WORK=$(mktemp -d)

step() { printf '\n$ %s\n' "$*" >> "$OUT"; }
scratch() { (cd "$WORK" && python3 "$1" >> "$OUT" 2>&1; printf 'exit=%d\n' "$?" >> "$OUT"); }
check() { (cd "${1:-$WORK}" && python3 check_counts.py ${2:-} >> "$OUT" 2>&1
           printf 'exit=%d\n' "$?" >> "$OUT"); }
reset_scratch() {
  cp "$HERE"/check_counts.py "$HERE"/events_ok.json "$HERE"/events_breach.json \
     "$HERE"/report_ok.json "$HERE"/report_breach_run1.json \
     "$HERE"/report_breach_run2.json "$WORK"/
}

{
  printf 'Stale-number check evidence for throughput-reporter\n'
  printf 'Parent commit: %s\n' "$PARENT"
  printf '\n'
  printf 'Rebuild with: bash mk_stale_evidence.sh\n'
  printf 'Volatile on a rebuild: the unittest duration, and the python3\n'
  printf 'version line on a different interpreter. Everything else is\n'
  printf 'byte-stable. See mk_stale_evidence.sh for why the steps use the\n'
  printf 'plain `$ ` form rather than the `=== $ ... ===` record form.\n'
  printf '\n'
  printf 'Every edit below is made to a scratch copy that mktemp created\n'
  printf 'and this script removes. The committed files are read, never\n'
  printf 'written; the last check re-runs against them to show it.\n'
} > "$OUT"

step "python3 --version"
python3 --version >> "$OUT" 2>&1

step "python3 check_counts.py            # the committed tree, untouched"
check "$HERE"

reset_scratch
step "python3 stale_one_count.py         # scratch copy: bob rewarded 1 -> 99"
cat > "$WORK/stale_one_count.py" <<'PY'
import json
report = json.load(open("report_ok.json", encoding="utf-8"))
for entry in report["contributors"]:
    if entry["contributor"] == "bob":
        entry["counts"]["rewarded"] = 99
with open("report_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
print("edited report_ok.json: contributors[bob].counts.rewarded = 99")
PY
scratch stale_one_count.py

step "python3 check_counts.py            # same scratch copy"
check

reset_scratch
step "python3 stale_the_fixture.py       # instead: drop bob's refusal from the fixture"
cat > "$WORK/stale_the_fixture.py" <<'PY'
import json
events = json.load(open("events_ok.json", encoding="utf-8"))
kept = [e for e in events
        if not (e["contributor"] == "bob" and e["state"] == "refused")]
with open("events_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(kept, sort_keys=True, separators=(",", ":")) + "\n")
print("dropped %d of %d events; the report is now the stale side"
      % (len(events) - len(kept), len(events)))
PY
scratch stale_the_fixture.py

step "python3 check_counts.py            # the report is now the stale side"
check

reset_scratch
step "python3 drift_a_timestamp.py       # instead: move alice's submits a month later"
cat > "$WORK/drift_a_timestamp.py" <<'PY'
import json
events = json.load(open("events_ok.json", encoding="utf-8"))
moved = 0
for event in events:
    if event["contributor"] == "alice" and event["state"] == "submitted":
        event["occurred_at"] = "2026-08-30T09:00:00Z"
        moved += 1
with open("events_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(events, sort_keys=True, separators=(",", ":")) + "\n")
print("moved %d submitted timestamps; every count is unchanged" % moved)
PY
scratch drift_a_timestamp.py

step "python3 check_counts.py            # no count moved; the grades did"
check

reset_scratch
step "python3 duplicate_a_contributor.py # one name twice, the first copy tampered"
cat > "$WORK/duplicate_a_contributor.py" <<'PY'
import json
report = json.load(open("report_ok.json", encoding="utf-8"))
twin = json.loads(json.dumps(report["contributors"][0]))
twin["counts"]["rewarded"] = 999
report["contributors"].insert(0, twin)
with open("report_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
print("inserted a second entry for %s" % twin["contributor"])
PY
scratch duplicate_a_contributor.py

step "python3 check_counts.py            # keying by name alone would have missed this"
check

reset_scratch
step "python3 add_an_unknown_value.py    # a value the recomputation does not produce"
cat > "$WORK/add_an_unknown_value.py" <<'PY'
import json
report = json.load(open("report_breach_run1.json", encoding="utf-8"))
report["totals"]["brand_new"] = 7
report["contributors"][0]["counts"]["surprise"] = 3
with open("report_breach_run1.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
print("added totals.brand_new and one contributor counts.surprise")
PY
scratch add_an_unknown_value.py

step "python3 check_counts.py            # UNEXPECTED, not silently passed"
check

reset_scratch
step "python3 break_the_fixture.py       # a state the tool itself refuses"
cat > "$WORK/break_the_fixture.py" <<'PY'
import json
events = json.load(open("events_ok.json", encoding="utf-8"))
events[0]["state"] = "queued"
with open("events_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(events, sort_keys=True, separators=(",", ":")) + "\n")
print("set events[0].state to an unknown value")
PY
scratch break_the_fixture.py

step "python3 check_counts.py            # exit 2, not a wall of STALE lines"
check

reset_scratch
step "python3 retag_the_flags.py         # config claims flags the command never passed"
cat > "$WORK/retag_the_flags.py" <<'PY'
import json
report = json.load(open("report_ok.json", encoding="utf-8"))
report["config"] = {"min_tasks": 1, "refusal_ceiling": 0.99}
with open("report_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
print("rewrote config to min_tasks=1 refusal_ceiling=0.99")
PY
scratch retag_the_flags.py

step "python3 check_counts.py            # pinned to the documented rerun, not read back"
check

reset_scratch
step "python3 pretty_print_it.py         # every value right, the bytes wrong"
cat > "$WORK/pretty_print_it.py" <<'PY'
import json
report = json.load(open("report_ok.json", encoding="utf-8"))
with open("report_ok.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
print("re-indented report_ok.json; no value changed")
PY
scratch pretty_print_it.py

step "python3 check_counts.py            # FORMAT: it would not reproduce byte for byte"
check

step "python3 check_counts.py nowhere    # a directory that does not exist"
check "$WORK" nowhere

step "python3 check_counts.py            # the committed tree, unchanged by any of this"
check "$HERE"

step "python3 -m unittest test_check_counts"
(cd "$HERE" && python3 -m unittest test_check_counts >> "$OUT" 2>&1
 printf 'exit=%d\n' "$?" >> "$OUT")

rm -rf "$WORK"

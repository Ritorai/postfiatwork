#!/bin/sh
# Regenerates captured_output.txt. Every record is a real run.
# Run from this directory:  sh capture.sh
set -u
OUT=captured_output.txt

rec() {
    printf '\n=== $ %s ===\n' "$*" >> "$OUT"
    sh -c "$*" >> "$OUT" 2>&1
    printf 'exit=%s\n' "$?" >> "$OUT"
}

cat > "$OUT" <<'PREAMBLE'
env-leak-scanner -- captured verification output
================================================

Records follow transcript-drift/FORMAT.md: a "=== $ command ===" header,
the verbatim output, and an exit= line.

SCRATCH PATHS ARE RELATIVE ON PURPOSE. A scanner that hunts absolute paths
must not write any into its own transcript, so the positive-control tree is
"_control" relative to this directory and is deleted by the same script.

WHAT THIS TRANSCRIPT NECESSARILY CONTAINS: the repository-wide report quotes
the leaked strings it found, because a finding you cannot see is not a
finding. Re-running the scanner after this commit therefore reports
env-leak-scanner's own files. That is expected and is stated in README.md.
PREAMBLE

rec "python3 --version"
rec "uname -sm"
rec "python3 -m unittest test_leakscan"

# ---- positive control, one planted category at a time -----------------
CTL=_control
rm -rf "$CTL"; mkdir -p "$CTL"
printf 'Nothing to see here.\nSee https://github.com/Ritorai/postfiatwork\nedit a/b/c.py now\n' > "$CTL/README.md"
rec "python3 leakscan.py --root $CTL"

plant() {
    printf '%s\n' "$2" >> "$CTL/README.md"
    rec "python3 leakscan.py --root $CTL"
}
plant absolute_path  "output went to /opt/build/out.json"
plant home_directory "saved to /home/rito/out.json"
plant temp_directory "wrote /tmp/a.json"
plant hostname       "curl http://localhost:8080/x"
rm -rf "$CTL"

rec "python3 leakscan.py --root /nonexistent-directory-xyz"

# ---- prefilter equivalence -------------------------------------------
rec "python3 -m unittest test_leakscan.TestFullScanEqualsCandidateScan -v"

# ---- the repository-wide run -----------------------------------------
rec "python3 leakscan.py --scan-candidates candidates_repo.json -o raw_scan.json"
rec "python3 leakscan.py --scan-candidates candidates_repo.json --review review.json -o leak_report_2026-08-04.json"
rec "python3 -c \"import json;r=json.load(open('leak_report_2026-08-04.json'));print('confirmed',r['counts']['confirmed'],'benign',r['counts']['benign']);print(r['counts']['by_category']);print('stale review entries',len(r['stale_review_entries']))\""
rec "python3 -c \"import json,collections;r=json.load(open('leak_report_2026-08-04.json'));c=collections.Counter((f['file'],f['category']) for f in r['confirmed_leaks']);[print('%-4d %-14s %s'%(v,k[1],k[0])) for k,v in sorted(c.items())]\""
rec "python3 -c \"import json;r=json.load(open('leak_report_2026-08-04.json'));[print(f['file'],f['line'],f['rule'],repr(f['matched'])) for f in r['confirmed_leaks'] if 'weak-assertion-scanner' in f['file']]\""

# ---- determinism: both runs inside ONE record, nothing appended between
rec "python3 leakscan.py --scan-candidates candidates_repo.json --review review.json -o _d1.json; python3 leakscan.py --scan-candidates candidates_repo.json --review review.json -o _d2.json; cmp _d1.json _d2.json && echo BYTE_IDENTICAL; rm -f _d1.json _d2.json"
rec "python3 -c \"import hashlib;print(hashlib.sha256(open('leak_report_2026-08-04.json','rb').read()).hexdigest(),' leak_report_2026-08-04.json')\""

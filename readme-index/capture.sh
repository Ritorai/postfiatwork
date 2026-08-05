#!/bin/sh
# Regenerates captured_output.txt. Run from inside readme-index/.
# Every path here is RELATIVE on purpose: this repo ships an environment-leak
# scanner and a transcript that hard-codes an absolute path is a finding.
out=captured_output.txt
mkdir -p _scratch
rec() {
  printf '=== $ %s ===\n' "$1" >> "$out"
  sh -c "$1" >> "$out" 2>&1
  printf 'exit=%s\n\n' "$?" >> "$out"
}
cat > "$out" <<'HDR'
readme-index -- captured verification output
============================================

Records follow transcript-drift/FORMAT.md: a "=== $ command ===" header, the
verbatim output, and an exit= line. All paths are relative.

The before/after pair is the point of this transcript: the reconciliation run
against the committed root README exits 1 with differences, and the same
reconciliation against the regenerated README exits 0.

HDR
rec "python3 --version"
rec "uname -sm"
rec "python3 -m unittest test_readmeindex"
rec "python3 readmeindex.py --corpus corpus.tsv -o _scratch/discard.json"
rec "python3 -c \"import json;r=json.load(open('index_report.json'));print(json.dumps(r['totals'],sort_keys=True))\""
rec "python3 readmeindex.py --corpus corpus.tsv --root-readme root_readme_before.md -o _scratch/discard.json"
rec "python3 -c \"import json,collections;r=json.load(open('index_report.json'));c=collections.Counter(d['kind'] for d in r['index_differences']);print(sorted(c.items()));print([d for d in r['index_differences'] if d['kind']=='aggregate_differs'])\""
rec "python3 -c \"import json;r=json.load(open('index_report.json'));print('count_differs:',sum(1 for d in r['index_differences'] if d['kind']=='count_differs'))\""
rec "python3 -c \"import json;r=json.load(open('index_report.json'));print([(t['tool'],t['status']) for t in r['tools'] if t['status']!='claim'])\""
rec "python3 readmeindex.py --corpus corpus.tsv --root-readme root_readme_before.md --rewrite _scratch/after.md -o _scratch/discard.json"
rec "python3 readmeindex.py --corpus corpus.tsv --root-readme root_readme_after.md -o _scratch/discard.json"
rec "python3 readmeindex.py --corpus corpus.tsv --root-readme root_readme_after.md --rewrite _scratch/after2.md -o _scratch/discard.json"
rec "cmp _scratch/after.md root_readme_after.md && echo REWRITE_MATCHES_COMMITTED"
rec "cmp _scratch/after2.md root_readme_after.md && echo IDEMPOTENT_BYTE_IDENTICAL"
rec "python3 readmeindex.py --root does-not-exist"
rec "python3 readmeindex.py --corpus corpus.tsv --rewrite _scratch/x.md"
rec "python3 -c \"import hashlib;print(hashlib.sha256(open('index_report.json','rb').read()).hexdigest(),' index_report.json')\""
rec "python3 -c \"import hashlib;print(hashlib.sha256(open('corpus.tsv','rb').read()).hexdigest(),' corpus.tsv')\""
rec "wc -l corpus.tsv"
rm -rf _scratch

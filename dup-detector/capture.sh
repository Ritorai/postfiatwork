#!/usr/bin/env bash
# Targeted pipefail-masking fix for ONE record in captured_output.txt.
#
# Reuses index-generator/capture.sh's fix (read that file's header comment
# first). This directory ships no capture script of its own -- the
# original captured_output.txt was produced by some ad hoc process, not
# committed here -- so this script does not "restore" a prior script; it
# is added by this task, scoped to the ONE affected record, to make that
# record reproducible and safe from exit-masking going forward. It does
# not regenerate the rest of the transcript (out of scope; see
# LIMITATIONS.md in this delivery).
#
# THE BUG: the original record's header was
#     cat report_run1.json | head -c 300; echo '   ...[truncated for display; full file is report_run1.json]'
# This is TWO statements, not one pipeline: `cat X | head -c 300`, then an
# unconditional `echo '<fixed label>'`. Whatever harness produced this
# transcript ran the whole compound via `sh -c "$*"` and recorded ITS
# exit status afterward -- which is always the LAST statement executed,
# the `echo`, whose own exit is unconditionally 0. `set -o pipefail`
# alone does NOT fix this shape: pipefail only changes the exit status of
# `cat | head` itself, but that status is immediately discarded the
# instant the following `echo` statement runs and exits 0 on its own.
# Verified live: even `bash -c 'set -o pipefail; cat missing.json | head
# -c 300; echo label'` still exits 0 when `missing.json` does not exist
# (see test_capture_fix.py in this directory, TestDecorativeEchoDefeats
# Pipefail). So if `report_run1.json` had been missing or unreadable at
# capture time, this record would have recorded exit=0 regardless.
#
# THE FIX: restructure to a single process with no pipe and no trailing
# statement that could reset $? -- a small Python one-liner that reads
# and truncates the file itself. If the file is missing or unreadable,
# THIS SAME process raises and exits nonzero; there is no second process
# whose independent success could hide that. No filter, no masking
# possible by construction (this mirrors index-generator's Finding 1 fix:
# don't pipe a fallible step through something that can succeed on its
# own).
#
# report_run1.json is not committed as a loose file (it is itself the
# output of an earlier record, `dupdetect.py records_dupes.json -o
# report_run1.json`); it is regenerated here as unrecorded prep, matching
# index-generator/capture.sh's own convention that fixture creation is
# prep, not evidence.
set -u

if ! command -v bash >/dev/null 2>&1; then
    echo "capture.sh: bash is required (for 'set -o pipefail'); not found on PATH" >&2
    exit 2
fi

cd "$(dirname "$0")" || exit 2
OUT=captured_output.txt
OLD_HEADER="cat report_run1.json | head -c 300; echo '   ...[truncated for display; full file is report_run1.json]'"
NEW_HEADER="python3 -c \"d=open('report_run1.json',encoding='utf-8').read(); print(d[:300]+'   ...[truncated for display; full file is report_run1.json]')\""

# Unrecorded prep: regenerate the artifact this record peeks into.
python3 dupdetect.py records_dupes.json -o report_run1.json >/dev/null 2>&1

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

bash -c "set -o pipefail; $NEW_HEADER" > "$BODY_FILE" 2>&1
REAL_EXIT=$?

python3 - "$OUT" "$OLD_HEADER" "$NEW_HEADER" "$BODY_FILE" "$REAL_EXIT" <<'PYEOF'
import sys

out_path, old_header, new_header, body_file, exit_val = sys.argv[1:6]

with open(out_path, encoding="utf-8") as fh:
    lines = fh.readlines()

old_header_line = "=== $ %s ===\n" % old_header
new_header_line = "=== $ %s ===\n" % new_header

start = None
for i, line in enumerate(lines):
    if line == old_header_line:
        start = i
        break
if start is None:
    sys.exit("capture.sh: old header not found verbatim -- refusing to guess: %r" % old_header_line)

import re as _re
EXIT_RE = _re.compile(r"^exit=-?\d+\s*$")

next_header = len(lines)
for j in range(start + 1, len(lines)):
    if lines[j].startswith("=== $ "):
        next_header = j
        break

# Stop at THIS record's own first exit= line, not at EOF/next-header --
# otherwise any un-headered trailing prose after the record (this file
# has some) gets silently deleted along with the record it's replacing.
end = next_header
for j in range(start + 1, next_header):
    if EXIT_RE.match(lines[j]):
        end = j + 1
        break

# If a blank separator line immediately followed the old record (which
# is true at every record boundary in this file, including right before
# EOF in some of them), keep exactly one blank line after the new record
# too -- and no more, so any trailing prose beyond it is preserved
# untouched.
had_blank_after = end < len(lines) and lines[end] == "\n"
if had_blank_after:
    end += 1

with open(body_file, encoding="utf-8") as fh:
    body = fh.read()
if not body.endswith("\n"):
    body += "\n"

trailing_sep = ["\n"] if had_blank_after else []
new_block = [new_header_line] + body.splitlines(keepends=True) + ["exit=%s\n" % exit_val] + trailing_sep

lines[start:end] = new_block

with open(out_path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)

print("capture.sh: replaced 1 record (%r -> %r), real exit=%s" % (old_header, new_header, exit_val))
PYEOF
SPLICE_EXIT=$?

rm -f report_run1.json
exit "$SPLICE_EXIT"

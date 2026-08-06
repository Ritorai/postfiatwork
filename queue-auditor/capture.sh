#!/usr/bin/env bash
# Targeted pipefail-masking fix for TWO records in captured_output.txt.
#
# Reuses index-generator/capture.sh's fix (read that file's header comment
# first). This directory ships no capture script of its own -- the
# original captured_output.txt was produced by some ad hoc process, not
# committed here -- so this script does not "restore" a prior script; it
# is added by this task, scoped to the two affected records, to make them
# reproducible and safe from exit-masking going forward. It does not
# regenerate the rest of the transcript (out of scope; see
# LIMITATIONS.md in this delivery).
#
# Both records deliberately exercise the CLI's documented "read from
# stdin via -" code path (`cat snapshot_dirty.json | ...` and
# `echo '{not json' | ...`), so unlike dup-detector's record there is no
# single-process rewrite that tests the same thing -- the pipe itself is
# the point. Both embed `; echo "exit=$?"` IN the command text (this
# directory's own convention -- no outer harness trailer follows, same
# shape as payload-validator/capture.sh, whose header comment explains in
# full why only the execution wrapper -- not the recorded command text --
# needs to change: `; echo "exit=$?"` is the very next statement after
# the pipe, so $? at that point already reflects the pipeline's status
# exactly as bash computes it, and pipefail is the only missing
# ingredient.
#
# The `cat snapshot_dirty.json | ...` record is the one with real
# masking risk (a missing/unreadable snapshot file would let `python3
# queue_audit.py -` still run against empty stdin and exit on its own).
# The `echo '{not json' | ...` record's first stage is a literal `echo`,
# which cannot realistically fail -- it is included here anyway, for
# uniform treatment: this task fixes every genuine pipe it finds, not
# just the ones with an observed live failure.
set -u

if ! command -v bash >/dev/null 2>&1; then
    echo "capture.sh: bash is required (for 'set -o pipefail'); not found on PATH" >&2
    exit 2
fi

cd "$(dirname "$0")" || exit 2
OUT=captured_output.txt

replace_record() {
    local header="$1"
    local body_file
    body_file="$(mktemp)"
    bash -c "set -o pipefail; $header" > "$body_file" 2>&1
    python3 - "$OUT" "$header" "$body_file" <<'PYEOF'
import sys

out_path, header, body_file = sys.argv[1:4]

with open(out_path, encoding="utf-8") as fh:
    lines = fh.readlines()

header_line = "=== $ %s ===\n" % header

start = None
for i, line in enumerate(lines):
    if line == header_line:
        start = i
        break
if start is None:
    sys.exit("capture.sh: header not found verbatim -- refusing to guess: %r" % header_line)

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
new_block = [header_line] + body.splitlines(keepends=True) + trailing_sep

lines[start:end] = new_block

with open(out_path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)

print("capture.sh: re-ran and replaced 1 record under pipefail: %r" % header)
PYEOF
    local splice_exit=$?
    rm -f "$body_file"
    return "$splice_exit"
}

replace_record 'cat snapshot_dirty.json | python3 queue_audit.py - ; echo "exit=$?"' || exit $?
replace_record "echo '{not json' | python3 queue_audit.py - ; echo \"exit=\$?\"" || exit $?

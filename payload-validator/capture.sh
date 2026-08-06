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
# THE RECORD: `cat payloads_bad.json | python3 payload_validate.py - ;
# echo "exit=$?"` deliberately exercises the CLI's documented "read from
# stdin via -" code path, so unlike dup-detector's record this one keeps
# a real, meaningful two-process pipe -- there is no single-process
# rewrite that tests the same thing. The `; echo "exit=$?"` is written
# INTO the command itself (this directory's own convention -- no outer
# harness trailer follows it, unlike commit-claim-auditor/transcript-
# schema/dup-detector above). Because that echo is the very next
# statement after the pipe, $? at the point it runs is already exactly
# the pipeline's status as bash computes it -- which means the ONLY thing
# that has to change to fix the masking is whether pipefail is in effect
# BEFORE the pipe runs, not the command text itself.
#
# THE BUG, verified live: `sh -c 'cat missing.json | python3 -c "..." ;
# echo "exit=$?"'` prints exit=0 when `missing.json` does not exist,
# because the recorded exit is `python3`'s (the pipe's last stage, which
# still runs successfully on empty/error stdin), not `cat`'s. THE FIX,
# also verified live: running the identical command text under
# `bash -c 'set -o pipefail; ...'` instead makes that same failing-cat
# case print exit=1 -- see test_capture_fix.py in this directory,
# TestPayloadValidatorShapeUnderPipefail, and the general case in
# index-generator/test_capture.py's PipefailMaskingTests.
#
# The header text recorded below is UNCHANGED (the command line as
# actually invoked, per FORMAT.md) -- only the wrapper that executes it
# changes, exactly like index-generator's rec().
set -u

if ! command -v bash >/dev/null 2>&1; then
    echo "capture.sh: bash is required (for 'set -o pipefail'); not found on PATH" >&2
    exit 2
fi

cd "$(dirname "$0")" || exit 2
OUT=captured_output.txt
HEADER='cat payloads_bad.json | python3 payload_validate.py - ; echo "exit=$?"'

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

bash -c "set -o pipefail; $HEADER" > "$BODY_FILE" 2>&1

python3 - "$OUT" "$HEADER" "$BODY_FILE" <<'PYEOF'
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

# This record's own convention embeds "; echo exit=$?" IN the command, so
# the exit= line is already the last line of `body` -- no separate
# trailer is appended (that would create a second exit= line, which
# FORMAT.md tolerates via "first occurrence wins" but which would not
# match this record's existing style, or any other record in this file).
trailing_sep = ["\n"] if had_blank_after else []
new_block = [header_line] + body.splitlines(keepends=True) + trailing_sep

lines[start:end] = new_block

with open(out_path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)

print("capture.sh: re-ran and replaced 1 record under pipefail: %r" % header)
PYEOF

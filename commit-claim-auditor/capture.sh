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
#     python3 -m unittest test_claimhist -v 2>&1 | tail -25
# `sh -c` (or plain `bash -c` without pipefail) on a pipeline reports only
# the LAST stage's exit status -- here, `tail`'s, which is ~always 0. A
# failing unittest suite piped through `tail -25` would therefore have
# been recorded as exit=0 even though `tail`'s own last-25-lines window
# would still show "FAILED (...)"  in the visible text -- the exit=
# line would silently disagree with the verdict printed two lines above
# it. Verified live: a throwaway failing suite piped through `tail -25`
# via `sh -c` records exit=0; the same suite run via
# `bash -c 'set -o pipefail; ...'` records the suite's real nonzero exit
# (see test_capture_fix.py in this directory).
#
# THE FIX (same two-part shape as index-generator's Finding 1 + 2):
#   1. No unittest run is piped through a filter at all -- `-v | tail -25`
#      existed only to keep the transcript from showing 154 individual
#      `... ok` lines; dropping `-v` produces unittest's own compact
#      dot-per-test + summary output directly, unpiped, with its own
#      genuine exit code. Nothing is filtered, so nothing can mask it.
#   2. rec() below still wraps every record in
#      `bash -c 'set -o pipefail; ...'` as defence in depth, matching
#      index-generator's convention, even though this particular
#      replacement command no longer contains a pipe.
set -u

if ! command -v bash >/dev/null 2>&1; then
    echo "capture.sh: bash is required (for 'set -o pipefail'); not found on PATH" >&2
    exit 2
fi

cd "$(dirname "$0")" || exit 2
OUT=captured_output.txt
OLD_HEADER='python3 -m unittest test_claimhist -v 2>&1 | tail -25'
NEW_HEADER='python3 -m unittest test_claimhist'

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

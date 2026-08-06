#!/usr/bin/env python3
"""pipe_classify.py -- shell-quote-aware companion to pipe_scan.py.

WHY THIS EXISTS, AND WHY IT IS A SEPARATE FILE
----------------------------------------------
``pipe_scan.py`` answers "which committed command records contain a ``|``
character?" by substring match (``"|" in command``). That is deliberately
naive and it is documented as such in ``README.md`` ("Finding 4" and
limitation 7): the utility's job is to be a small, honest, read-only
counter, and its raw number is quoted verbatim in this directory's
committed transcript.

The cost of that naivety is now measurable rather than hypothetical.  Of
the ten records ``pipe_scan.py`` currently flags repo-wide, **six contain
no shell pipeline at all**: five are regex alternation (``\\|`` or ``|``)
inside a quoted ``grep`` pattern, and one is the ``||`` OR operator.  A
reader who takes ``total_piped_records`` as "records with an exit-masking
risk" over-counts by 60%.

``pipe_scan.py`` is therefore left byte-for-byte alone -- changing its
output would invalidate the committed transcript that quotes it, and
tuning a disclosure tool until it reports zero is exactly the failure
mode this repository's evidence standard warns about.  Instead this file
adds the classification *beside* it: same scan, same records, but each
``|`` is labelled with what it actually is.  Both numbers can then be
reported together, and the gap between them is the disclosure.

WHAT COUNTS AS A PIPELINE
-------------------------
A ``|`` is a pipeline operator only when, at that position, the parser is
not inside single or double quotes, the character is not backslash-
escaped, and the character is not part of a ``||`` (logical OR) token.
``|&`` (bash's "pipe stdout and stderr") IS a pipeline.

Quoting rules implemented (POSIX ``sh``, which is what the ``rec()``
convention runs):

* Inside ``'single quotes'`` nothing is special -- not even a backslash.
  A ``|`` there is literal.
* Inside ``"double quotes"`` a backslash escapes only ``$``, ``` ` ```,
  ``"``, ``\\`` and newline.  A ``\\|`` inside double quotes is therefore
  two literal characters, and the ``|`` is still quoted (hence literal).
* Outside quotes, ``\\`` escapes the next character, so ``\\|`` is a
  literal bar.
* ``$(...)`` and backticks are NOT treated as quoting: a ``|`` inside a
  command substitution really is a pipeline, and is reported as one.

KNOWN LIMITS (stated, not silently assumed away)
------------------------------------------------
* ``#`` comments are not recognised.  No committed record in this
  repository contains one; a header ending in ``# ... | ...`` would be
  mis-labelled ``pipeline``.  That direction is the safe one: it
  over-reports risk rather than hiding it.
* Here-documents are not recognised, for the same reason (none exist in
  the committed records, and the failure direction is over-reporting).
* This tool classifies TEXT.  It does not execute anything, and it does
  not claim that a record classified ``pipeline`` is actually masking an
  exit status -- only that it is a pipeline, and therefore that the
  question applies to it.

Usage:
    python3 pipe_classify.py --repo-root ..
    python3 pipe_classify.py --repo-root .. -o pipe_classification_report.json
    python3 pipe_classify.py --command 'grep -c "a\\|b" f.json'

Exit codes:
    0  scan completed (this is a report, not a pass/fail check)
    2  --repo-root is not a directory
"""
import argparse
import json
import os
import re
import sys

HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$")

#: A ``|`` that really does connect two processes.
PIPELINE = "pipeline"
#: Part of a ``||`` logical-OR token.
OR_OPERATOR = "or_operator"
#: Inside single or double quotes -- a literal character in an argument.
QUOTED = "quoted"
#: Backslash-escaped outside quotes -- a literal character.
ESCAPED = "escaped"

#: Every kind, in a fixed order, so report keys are stable.
ALL_KINDS = (PIPELINE, OR_OPERATOR, QUOTED, ESCAPED)

#: The kinds that are NOT a pipeline. Kept explicit so a future kind has
#: to be classified deliberately rather than defaulting into "harmless".
NON_PIPELINE_KINDS = (OR_OPERATOR, QUOTED, ESCAPED)


def classify_bars(command):
    """Return a list of ``{"index": int, "kind": str}`` for every ``|``.

    ``index`` is the 0-based offset of the character within ``command``.
    Both characters of a ``||`` token are reported, each as
    ``or_operator``, so ``len(classify_bars(s))`` always equals
    ``s.count("|")`` -- the two views can be reconciled exactly.
    """
    bars = []
    i = 0
    n = len(command)
    in_single = False
    in_double = False
    while i < n:
        ch = command[i]
        if in_single:
            # Nothing is special inside single quotes, not even backslash.
            if ch == "'":
                in_single = False
            elif ch == "|":
                bars.append({"index": i, "kind": QUOTED})
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < n and command[i + 1] in '$`"\\\n':
                # Only these are escapable inside double quotes; the pair
                # is consumed together so a `\"` does not close the quote.
                i += 2
                continue
            if ch == '"':
                in_double = False
            elif ch == "|":
                bars.append({"index": i, "kind": QUOTED})
            i += 1
            continue
        # --- unquoted ---
        if ch == "\\":
            if i + 1 < n and command[i + 1] == "|":
                bars.append({"index": i + 1, "kind": ESCAPED})
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "|":
            if i + 1 < n and command[i + 1] == "|":
                bars.append({"index": i, "kind": OR_OPERATOR})
                bars.append({"index": i + 1, "kind": OR_OPERATOR})
                i += 2
                continue
            bars.append({"index": i, "kind": PIPELINE})
            i += 1
            continue
        i += 1
    return bars


def is_shell_pipeline(command):
    """True iff ``command`` contains at least one real pipeline operator."""
    return any(b["kind"] == PIPELINE for b in classify_bars(command))


def classify_command(command):
    """Return a summary dict for one command string."""
    bars = classify_bars(command)
    counts = {k: 0 for k in ALL_KINDS}
    for b in bars:
        counts[b["kind"]] += 1
    return {
        "command": command,
        "bar_count": len(bars),
        "counts": counts,
        "is_pipeline": counts[PIPELINE] > 0,
    }


def scan(repo_root):
    """Classify every ``|``-bearing record one level under ``repo_root``.

    Mirrors ``pipe_scan.py``'s traversal exactly (same directories, same
    ``captured_output.txt`` filename, same ``=== $ ... ===`` header
    grammar) so the two reports describe the same population and can be
    compared record for record.  Every list is sorted, and no wall-clock
    or environment value is read, so two runs over the same tree produce
    identical bytes.
    """
    tools = []
    total_files = 0
    total_records = 0
    total_bar_records = 0
    total_pipeline_records = 0
    total_bars = {k: 0 for k in ALL_KINDS}
    for name in sorted(os.listdir(repo_root)):
        d = os.path.join(repo_root, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        path = os.path.join(d, "captured_output.txt")
        if not os.path.isfile(path):
            continue
        total_files += 1
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        records = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = HEADER_RE.match(line)
            if not m:
                continue
            total_records += 1
            command = m.group(1)
            if "|" not in command:
                continue
            info = classify_command(command)
            info["line"] = lineno
            records.append(info)
            total_bar_records += 1
            for k in ALL_KINDS:
                total_bars[k] += info["counts"][k]
            if info["is_pipeline"]:
                total_pipeline_records += 1
        if records:
            records.sort(key=lambda r: r["line"])
            tools.append({
                "tool": name,
                "records_with_a_bar": len(records),
                "pipeline_records": sum(1 for r in records if r["is_pipeline"]),
                "records": records,
            })
    tools.sort(key=lambda t: t["tool"])
    return {
        "transcript_files_scanned": total_files,
        "total_command_records": total_records,
        # Same population pipe_scan.py's `total_piped_records` counts.
        "total_records_with_a_bar": total_bar_records,
        # The subset that is actually a shell pipeline.
        "total_pipeline_records": total_pipeline_records,
        # The difference, spelled out rather than left to subtraction.
        "total_non_pipeline_bar_records": total_bar_records - total_pipeline_records,
        "total_files_with_a_bar_record": len(tools),
        "total_files_with_a_pipeline_record":
            sum(1 for t in tools if t["pipeline_records"]),
        "bar_character_counts_by_kind": total_bars,
        "tools": tools,
    }


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipe_classify.py")
    ap.add_argument("--repo-root", default="..",
                    help="repository root containing the tool directories (default: ..)")
    ap.add_argument("-o", "--output", default=None, help="write the report JSON here")
    ap.add_argument("--command", default=None,
                    help="classify this single command string instead of scanning")
    args = ap.parse_args(argv)

    if args.command is not None:
        sys.stdout.write(canonical_json(classify_command(args.command)))
        return 0

    if not os.path.isdir(args.repo_root):
        sys.stderr.write("pipe_classify.py: --repo-root is not a directory: %s\n"
                         % args.repo_root)
        return 2

    text = canonical_json(scan(args.repo_root))
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

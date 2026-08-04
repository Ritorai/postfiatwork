#!/usr/bin/env python3
"""driftcheck.py -- compare each tool's README claims against its own
captured_output.txt transcript.

Every tool in this repository ships a README that makes checkable claims --
"Ran 26 tests", "exit 1", a block of commands -- and a captured_output.txt
that is supposed to be the run those claims came from. Nothing checked that
the two agreed. This does.

THE MINIMAL TRANSCRIPT FORMAT (see FORMAT.md for the normative version)

A captured_output.txt is a sequence of COMMAND RECORDS. A record starts at a
header line and runs to the next header or end of file:

    === $ <command> ===

Inside a record, three things are extracted and nothing else is required:

    exit=<int>              the command's exit status
    Ran <int> tests         a unittest summary line
    OK | FAILED (...)       the unittest verdict

That is the whole format. It is deliberately the shape the repository's
transcripts already have, so no existing file has to be rewritten -- a
format nobody can adopt without a migration is not a format, it is a wish.
Lines outside a record are preamble and are ignored.

WHAT IS COMPARED

  README claim                          transcript record
  --------------------------------      ---------------------------------
  `Ran N tests` / "N tests"             Ran N tests
  exit N / `exit=N` for a command       exit=N in that command's record
  a command in a fenced code block      a === $ command === header

Exit codes:
    0  no drift in any scanned directory
    1  drift found
    2  setup error (--root missing, --output unwritable)

The report carries a `coverage` block naming every directory found, whether
each had a README and a transcript, and whether its content was actually
compared -- so "no drift" can never be read as "everything was checked".
No timestamps, no durations: two runs produce byte-identical JSON.
"""

import argparse
import json
import os
import re
import sys

SCHEMA_VERSION = 1
TOOL_NAME = "transcript-drift"

D_MISSING_TRANSCRIPT = "MISSING_TRANSCRIPT"
D_MISSING_README = "MISSING_README"
D_NO_RECORDS = "TRANSCRIPT_HAS_NO_COMMAND_RECORDS"
D_RECORD_NO_EXIT = "TRANSCRIPT_RECORD_HAS_NO_EXIT"
D_TEST_COUNT = "TEST_COUNT_MISMATCH"
D_TEST_UNCLAIMED = "TEST_COUNT_NOT_CLAIMED_IN_README"
D_TEST_FAILED = "TRANSCRIPT_SHOWS_TEST_FAILURE"
D_EXIT_MISMATCH = "EXIT_CODE_MISMATCH"
D_CMD_NOT_RUN = "README_COMMAND_NOT_IN_TRANSCRIPT"

ALL_CODES = frozenset({
    D_MISSING_TRANSCRIPT, D_MISSING_README, D_NO_RECORDS, D_RECORD_NO_EXIT,
    D_TEST_COUNT, D_TEST_UNCLAIMED, D_TEST_FAILED, D_EXIT_MISMATCH, D_CMD_NOT_RUN,
})

HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$")
EXIT_RE = re.compile(r"^\s*exit=(-?\d+)\s*$")
RAN_RE = re.compile(r"^Ran (\d+) tests? in ")
VERDICT_RE = re.compile(r"^(OK|FAILED)\b")
FENCE_RE = re.compile(r"^```")
# "Ran 26 tests", "26 tests", "**174 tests**" -- a count the README asserts.
README_TESTS_RE = re.compile(r"(?:Ran\s+)?\*{0,2}(\d+)\*{0,2}\s+tests?\b")
# "| Exit | Meaning |" opens an exit table; "| `2` | setup error |" is a claim.
TABLE_EXIT_HEADER_RE = re.compile(r"^\|\s*\**exit\b", re.I)
TABLE_ROW_INT_RE = re.compile(r"^\|\s*\*{0,2}`?(-?\d+)`?\*{0,2}\s*\|")
MAX_LISTED = 8


class SetupError(Exception):
    pass


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Transcript parsing
# --------------------------------------------------------------------------

def parse_transcript(text):
    """Return (records, verdicts). A record is
    {command, exit_code, test_count, line}. verdicts is the list of
    OK/FAILED lines seen anywhere in the file."""
    records = []
    verdicts = []
    current = None
    for lineno, line in enumerate(text.splitlines(), 1):
        m = HEADER_RE.match(line)
        if m:
            current = {"command": normalise_cmd(m.group(1)), "exit_code": None,
                       "test_count": None, "line": lineno}
            records.append(current)
            continue
        v = VERDICT_RE.match(line)
        if v:
            verdicts.append(v.group(1))
        if current is None:
            continue
        e = EXIT_RE.match(line)
        if e and current["exit_code"] is None:
            current["exit_code"] = int(e.group(1))
            continue
        r = RAN_RE.match(line)
        if r and current["test_count"] is None:
            current["test_count"] = int(r.group(1))
    return records, verdicts


def normalise_cmd(cmd):
    """Collapse whitespace and drop a trailing `; echo "exit=$?"` so a README
    command and its transcript header compare on the part that matters."""
    cmd = re.sub(r"\s+", " ", cmd).strip()
    cmd = re.sub(r"\s*;\s*echo\s+\"?exit=\$\?\"?\s*$", "", cmd)
    cmd = re.sub(r"\s*\|\s*(tail|head)\b.*$", "", cmd)
    return cmd.strip()


# --------------------------------------------------------------------------
# README parsing
# --------------------------------------------------------------------------

def parse_readme(text):
    """Return {commands, test_counts, exit_claims}. exit_claims maps a
    normalised command to the exit code the README states on the same line."""
    commands = []
    exit_claims = {}
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        stripped = line.strip()
        if in_fence and stripped and not stripped.startswith("#"):
            cmd = normalise_cmd(stripped)
            if cmd.startswith("python3 ") or cmd.startswith("./"):
                commands.append(cmd)
                m = re.search(r"exit=\$\?", stripped)
                if m:
                    exit_claims.setdefault(cmd, None)
    counts = [int(m) for m in README_TESTS_RE.findall(text)]
    # A table row like "| tests | `Ran 26 tests` / `OK` |" and prose
    # "174 tests, OK" both land here.
    return {"commands": sorted(set(commands)),
            "test_counts": sorted(set(counts)),
            "exit_claims": exit_claims}


def readme_exit_claims(text):
    """Explicit 'exit **N**' / 'exit `N`' / 'exit=N' assertions anywhere in
    the README, as a set of ints. Used only to check that every exit code the
    transcript records is one the README acknowledges.

    The markdown exit table is handled separately, and finding that out cost
    a round: nearly every README here documents its exit codes as

        | Exit | Meaning |
        |---|---|
        | `0` | clean |

    and the prose regex above matches none of those rows -- the word "exit"
    is in the header, the numbers are in later lines. Without the table pass
    the whole EXIT_CODE_MISMATCH check silently found nothing on most of the
    repository. This tool's own README was the file that exposed it."""
    out = set()
    for m in re.finditer(r"exit(?:\s+code)?[\s=]*\*{0,2}`?(-?\d+)`?\*{0,2}", text):
        out.add(int(m.group(1)))
    in_exit_table = False
    for line in text.splitlines():
        if TABLE_EXIT_HEADER_RE.match(line):
            in_exit_table = True
            continue
        if not in_exit_table:
            continue
        if not line.startswith("|"):
            in_exit_table = False
            continue
        m = TABLE_ROW_INT_RE.match(line)
        if m:
            out.add(int(m.group(1)))
    return out


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def compare(name, readme_text, transcript_text):
    findings = []

    def add(code, detail):
        findings.append({"tool": name, "code": code, "detail": detail})

    records, verdicts = parse_transcript(transcript_text)
    claims = parse_readme(readme_text)
    claimed_exits = readme_exit_claims(readme_text)

    if not records:
        add(D_NO_RECORDS, {"reason": "no '=== $ command ===' header found; "
                                     "the transcript does not follow FORMAT.md"})
        return findings, {"records": 0, "readme_commands": len(claims["commands"])}

    for rec in records:
        if rec["exit_code"] is None:
            add(D_RECORD_NO_EXIT, {"command": rec["command"], "line": rec["line"]})

    if "FAILED" in verdicts:
        add(D_TEST_FAILED, {"verdicts": sorted(set(verdicts))})

    recorded_counts = sorted({r["test_count"] for r in records
                              if r["test_count"] is not None})
    if recorded_counts:
        unclaimed = [c for c in recorded_counts if c not in claims["test_counts"]]
        if unclaimed:
            if claims["test_counts"]:
                add(D_TEST_COUNT, {"transcript_ran": recorded_counts,
                                   "readme_claims": claims["test_counts"],
                                   "unmatched": unclaimed})
            else:
                add(D_TEST_UNCLAIMED, {"transcript_ran": recorded_counts})

    recorded_exits = sorted({r["exit_code"] for r in records
                             if r["exit_code"] is not None})
    unacknowledged = [c for c in recorded_exits if c not in claimed_exits]
    if unacknowledged and claimed_exits:
        add(D_EXIT_MISMATCH, {"transcript_exits": recorded_exits,
                              "readme_exit_claims": sorted(claimed_exits),
                              "unacknowledged": unacknowledged})

    ran = {r["command"] for r in records}
    not_run = [c for c in claims["commands"] if c not in ran]
    if not_run:
        add(D_CMD_NOT_RUN, {"count": len(not_run),
                            "commands": not_run[:MAX_LISTED],
                            "truncated": max(0, len(not_run) - MAX_LISTED)})

    return findings, {"records": len(records),
                      "readme_commands": len(claims["commands"]),
                      "transcript_test_counts": recorded_counts,
                      "readme_test_counts": claims["test_counts"],
                      "transcript_exits": recorded_exits}


# --------------------------------------------------------------------------
# Discovery and report
# --------------------------------------------------------------------------

def discover(root):
    if not os.path.isdir(root):
        raise SetupError("--root is not a directory: %s" % root)
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == "__pycache__":
            continue
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        out.append((name,
                    os.path.join(d, "README.md"),
                    os.path.join(d, "captured_output.txt")))
    return out


def build_report(root, inventory_path=None):
    dirs = discover(root)
    inventory = None
    if inventory_path:
        if not os.path.isfile(inventory_path):
            raise SetupError("--inventory not found: %s" % inventory_path)
        try:
            inventory = json.loads(read_text(inventory_path))
        except json.JSONDecodeError as exc:
            raise SetupError("--inventory is not valid JSON: %s" % exc)

    findings = []
    scanned, present_only, absent = [], [], []
    stats = {}

    names = sorted({n for n, _, _ in dirs} | set(inventory or {}))
    for name in names:
        entry = next((d for d in dirs if d[0] == name), None)
        inv = (inventory or {}).get(name)
        has_readme = bool(entry and os.path.isfile(entry[1])) or bool(inv and inv.get("readme"))
        has_transcript = bool(entry and os.path.isfile(entry[2])) or bool(inv and inv.get("transcript"))

        if not has_readme:
            findings.append({"tool": name, "code": D_MISSING_README, "detail": {}})
        if not has_transcript:
            findings.append({"tool": name, "code": D_MISSING_TRANSCRIPT,
                             "detail": {"reason": "directory has a README but no "
                                                  "captured_output.txt to check it against"}})

        content_here = bool(entry and os.path.isfile(entry[1]) and os.path.isfile(entry[2]))
        if content_here:
            f, s = compare(name, read_text(entry[1]), read_text(entry[2]))
            findings.extend(f)
            stats[name] = s
            scanned.append(name)
        elif has_readme or has_transcript:
            present_only.append(name)
        else:
            absent.append(name)

    counts = {c: 0 for c in sorted(ALL_CODES)}
    for f in findings:
        counts[f["code"]] += 1
    findings.sort(key=lambda f: (f["tool"], f["code"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "drift" if findings else "clean",
        "findings": findings,
        "drift_counts": counts,
        "coverage": {
            "directories_known": len(names),
            "content_compared": len(scanned),
            "presence_checked_only": len(present_only),
            "compared_tools": sorted(scanned),
            "presence_only_tools": sorted(present_only),
        },
        "stats": stats,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="driftcheck.py")
    ap.add_argument("--root", default=".", help="directory containing tool subdirectories")
    ap.add_argument("--inventory", default=None,
                    help="JSON {tool: {readme: bool|size, transcript: bool|size}} covering "
                         "directories whose content is not available locally. Presence "
                         "drift is reported for those; content drift is not claimed.")
    ap.add_argument("-o", "--output", default=None, help="write the report JSON here")
    args = ap.parse_args(argv)
    try:
        report = build_report(args.root, args.inventory)
        text = canonical_json(report)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise SetupError("could not write --output %s: %s" % (args.output, exc))
        else:
            sys.stdout.write(text)
        return 1 if report["findings"] else 0
    except SetupError as exc:
        text = canonical_json({"schema_version": SCHEMA_VERSION, "tool": TOOL_NAME,
                               "status": "error", "error": str(exc)})
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError:
                sys.stderr.write(text)
        else:
            sys.stdout.write(text)
        return 2


if __name__ == "__main__":
    sys.exit(main())

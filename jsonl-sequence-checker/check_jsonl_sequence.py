"""Accept a JSONL file only if its records are sequenced exactly 1..N in file order.

Stdlib-only Python 3. No third-party packages, no network.

THE CONTRACT, stated once and enforced literally

A file is ACCEPTED when all of the following hold:

  1. It contains at least one record. An empty file, or one whose entire
     content is a single newline, holds no records and is rejected --
     "nonempty" is part of the contract, not an edge case to shrug at.
  2. Every record line parses as JSON.
  3. Every record is a JSON object.
  4. Every record has a `sequence` key.
  5. Every `sequence` value is a JSON integer. `true` is NOT an integer here,
     even though Python says `isinstance(True, int)` -- see REJECTION CODES.
     Nor is `2.0`, `"2"`, or `null`.
  6. The values are exactly 1..N, where N is the record count, in file order:
     the record on the k-th line carries `sequence` k.

Anything else is rejected, and the report names the line.

REJECTION CODES

  EMPTY_INPUT            no records at all
  MALFORMED_JSON         a line does not parse as JSON
  RECORD_NOT_OBJECT      a line parses but is not a JSON object
  MISSING_SEQUENCE       a record has no `sequence` key
  SEQUENCE_NOT_INTEGER   `sequence` is a float, string, null, list or object
  SEQUENCE_IS_BOOLEAN    `sequence` is `true` or `false`

    Booleans get their own code rather than folding into
    SEQUENCE_NOT_INTEGER because in Python `bool` is a subclass of `int`:
    `isinstance(True, int)` is True and `True == 1`. A checker written the
    obvious way accepts `{"sequence": true}` as the number 1 and says nothing.
    The separate code is the visible proof that this file does not.

  SEQUENCE_OUT_OF_ORDER  the k-th record does not carry `sequence` k
  SEQUENCE_DUPLICATE     a value appears on more than one line
  SEQUENCE_MISSING       a value in 1..N appears on no line
  SEQUENCE_OUT_OF_RANGE  a value outside 1..N

    The last four overlap on purpose. A positional walk alone reports the
    same OUT_OF_ORDER for a gap, a duplicate and a swap, which tells a
    reader where the file stops matching but not what is wrong with it. The
    set-level codes answer that second question: DUPLICATE plus MISSING says
    "a value was repeated instead of advancing", DUPLICATE and MISSING both
    absent says "the values are a permutation, only the order is wrong".

EXIT CODES

  0  accepted
  1  rejected -- the file was read and parsed as far as it could be, and the
     contract does not hold. This includes malformed lines: refusing a file is
     this tool's job, so a bad line is a finding about the input, not a
     failure of the run.
  2  usage error -- something the caller fixes by changing the command line:
     bad flags, a path that does not exist, a path that is a directory, bytes
     that are not UTF-8.

DETERMINISM

The report is `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing newline. It contains line numbers and
values, never a path, a timestamp, a hostname or a working directory, so the
same input produces the same bytes from any directory, for a given
interpreter configuration. That last clause is not decoration: CPython's
int_max_str_digits bounds how long an integer literal `json` will parse, so a
5000-digit `sequence` is MALFORMED_JSON by default and OUT_OF_RANGE under
PYTHONINTMAXSTRDIGITS=6000. The report never quotes an exception message, so
the bytes do not drift further than that.
"""
import argparse
import json
import sys

REPORT_VERSION = "1.0"

EXIT_ACCEPTED = 0
EXIT_REJECTED = 1
EXIT_USAGE = 2

#: Every code this tool can emit. `test_check_jsonl_sequence` asserts that
#: this tuple and the README's table name exactly the same set, so a code
#: cannot be added in one place and forgotten in the other.
CODES = (
    "EMPTY_INPUT",
    "MALFORMED_JSON",
    "RECORD_NOT_OBJECT",
    "MISSING_SEQUENCE",
    "SEQUENCE_NOT_INTEGER",
    "SEQUENCE_IS_BOOLEAN",
    "SEQUENCE_OUT_OF_ORDER",
    "SEQUENCE_DUPLICATE",
    "SEQUENCE_MISSING",
    "SEQUENCE_OUT_OF_RANGE",
)


class UsageError(Exception):
    """Something the caller can fix by changing the command line."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def finding(code, line, **extra):
    """One rejection reason. `line` is 1-based, or None for whole-file codes."""
    out = {"code": code, "line": line}
    out.update(extra)
    return out


def _describe(value):
    """A stable type name for the report. Never the value's repr."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def read_lines(path):
    """Return the file's record lines, or raise UsageError.

    A single trailing newline ends the last record and does not begin a new
    one. Any other blank line is kept, so it can be reported as
    MALFORMED_JSON rather than silently skipped -- a checker that skips blank
    lines cannot tell a file with a hole in it from a file without one.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise UsageError("no such file: %s" % path)
    except IsADirectoryError:
        raise UsageError("not a file: %s" % path)
    except UnicodeDecodeError as exc:
        raise UsageError("input is not valid UTF-8: %s" % exc.reason)
    except OSError as exc:
        raise UsageError("cannot read %s: %s" % (path, exc.strerror or exc))
    if text in ("", "\n"):
        # Nothing, or a lone newline that terminates a record which is not
        # there. Either way the file holds no records. A file of TWO newlines
        # does hold records -- two blank ones -- and they are reported as
        # MALFORMED_JSON, because a blank line between records is a hole.
        return []
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def parse_records(lines):
    """Split lines into (records, findings).

    A record is (line_number, object). Findings here are per-line defects:
    unparseable JSON, a non-object, a missing key, a wrong type.
    """
    records = []
    findings = []
    for index, raw in enumerate(lines):
        lineno = index + 1
        try:
            value = json.loads(raw)
        except ValueError as exc:
            # The exception text is deliberately NOT carried into the
            # report. CPython's int_max_str_digits is an environment setting,
            # so json's message for an over-long integer literal varies with
            # interpreter configuration, and a report that quotes it stops
            # being reproducible.
            findings.append(finding("MALFORMED_JSON", lineno))
            continue
        if not isinstance(value, dict):
            findings.append(finding("RECORD_NOT_OBJECT", lineno,
                                    found_type=_describe(value)))
            continue
        if "sequence" not in value:
            findings.append(finding("MISSING_SEQUENCE", lineno))
            continue
        seq = value["sequence"]
        # Order matters: bool before int, because bool IS an int in Python.
        if isinstance(seq, bool):
            findings.append(finding("SEQUENCE_IS_BOOLEAN", lineno,
                                    found=json.dumps(seq)))
            continue
        if not isinstance(seq, int):
            findings.append(finding("SEQUENCE_NOT_INTEGER", lineno,
                                    found_type=_describe(seq)))
            continue
        records.append((lineno, seq))
    return records, findings


def check_sequence(records, total):
    """Positional and set-level checks over the records that have an integer.

    `total` is the number of record LINES, not the number of usable records,
    so a file whose third line is malformed is still checked against 1..N for
    the N it actually has. Otherwise deleting a bad line would silently shrink
    the expected range and hide a gap.
    """
    findings = []

    for position, (lineno, value) in enumerate(records, start=1):
        if value != position:
            findings.append(finding("SEQUENCE_OUT_OF_ORDER", lineno,
                                    expected=position, found=value))
            break

    seen = {}
    for lineno, value in records:
        seen.setdefault(value, []).append(lineno)

    for value in sorted(seen):
        if len(seen[value]) > 1:
            findings.append(finding("SEQUENCE_DUPLICATE", seen[value][0],
                                    value=value, lines=seen[value]))

    for value in sorted(seen):
        if value < 1 or value > total:
            findings.append(finding("SEQUENCE_OUT_OF_RANGE", seen[value][0],
                                    value=value, expected_range=[1, total]))

    for value in range(1, total + 1):
        if value not in seen:
            findings.append(finding("SEQUENCE_MISSING", None, value=value))

    return findings


def check(lines):
    """Return the report for a list of record lines."""
    total = len(lines)
    if total == 0:
        findings = [finding("EMPTY_INPUT", None)]
        records = []
    else:
        records, findings = parse_records(lines)
        findings = findings + check_sequence(records, total)

    findings.sort(key=lambda f: (f["line"] is None, f["line"] or 0,
                                 f["code"], canonical(f)))

    counts = {}
    for f in findings:
        counts[f["code"]] = counts.get(f["code"], 0) + 1

    return {
        "report_version": REPORT_VERSION,
        "status": "accepted" if not findings else "rejected",
        "records": total,
        "sequenced_records": len(records),
        "findings_total": len(findings),
        "counts_by_code": counts,
        "findings": findings,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="check_jsonl_sequence.py",
        description="Accept a JSONL file only if its records are sequenced "
                    "exactly 1..N in file order.")
    parser.add_argument("input", help="path to the JSONL file to check")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        lines = read_lines(args.input)
    except UsageError as exc:
        sys.stderr.write("INVALID_INPUT: %s\n" % exc)
        return EXIT_USAGE

    report = check(lines)
    sys.stdout.write(canonical(report))
    return EXIT_ACCEPTED if report["status"] == "accepted" else EXIT_REJECTED


if __name__ == "__main__":
    sys.exit(main())

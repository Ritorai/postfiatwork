#!/usr/bin/env python3
"""queue_audit.py -- stdlib-only PFTL task-queue snapshot auditor.

Checks a task-queue snapshot (JSON document with a "tasks" array and a
"summary" object) for:

  * duplicate task_id values
  * malformed records (missing / wrong-typed required fields)
  * status/list mismatches
  * invalid rewards (negative, non-numeric, or NaN/inf)
  * invalid or non-UTC timestamps
  * deadlines that fall before their creation timestamp
  * summary counts that disagree with the actual per-bucket task counts

Output is "canonical" JSON: json.dumps(..., sort_keys=True,
separators=(",", ":"), ensure_ascii=True) followed by a single trailing
newline, with no runtime-dependent fields (no wall-clock timestamps,
no hostnames, no absolute paths, no set/dict-ordering leakage). Running
the tool twice on the same input therefore produces byte-identical
output.

Exit codes:
  0 -- clean snapshot, no findings
  1 -- snapshot read and parsed successfully, findings were reported
  2 -- input could not be read or parsed, or a usage error occurred

Standard library only: argparse, json, sys, os, datetime, decimal, math.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------
# Finding codes
# --------------------------------------------------------------------------

DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
MALFORMED_RECORD = "MALFORMED_RECORD"
STATUS_LIST_MISMATCH = "STATUS_LIST_MISMATCH"
INVALID_REWARD = "INVALID_REWARD"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
DEADLINE_BEFORE_CREATED = "DEADLINE_BEFORE_CREATED"
SUMMARY_COUNT_MISMATCH = "SUMMARY_COUNT_MISMATCH"

ALL_CODES = (
    DUPLICATE_TASK_ID,
    MALFORMED_RECORD,
    STATUS_LIST_MISMATCH,
    INVALID_REWARD,
    INVALID_TIMESTAMP,
    DEADLINE_BEFORE_CREATED,
    SUMMARY_COUNT_MISMATCH,
)

REQUIRED_FIELDS = (
    "task_id",
    "title",
    "status",
    "list",
    "reward",
    "created_at",
    "deadline",
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_INVALID_INPUT = 2


# --------------------------------------------------------------------------
# Small data holder
# --------------------------------------------------------------------------


class Finding:
    """One audit finding. Comparable/sortable by (code, task_id, detail)."""

    __slots__ = ("code", "task_id", "detail")

    def __init__(self, code, task_id, detail):
        self.code = code
        self.task_id = task_id
        self.detail = detail

    def sort_key(self):
        return (self.code, self.task_id, self.detail)

    def to_dict(self):
        return {"code": self.code, "task_id": self.task_id, "detail": self.detail}


def _synthetic_id(index):
    """Stable placeholder identifier for records lacking a usable task_id."""
    return "<index:%d>" % index


# --------------------------------------------------------------------------
# Timestamp parsing
# --------------------------------------------------------------------------


def parse_utc_timestamp(value):
    """Parse an ISO-8601 UTC timestamp string.

    Returns a timezone-aware datetime.datetime on success, or None if the
    value is not a valid, explicitly-UTC ISO-8601 timestamp. Naive
    timestamps (no offset) and timestamps with a non-zero UTC offset are
    both rejected -- the snapshot format requires explicit UTC.
    """
    if not isinstance(value, str) or not value:
        return None

    text = value
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return None
    if dt.utcoffset() != datetime.timedelta(0):
        return None
    return dt


# --------------------------------------------------------------------------
# Reward validation
# --------------------------------------------------------------------------


def validate_reward(raw):
    """Validate a reward value.

    Returns (decimal_value_or_None, error_detail_or_None). On success,
    error_detail is None and decimal_value is a Decimal >= 0. On failure,
    decimal_value is None and error_detail explains why.

    Callers must run this against a value produced by
    json.loads(text, parse_float=Decimal) -- see the note on FLOAT_TYPES
    below for why plain float is only expected to arrive here via the
    NaN/Infinity/-Infinity JSON literals (which json.loads always parses
    as float, independent of parse_float).
    """
    if isinstance(raw, bool):
        return None, "reward must be numeric, got boolean %r" % (raw,)

    if isinstance(raw, int):
        value = Decimal(raw)
        if value < 0:
            return None, "reward is negative (%s)" % value
        return value, None

    if isinstance(raw, Decimal):
        # This is the normal path for any fractional/exponential JSON
        # number literal when the caller parses with parse_float=Decimal,
        # which preserves full source precision (never round-trips
        # through a 64-bit float, so no precision is silently lost).
        if raw.is_nan():
            return None, "reward is NaN"
        if raw.is_infinite():
            return None, "reward is infinite"
        if raw < 0:
            return None, "reward is negative (%s)" % raw
        return raw, None

    if isinstance(raw, float):
        # Only reachable in practice for the literal JSON tokens NaN,
        # Infinity and -Infinity: json.loads always routes those through
        # parse_constant (as float), never through parse_float, so a
        # finite float should not normally appear here. Guard explicitly
        # rather than trust that: a finite float that underflowed to
        # +/-0.0 or lost precision must not be silently treated as a
        # clean Decimal by round-tripping it through str().
        if math.isnan(raw):
            return None, "reward is NaN"
        if math.isinf(raw):
            return None, "reward is infinite"
        return None, (
            "reward arrived as a raw float (%r) instead of Decimal; "
            "the caller must parse JSON with parse_float=Decimal" % (raw,)
        )

    if isinstance(raw, str):
        return None, "reward must be a JSON number, got a string (%r)" % (raw,)

    if isinstance(raw, list):
        return None, "reward must be a JSON number, got an array"

    if isinstance(raw, dict):
        return None, "reward must be a JSON number, got an object"

    return None, "reward must be a JSON number, got %s" % type(raw).__name__


# --------------------------------------------------------------------------
# Per-record structural validation
# --------------------------------------------------------------------------


def _check_required_str_field(record, field, findings, task_id_for_sort):
    """Check that `field` exists on `record` and is a non-empty string.

    Returns True if the field is present and a valid non-empty string,
    False otherwise (and appends a MALFORMED_RECORD finding in that case).
    """
    if field not in record or record[field] is None:
        findings.append(
            Finding(
                MALFORMED_RECORD,
                task_id_for_sort,
                "missing required field '%s'" % field,
            )
        )
        return False
    value = record[field]
    if not isinstance(value, str) or value == "":
        findings.append(
            Finding(
                MALFORMED_RECORD,
                task_id_for_sort,
                "field '%s' must be a non-empty string, got %s"
                % (field, type(value).__name__),
            )
        )
        return False
    return True


def audit_task_record(index, record, findings, id_counts):
    """Validate a single task record, appending Finding objects.

    Returns (task_id_for_sort, list_value_or_None) so the caller can
    aggregate duplicate-id and bucket-count information.
    """
    if not isinstance(record, dict):
        synthetic = _synthetic_id(index)
        findings.append(
            Finding(
                MALFORMED_RECORD,
                synthetic,
                "task record at index %d is not a JSON object (got %s)"
                % (index, type(record).__name__),
            )
        )
        return synthetic, None

    # task_id: validated separately so we always have a sort identifier.
    raw_task_id = record.get("task_id")
    if "task_id" not in record or raw_task_id is None:
        task_id_for_sort = _synthetic_id(index)
        findings.append(
            Finding(
                MALFORMED_RECORD,
                task_id_for_sort,
                "missing required field 'task_id'",
            )
        )
        task_id_ok = False
    elif not isinstance(raw_task_id, str) or raw_task_id == "":
        task_id_for_sort = _synthetic_id(index)
        findings.append(
            Finding(
                MALFORMED_RECORD,
                task_id_for_sort,
                "field 'task_id' must be a non-empty string, got %s"
                % type(raw_task_id).__name__,
            )
        )
        task_id_ok = False
    else:
        task_id_for_sort = raw_task_id
        task_id_ok = True

    if task_id_ok:
        id_counts.setdefault(raw_task_id, []).append(index)

    title_ok = _check_required_str_field(record, "title", findings, task_id_for_sort)
    status_ok = _check_required_str_field(record, "status", findings, task_id_for_sort)
    list_ok = _check_required_str_field(record, "list", findings, task_id_for_sort)
    created_ok = _check_required_str_field(
        record, "created_at", findings, task_id_for_sort
    )
    deadline_ok = _check_required_str_field(
        record, "deadline", findings, task_id_for_sort
    )

    # Reward: missing/null is MALFORMED_RECORD; present-but-wrong is
    # INVALID_REWARD (handled by validate_reward).
    if "reward" not in record or record["reward"] is None:
        findings.append(
            Finding(
                MALFORMED_RECORD,
                task_id_for_sort,
                "missing required field 'reward'",
            )
        )
    else:
        _, reward_error = validate_reward(record["reward"])
        if reward_error is not None:
            findings.append(Finding(INVALID_REWARD, task_id_for_sort, reward_error))

    # status/list mismatch (only meaningful if both fields are usable).
    if status_ok and list_ok:
        status_val = record["status"]
        list_val = record["list"]
        if status_val != list_val:
            findings.append(
                Finding(
                    STATUS_LIST_MISMATCH,
                    task_id_for_sort,
                    "status '%s' does not match list '%s'" % (status_val, list_val),
                )
            )

    # timestamps
    created_dt = None
    deadline_dt = None
    if created_ok:
        created_dt = parse_utc_timestamp(record["created_at"])
        if created_dt is None:
            findings.append(
                Finding(
                    INVALID_TIMESTAMP,
                    task_id_for_sort,
                    "created_at is not a valid UTC ISO-8601 timestamp (%r)"
                    % (record["created_at"],),
                )
            )
    if deadline_ok:
        deadline_dt = parse_utc_timestamp(record["deadline"])
        if deadline_dt is None:
            findings.append(
                Finding(
                    INVALID_TIMESTAMP,
                    task_id_for_sort,
                    "deadline is not a valid UTC ISO-8601 timestamp (%r)"
                    % (record["deadline"],),
                )
            )

    if created_dt is not None and deadline_dt is not None:
        if deadline_dt < created_dt:
            findings.append(
                Finding(
                    DEADLINE_BEFORE_CREATED,
                    task_id_for_sort,
                    "deadline (%s) is before created_at (%s)"
                    % (record["deadline"], record["created_at"]),
                )
            )

    list_bucket = record.get("list") if list_ok else None
    return task_id_for_sort, list_bucket


def audit_document(document):
    """Audit a parsed snapshot document.

    Returns (findings_dict_list, task_count). Raises ValueError if the
    top-level document shape is unusable (not a dict, "tasks" missing or
    not a list, "summary" present but not a dict).
    """
    if not isinstance(document, dict):
        raise ValueError("top-level JSON value must be an object")

    tasks = document.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("'tasks' field must be present and be an array")

    summary = document.get("summary", {})
    if not isinstance(summary, dict):
        raise ValueError("'summary' field must be an object")

    findings = []
    id_counts = {}
    actual_counts = {}

    for index, record in enumerate(tasks):
        _, list_bucket = audit_task_record(index, record, findings, id_counts)
        if list_bucket is not None:
            actual_counts[list_bucket] = actual_counts.get(list_bucket, 0) + 1

    for task_id, indices in id_counts.items():
        if len(indices) > 1:
            findings.append(
                Finding(
                    DUPLICATE_TASK_ID,
                    task_id,
                    "task_id '%s' appears %d times" % (task_id, len(indices)),
                )
            )

    summary_numeric = {}
    for key, value in summary.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            findings.append(
                Finding(
                    SUMMARY_COUNT_MISMATCH,
                    key,
                    "summary count for bucket '%s' is not numeric (got %s)"
                    % (key, type(value).__name__),
                )
            )
            continue
        summary_numeric[key] = value

    buckets = set(summary_numeric) | set(actual_counts)
    for bucket in buckets:
        expected = summary_numeric.get(bucket, 0)
        actual = actual_counts.get(bucket, 0)
        if expected != actual:
            findings.append(
                Finding(
                    SUMMARY_COUNT_MISMATCH,
                    bucket,
                    "summary count for bucket '%s' is %s but actual count is %d"
                    % (bucket, expected, actual),
                )
            )

    findings.sort(key=Finding.sort_key)
    return [f.to_dict() for f in findings], len(tasks)


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------


def canonical_dumps(obj):
    """Serialize obj as canonical JSON with a single trailing newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def build_report(findings, task_count):
    return {
        "findings": findings,
        "finding_count": len(findings),
        "task_count": task_count,
        "result": "clean" if not findings else "findings",
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_input_text(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def run(argv):
    parser = argparse.ArgumentParser(
        prog="queue_audit.py",
        description="Audit a PFTL task-queue snapshot for structural and consistency issues.",
    )
    parser.add_argument(
        "input",
        help="Path to a snapshot JSON file, or '-' to read from stdin.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write the canonical JSON report to this file instead of stdout.",
    )
    args = parser.parse_args(argv)

    try:
        text = _read_input_text(args.input)
    except OSError as exc:
        print("queue_audit.py: error: could not read '%s': %s" % (args.input, exc), file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        document = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        print("queue_audit.py: error: invalid JSON: %s" % exc, file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        findings, task_count = audit_document(document)
    except ValueError as exc:
        print("queue_audit.py: error: invalid snapshot: %s" % exc, file=sys.stderr)
        return EXIT_INVALID_INPUT

    report = build_report(findings, task_count)
    output_text = canonical_dumps(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as fh:
                fh.write(output_text)
        except OSError as exc:
            print(
                "queue_audit.py: error: could not write '%s': %s" % (args.output, exc),
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT
    else:
        sys.stdout.write(output_text)

    return EXIT_FINDINGS if findings else EXIT_CLEAN


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()

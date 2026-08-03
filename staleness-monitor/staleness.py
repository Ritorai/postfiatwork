#!/usr/bin/env python3
"""staleness.py -- stdlib-only staleness monitor for Task Node records.

Reads an array of open Task Node records (JSON) and reports:

  * OVERDUE_PROPOSED          -- a "proposed" task whose deadline is in the
                                  past relative to the injected --now.
  * STALE_ACCEPTED             -- an "accepted" task whose age (now - created_at)
                                  exceeds --accepted-stale-hours.
  * STALE_SUBMITTED            -- a "submitted" task whose age exceeds
                                  --submitted-stale-hours.
  * MALFORMED_DEADLINE         -- deadline is present, non-null, and either
                                  unparseable as ISO-8601 or not expressed in UTC.
  * MALFORMED_CREATED_AT       -- created_at is unparseable as ISO-8601 or not
                                  expressed in UTC.
  * DEADLINE_BEFORE_CREATED    -- deadline (once parsed) is earlier than
                                  created_at (once parsed) -- the deadline was
                                  already past at creation time.

Findings are grouped by urgency bucket ("critical" / "warning" / "info") and
emitted as canonical JSON:

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

plus a single trailing newline.

Reproducibility contract
-------------------------
The current time is NEVER read from the system clock anywhere in the report
path. The UTC "now" used for every age/overdue computation is supplied
exclusively via the required --now command-line argument and threaded
explicitly through every function that needs it. This module makes no
wall-clock lookup of any kind (the standard library's current-moment
accessors on the datetime and time modules are absent from this file
entirely) anywhere in the report path (see README.md / captured_output.txt
for a grep proof).

Exit codes
----------
  0  -- input parsed successfully and no findings were produced (no breach).
  1  -- input parsed successfully and at least one finding was produced.
  2  -- invalid input or usage error (bad/missing --now, unreadable or
        malformed input file, input JSON that is not a list, task records
        missing required keys, negative --*-stale-hours, etc).
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

__all__ = [
    "parse_utc_timestamp",
    "format_age",
    "bucket_for_overdue_proposed",
    "bucket_for_stale",
    "evaluate_task",
    "build_report",
    "canonical_json",
    "InputError",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ALLOWED_STATUSES = ("proposed", "accepted", "submitted")

# Keys every task record must contain. ``deadline`` may hold the value None.
REQUIRED_KEYS = ("task_id", "title", "status", "created_at", "deadline")

CODE_OVERDUE_PROPOSED = "OVERDUE_PROPOSED"
CODE_STALE_ACCEPTED = "STALE_ACCEPTED"
CODE_STALE_SUBMITTED = "STALE_SUBMITTED"
CODE_MALFORMED_DEADLINE = "MALFORMED_DEADLINE"
CODE_DEADLINE_BEFORE_CREATED = "DEADLINE_BEFORE_CREATED"
CODE_MALFORMED_CREATED_AT = "MALFORMED_CREATED_AT"

ALL_CODES = (
    CODE_OVERDUE_PROPOSED,
    CODE_STALE_ACCEPTED,
    CODE_STALE_SUBMITTED,
    CODE_MALFORMED_DEADLINE,
    CODE_DEADLINE_BEFORE_CREATED,
    CODE_MALFORMED_CREATED_AT,
)

BUCKET_CRITICAL = "critical"
BUCKET_WARNING = "warning"
BUCKET_INFO = "info"

ALL_BUCKETS = (BUCKET_CRITICAL, BUCKET_WARNING, BUCKET_INFO)

DEFAULT_ACCEPTED_STALE_HOURS = 48
DEFAULT_SUBMITTED_STALE_HOURS = 72


class InputError(Exception):
    """Raised for invalid input / usage problems. Maps to exit code 2."""


# --------------------------------------------------------------------------
# Timestamp parsing (no wall-clock reads anywhere in this module)
# --------------------------------------------------------------------------


def parse_utc_timestamp(raw):
    """Parse an ISO-8601 UTC timestamp string into an aware UTC datetime.

    Accepted forms:
      * a trailing 'Z' or 'z' with no embedded offset, e.g. "2026-08-02T00:00:00Z"
      * an explicit zero UTC offset, e.g. "2026-08-02T00:00:00+00:00" or
        "...-00:00"

    Rejected (raises ValueError):
      * any non-string value (including None -- callers must special-case
        a null ``deadline`` before calling this)
      * an empty / whitespace-only string
      * a string that datetime.fromisoformat cannot parse
      * a timezone-naive string (no offset and no 'Z')
      * a string with a non-zero UTC offset (e.g. "+05:30") -- this is
        "non-UTC" per the finding-code contract, even though it is valid
        ISO-8601
      * a string that combines a 'Z' suffix with an embedded offset
    """
    if not isinstance(raw, str):
        raise ValueError("timestamp must be a JSON string, got %s" % type(raw).__name__)
    s = raw.strip()
    if not s:
        raise ValueError("timestamp must be a non-empty string")

    if s[-1] in ("Z", "z"):
        core = s[:-1]
        try:
            dt = datetime.fromisoformat(core)
        except ValueError:
            raise ValueError("unparseable ISO-8601 timestamp: %r" % raw)
        if dt.tzinfo is not None:
            raise ValueError(
                "timestamp combines a 'Z' suffix with an embedded UTC offset: %r" % raw
            )
        return dt.replace(tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError("unparseable ISO-8601 timestamp: %r" % raw)
    if dt.tzinfo is None:
        raise ValueError("timestamp is missing a UTC offset / timezone-naive: %r" % raw)
    if dt.utcoffset() != timedelta(0):
        raise ValueError("timestamp is not expressed in UTC (non-zero offset): %r" % raw)
    return dt.astimezone(timezone.utc)


def iso_z(dt):
    """Render an aware UTC datetime as ISO-8601 with a 'Z' suffix."""
    s = dt.isoformat()
    if s.endswith("+00:00"):
        s = s[: -len("+00:00")] + "Z"
    return s


def format_age(total_seconds):
    """Render an age (in seconds, possibly negative or fractional) as a
    human string of the form "<sign><d>d <h>h <m>m". Sub-minute remainders
    are truncated (the exact value belongs in the accompanying age_seconds
    field, not in this rounded human string)."""
    seconds = int(total_seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes_total = seconds // 60
    days, rem_minutes = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    return f"{sign}{days}d {hours}h {minutes}m"


# --------------------------------------------------------------------------
# Urgency bucket rules
# --------------------------------------------------------------------------
#
# Data-integrity findings (the record itself cannot be trusted) are always
# "critical", regardless of magnitude:
#   MALFORMED_DEADLINE, MALFORMED_CREATED_AT, DEADLINE_BEFORE_CREATED
#
# Time-window-breach findings are bucketed by *how far past* the relevant
# threshold the task is:
#
#   OVERDUE_PROPOSED (overage = now - deadline; only computed when > 0):
#       overage >= 24h            -> critical
#       6h <= overage < 24h       -> warning
#       0  <  overage < 6h        -> info
#
#   STALE_ACCEPTED / STALE_SUBMITTED
#       (age = now - created_at; window = the configured
#        accepted/submitted stale-hours; overage = age - window; only
#        computed when overage > 0, i.e. age > window strictly):
#       overage >= window                 -> critical  (age >= 2x window)
#       window/2 <= overage < window      -> warning   (1.5x <= age < 2x window)
#       0 < overage < window/2            -> info       (1x < age < 1.5x window)
#
# Boundary rule: a breach requires a STRICT inequality (age > window,
# deadline < now). A value exactly equal to the threshold does NOT breach.


def bucket_for_overdue_proposed(overage_seconds):
    if overage_seconds >= 24 * 3600:
        return BUCKET_CRITICAL
    if overage_seconds >= 6 * 3600:
        return BUCKET_WARNING
    return BUCKET_INFO


def bucket_for_stale(age_seconds, window_seconds):
    overage = age_seconds - window_seconds
    if overage >= window_seconds:
        return BUCKET_CRITICAL
    if overage >= window_seconds / 2:
        return BUCKET_WARNING
    return BUCKET_INFO


# --------------------------------------------------------------------------
# Per-task evaluation
# --------------------------------------------------------------------------


def _base_finding(task, code, bucket, message):
    return {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "code": code,
        "bucket": bucket,
        "message": message,
    }


def evaluate_task(task, now, accepted_stale_hours, submitted_stale_hours):
    """Return a list of finding dicts for a single (already key-validated)
    task record. ``now`` must be an aware UTC datetime supplied by the
    caller -- this function never reads the wall clock."""
    findings = []

    task_id = task["task_id"]
    status = task["status"]
    created_raw = task["created_at"]
    deadline_raw = task["deadline"]

    accepted_seconds = accepted_stale_hours * 3600.0
    submitted_seconds = submitted_stale_hours * 3600.0

    created_dt = None
    try:
        created_dt = parse_utc_timestamp(created_raw)
    except ValueError as exc:
        f = _base_finding(task, CODE_MALFORMED_CREATED_AT, BUCKET_CRITICAL, str(exc))
        f["created_at_raw"] = created_raw
        findings.append(f)

    deadline_dt = None
    if deadline_raw is not None:
        try:
            deadline_dt = parse_utc_timestamp(deadline_raw)
        except ValueError as exc:
            f = _base_finding(task, CODE_MALFORMED_DEADLINE, BUCKET_CRITICAL, str(exc))
            f["deadline_raw"] = deadline_raw
            findings.append(f)

    if created_dt is not None and deadline_dt is not None and deadline_dt < created_dt:
        age = (created_dt - deadline_dt).total_seconds()
        f = _base_finding(
            task,
            CODE_DEADLINE_BEFORE_CREATED,
            BUCKET_CRITICAL,
            "deadline is earlier than created_at (deadline was already past when the task was created)",
        )
        f["created_at"] = iso_z(created_dt)
        f["deadline"] = iso_z(deadline_dt)
        f["age_seconds"] = int(round(age))
        f["age_human"] = format_age(age)
        findings.append(f)

    if status == "proposed" and deadline_dt is not None:
        overage = (now - deadline_dt).total_seconds()
        if overage > 0:
            bucket = bucket_for_overdue_proposed(overage)
            f = _base_finding(
                task, CODE_OVERDUE_PROPOSED, bucket, "proposed task's deadline has passed"
            )
            f["deadline"] = iso_z(deadline_dt)
            f["age_seconds"] = int(round(overage))
            f["age_human"] = format_age(overage)
            findings.append(f)

    if status == "accepted" and created_dt is not None:
        age = (now - created_dt).total_seconds()
        if age > accepted_seconds:
            bucket = bucket_for_stale(age, accepted_seconds)
            f = _base_finding(
                task,
                CODE_STALE_ACCEPTED,
                bucket,
                "accepted task has exceeded the accepted-stale window",
            )
            f["created_at"] = iso_z(created_dt)
            f["accepted_stale_hours"] = accepted_stale_hours
            f["age_seconds"] = int(round(age))
            f["age_human"] = format_age(age)
            findings.append(f)

    if status == "submitted" and created_dt is not None:
        age = (now - created_dt).total_seconds()
        if age > submitted_seconds:
            bucket = bucket_for_stale(age, submitted_seconds)
            f = _base_finding(
                task,
                CODE_STALE_SUBMITTED,
                bucket,
                "submitted task has exceeded the submitted-stale window",
            )
            f["created_at"] = iso_z(created_dt)
            f["submitted_stale_hours"] = submitted_stale_hours
            f["age_seconds"] = int(round(age))
            f["age_human"] = format_age(age)
            findings.append(f)

    return findings


# --------------------------------------------------------------------------
# Whole-input validation + report assembly
# --------------------------------------------------------------------------


def _validate_tasks_shape(data):
    if not isinstance(data, list):
        raise InputError("input JSON must be an array of task records")
    for idx, task in enumerate(data):
        if not isinstance(task, dict):
            raise InputError(f"task at index {idx} is not a JSON object")
        missing = [k for k in REQUIRED_KEYS if k not in task]
        if missing:
            raise InputError(
                f"task at index {idx} (task_id={task.get('task_id')!r}) is missing required "
                f"key(s): {', '.join(missing)}"
            )


def build_report(tasks, now, accepted_stale_hours, submitted_stale_hours):
    """Build the full report dict for ``tasks`` at reference time ``now``.

    Returns (report_dict, total_finding_count). Raises InputError if the
    task list is malformed at the *shape* level (not a list, a record is
    not an object, or a record is missing a required key). Per-field value
    problems (bad created_at/deadline strings, unknown status values) are
    NOT shape errors -- they surface as findings or are silently inert,
    per the documented contract.
    """
    _validate_tasks_shape(tasks)

    buckets = {BUCKET_CRITICAL: [], BUCKET_WARNING: [], BUCKET_INFO: []}
    total = 0
    for task in tasks:
        for finding in evaluate_task(task, now, accepted_stale_hours, submitted_stale_hours):
            buckets[finding["bucket"]].append(finding)
            total += 1

    for bucket_name in buckets:
        buckets[bucket_name].sort(key=lambda f: (str(f["task_id"]), f["code"]))

    summary = {
        "total_tasks": len(tasks),
        "total_findings": total,
        "critical": len(buckets[BUCKET_CRITICAL]),
        "warning": len(buckets[BUCKET_WARNING]),
        "info": len(buckets[BUCKET_INFO]),
    }

    report = {
        "generated_at": iso_z(now),
        "windows": {
            "accepted_stale_hours": accepted_stale_hours,
            "submitted_stale_hours": submitted_stale_hours,
        },
        "summary": summary,
        "findings": {
            "critical": buckets[BUCKET_CRITICAL],
            "warning": buckets[BUCKET_WARNING],
            "info": buckets[BUCKET_INFO],
        },
    }
    return report, total


def canonical_json(report):
    """Serialize ``report`` as canonical JSON (sorted keys, compact
    separators, ASCII-only) plus exactly one trailing newline."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="staleness.py",
        description="Find overdue / stale Task Node records and emit canonical JSON findings.",
    )
    parser.add_argument("input_file", help="Path to a JSON file containing an array of task records.")
    parser.add_argument(
        "--now",
        required=True,
        help="UTC reference time in ISO-8601 (e.g. 2026-08-02T00:00:00Z). Required; never read from the system clock.",
    )
    parser.add_argument(
        "--accepted-stale-hours",
        type=float,
        default=DEFAULT_ACCEPTED_STALE_HOURS,
        help=f"Hours after created_at an accepted task is considered stale (default: {DEFAULT_ACCEPTED_STALE_HOURS}).",
    )
    parser.add_argument(
        "--submitted-stale-hours",
        type=float,
        default=DEFAULT_SUBMITTED_STALE_HOURS,
        help=f"Hours after created_at a submitted task is considered stale (default: {DEFAULT_SUBMITTED_STALE_HOURS}).",
    )
    parser.add_argument("-o", "--output", help="Write the JSON report to this path instead of stdout.")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits(2) on usage errors

    try:
        now = parse_utc_timestamp(args.now)
    except ValueError as exc:
        print(f"staleness.py: error: invalid --now value: {exc}", file=sys.stderr)
        return 2

    if args.accepted_stale_hours < 0 or args.submitted_stale_hours < 0:
        print("staleness.py: error: --accepted-stale-hours/--submitted-stale-hours must be >= 0", file=sys.stderr)
        return 2

    try:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"staleness.py: error: input file not found: {args.input_file}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"staleness.py: error: could not read input file: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"staleness.py: error: input file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        report, total_findings = build_report(
            data, now, args.accepted_stale_hours, args.submitted_stale_hours
        )
    except InputError as exc:
        print(f"staleness.py: error: {exc}", file=sys.stderr)
        return 2

    out = canonical_json(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
        except OSError as exc:
            print(f"staleness.py: error: could not write output file: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(out)

    return 1 if total_findings > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

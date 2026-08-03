#!/usr/bin/env python3
"""loop_health.py -- stdlib-only Loop Health CLI for exported task histories.

Reads an array of task history records (JSON) -- each record a task_id plus
its chronological list of lifecycle events -- and reports:

  * resubmission_rounds per task -- the number of times the task's event
    sequence went verification_requested -> submitted (a strict, adjacent
    transition; see "Resubmission rounds" in README.md). Zero rounds is
    healthy and is reported as data, never as a finding.
  * REVIEW_OVERDUE       -- the task's latest known state is awaiting_review
    or submitted, and its age against the injected --now exceeds
    --review-overdue-hours.
  * EXCESSIVE_RESUBMISSIONS -- a task's resubmission_rounds exceeds
    --max-rounds.
  * refusal_reason distribution -- counts per distinct refusal_reason across
    every well-formed "refused" event in the whole input.
  * MALFORMED_RECORD    -- a task record or one of its events fails the
    structural shape contract (see README.md "Input shape").
  * INVALID_TIMESTAMP   -- an event's "at" value is present as a string but
    fails to parse as a UTC ISO-8601 timestamp.
  * UNKNOWN_STATE        -- an event's "state" is a valid non-empty string
    but is not one of the seven known lifecycle states.
  * EMPTY_HISTORY        -- a task record's "events" array is present, is a
    JSON array, and has zero elements.

Output is emitted as canonical JSON:

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

plus a single trailing newline.

Reproducibility contract
-------------------------
The wall clock is never consulted anywhere in the report path. The UTC
reference moment used for every age / overdue computation is supplied
exclusively via the required --now command-line argument and threaded
explicitly through every function that needs it, as an ordinary parameter.
This module makes no wall-clock lookup of any kind (see README.md /
captured_output.txt for a grep proof over this file's source).

This convention -- and the parse_utc_timestamp / format_age helpers below --
were matched deliberately from the sibling tool staleness-monitor
(staleness.py), which established the "injected --now, never read the wall
clock" pattern first. See README.md, "What we matched from staleness-monitor".

Exit codes
----------
  0  -- input parsed successfully and no findings were produced.
  1  -- input parsed successfully and at least one finding was produced.
  2  -- invalid input or usage error (missing/unparseable --now, unreadable
        or malformed input file, input JSON whose root is not a list,
        negative --max-rounds / --review-overdue-hours, etc). Note: a
        malformed *record* inside an otherwise-valid array is NOT a usage
        error -- it is reported as a MALFORMED_RECORD finding (exit 1),
        exactly as the task spec requires.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

__all__ = [
    "parse_utc_timestamp",
    "iso_z",
    "format_age",
    "process_task",
    "build_report",
    "canonical_json",
    "InputError",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ALLOWED_STATES = (
    "proposed",
    "accepted",
    "submitted",
    "verification_requested",
    "awaiting_review",
    "rewarded",
    "refused",
)

# States for which REVIEW_OVERDUE is even eligible to fire, when they are
# the task's *latest* chronological state.
OVERDUE_ELIGIBLE_STATES = ("awaiting_review", "submitted")

CODE_MALFORMED_RECORD = "MALFORMED_RECORD"
CODE_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
CODE_UNKNOWN_STATE = "UNKNOWN_STATE"
CODE_EMPTY_HISTORY = "EMPTY_HISTORY"
CODE_REVIEW_OVERDUE = "REVIEW_OVERDUE"
CODE_EXCESSIVE_RESUBMISSIONS = "EXCESSIVE_RESUBMISSIONS"

ALL_CODES = (
    CODE_MALFORMED_RECORD,
    CODE_INVALID_TIMESTAMP,
    CODE_UNKNOWN_STATE,
    CODE_EMPTY_HISTORY,
    CODE_REVIEW_OVERDUE,
    CODE_EXCESSIVE_RESUBMISSIONS,
)

DEFAULT_MAX_ROUNDS = 3
DEFAULT_REVIEW_OVERDUE_HOURS = 72

# Sentinel distinguishing "key absent" from "key present with value None",
# since JSON null is a legitimate (if unhelpful) value for some fields.
_MISSING = object()


class InputError(Exception):
    """Raised for invalid input / usage problems. Maps to exit code 2."""


# --------------------------------------------------------------------------
# Timestamp parsing (matched from staleness.py; no wall-clock reads here)
# --------------------------------------------------------------------------


def parse_utc_timestamp(raw):
    """Parse an ISO-8601 UTC timestamp string into an aware UTC datetime.

    Accepted forms:
      * a trailing 'Z' or 'z' with no embedded offset, e.g. "2026-08-02T00:00:00Z"
      * an explicit zero UTC offset, e.g. "2026-08-02T00:00:00+00:00" or
        "...-00:00"

    Rejected (raises ValueError):
      * any non-string value
      * an empty / whitespace-only string
      * a string that datetime.fromisoformat cannot parse
      * a timezone-naive string (no offset and no 'Z')
      * a string with a non-zero UTC offset (e.g. "+05:30")
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
# Finding construction
# --------------------------------------------------------------------------


def _finding(task_id, code, message, extra=None):
    f = {"task_id": task_id, "code": code, "message": message}
    if extra:
        f.update(extra)
    return f


def _index_ref(idx):
    return f"<index:{idx}>"


# --------------------------------------------------------------------------
# Per-event refusal_reason extraction
# --------------------------------------------------------------------------


def _refusal_reason_for(task_id, j, ev, state, findings):
    """Return the refusal_reason string to count in the distribution for
    this event, or None if nothing should be counted. Also appends a
    MALFORMED_RECORD finding if refusal_reason is present but not a string.

    Rules (documented in README.md):
      * refusal_reason absent, or explicitly JSON null -> not counted, not
        an error (lenient).
      * refusal_reason present but not a string -> MALFORMED_RECORD, not
        counted.
      * refusal_reason present as a string (including "") on an event whose
        state is NOT "refused" -> ignored for the distribution, not an
        error (documented behavior; see README.md limitations).
      * refusal_reason present as a string (including "") on a "refused"
        event -> counted, verbatim, as one distinct distribution bucket.
    """
    if "refusal_reason" not in ev:
        return None
    val = ev["refusal_reason"]
    if val is None:
        return None
    if not isinstance(val, str):
        findings.append(
            _finding(
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} event at index {j} has a non-string refusal_reason",
                extra={"event_index": j},
            )
        )
        return None
    if state != "refused":
        return None
    return val


# --------------------------------------------------------------------------
# Per-task processing
# --------------------------------------------------------------------------


def process_task(idx, record, now, max_rounds, review_overdue_hours):
    """Process a single top-level array element.

    Returns (findings, resubmission_rounds_or_None, refusal_reasons).

    ``resubmission_rounds_or_None`` is None only when the record itself is
    unusable at the structural level (not an object, missing task_id,
    missing/non-array events) -- in that case the record contributes no
    resubmission_rounds entry and no refusal_reasons at all, only the
    MALFORMED_RECORD finding(s) already appended.
    """
    findings = []

    if not isinstance(record, dict):
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} is not a JSON object",
                extra={"record_index": idx},
            )
        )
        return findings, None, []

    if "task_id" not in record:
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} is missing required key: task_id",
                extra={"record_index": idx},
            )
        )
        return findings, None, []

    task_id = record["task_id"]
    if not isinstance(task_id, str) or task_id == "":
        findings.append(
            _finding(
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} has an invalid task_id (must be a non-empty "
                f"JSON string): {task_id!r}",
                extra={"record_index": idx},
            )
        )
        return findings, None, []

    if "events" not in record:
        findings.append(
            _finding(
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} is missing required key: events",
            )
        )
        return findings, None, []

    events = record["events"]
    if not isinstance(events, list):
        findings.append(
            _finding(
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} 'events' must be a JSON array",
            )
        )
        return findings, None, []

    if len(events) == 0:
        findings.append(
            _finding(task_id, CODE_EMPTY_HISTORY, f"task {task_id!r} has zero events")
        )
        return findings, 0, []

    timed_events = []  # list of (dt, original_index, state)
    refusal_reasons = []

    for j, ev in enumerate(events):
        if not isinstance(ev, dict):
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} event at index {j} is not a JSON object",
                    extra={"event_index": j},
                )
            )
            continue

        state = ev.get("state", _MISSING)
        if state is _MISSING or not isinstance(state, str) or state == "":
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} event at index {j} has a missing or invalid "
                    f"'state' (must be a non-empty string)",
                    extra={"event_index": j},
                )
            )
            continue

        if state not in ALLOWED_STATES:
            findings.append(
                _finding(
                    task_id,
                    CODE_UNKNOWN_STATE,
                    f"task {task_id!r} event at index {j} has unknown state {state!r}",
                    extra={"event_index": j, "state": state},
                )
            )

        at_raw = ev.get("at", _MISSING)
        dt = None
        if at_raw is _MISSING or not isinstance(at_raw, str):
            findings.append(
                _finding(
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} event at index {j} is missing required key: at",
                    extra={"event_index": j},
                )
            )
        else:
            try:
                dt = parse_utc_timestamp(at_raw)
            except ValueError as exc:
                findings.append(
                    _finding(
                        task_id,
                        CODE_INVALID_TIMESTAMP,
                        f"task {task_id!r} event at index {j}: {exc}",
                        extra={"event_index": j, "at_raw": at_raw},
                    )
                )

        if dt is not None:
            timed_events.append((dt, j, state))

        reason = _refusal_reason_for(task_id, j, ev, state, findings)
        if reason is not None:
            refusal_reasons.append(reason)

    # Deterministic chronological order: sort by (timestamp, original
    # position in the input array). The explicit original-index tiebreak
    # means that when two events share an identical "at", the one that
    # appeared LATER in the input array is treated as the later event --
    # this is a documented, tested convention (see README.md "Identical
    # timestamps"), not an accident of Python's stable sort.
    timed_events.sort(key=lambda t: (t[0], t[1]))

    rounds = 0
    for a, b in zip(timed_events, timed_events[1:]):
        if a[2] == "verification_requested" and b[2] == "submitted":
            rounds += 1

    if rounds > max_rounds:
        findings.append(
            _finding(
                task_id,
                CODE_EXCESSIVE_RESUBMISSIONS,
                f"task {task_id!r} has {rounds} resubmission round(s), exceeding "
                f"max_rounds={max_rounds}",
                extra={"rounds": rounds, "max_rounds": max_rounds},
            )
        )

    if timed_events:
        latest_dt, _latest_j, latest_state = timed_events[-1]
        if latest_state in OVERDUE_ELIGIBLE_STATES:
            age = (now - latest_dt).total_seconds()
            threshold_seconds = review_overdue_hours * 3600.0
            if age > threshold_seconds:
                findings.append(
                    _finding(
                        task_id,
                        CODE_REVIEW_OVERDUE,
                        f"task {task_id!r} latest state {latest_state!r} has been "
                        f"waiting {format_age(age)}, exceeding the "
                        f"{review_overdue_hours}h review-overdue threshold",
                        extra={
                            "state": latest_state,
                            "since": iso_z(latest_dt),
                            "age_seconds": int(round(age)),
                            "age_human": format_age(age),
                            "review_overdue_hours": review_overdue_hours,
                        },
                    )
                )

    return findings, rounds, refusal_reasons


# --------------------------------------------------------------------------
# Whole-input report assembly
# --------------------------------------------------------------------------


def build_report(data, now, max_rounds, review_overdue_hours):
    """Build the full report dict for ``data`` at reference time ``now``.

    Returns (report_dict, total_finding_count). Raises InputError only if
    ``data`` itself is not a JSON array -- that is the sole *usage*-level
    shape requirement. Everything below the top-level array (malformed
    records, malformed events, unknown states, bad timestamps, empty
    histories) is reported as findings, never as InputError.
    """
    if not isinstance(data, list):
        raise InputError("input JSON must be an array of task history records")

    all_findings = []
    resubmission_entries = []
    refusal_counter = {}

    for idx, record in enumerate(data):
        findings, rounds, refusal_reasons = process_task(
            idx, record, now, max_rounds, review_overdue_hours
        )
        all_findings.extend(findings)
        if rounds is not None:
            task_id = record["task_id"] if isinstance(record, dict) else _index_ref(idx)
            resubmission_entries.append(
                {"task_id": task_id, "resubmission_rounds": rounds, "_record_index": idx}
            )
        for reason in refusal_reasons:
            refusal_counter[reason] = refusal_counter.get(reason, 0) + 1

    resubmission_entries.sort(key=lambda e: (str(e["task_id"]), e["_record_index"]))
    for e in resubmission_entries:
        del e["_record_index"]

    distribution = [{"reason": r, "count": c} for r, c in refusal_counter.items()]
    distribution.sort(key=lambda d: (-d["count"], d["reason"]))

    # Final sort of findings: (task_id, code, event_index-or-(-1), full
    # canonical dump) -- the canonical-dump tiebreak guarantees a total
    # deterministic order even when two findings share task_id/code/index.
    all_findings.sort(
        key=lambda f: (
            str(f["task_id"]),
            f["code"],
            f.get("event_index", -1),
            json.dumps(f, sort_keys=True, ensure_ascii=True),
        )
    )

    total = len(all_findings)

    counts_by_code = {code: 0 for code in ALL_CODES}
    for f in all_findings:
        counts_by_code[f["code"]] += 1

    summary = {
        "total_tasks": len(data),
        "total_findings": total,
        "counts_by_code": counts_by_code,
    }

    report = {
        "generated_at": iso_z(now),
        "options": {
            "max_rounds": max_rounds,
            "review_overdue_hours": review_overdue_hours,
        },
        "summary": summary,
        "resubmission_rounds": resubmission_entries,
        "refusal_reason_distribution": distribution,
        "findings": all_findings,
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
        prog="loop_health.py",
        description=(
            "Report resubmission rounds, overdue reviews, and refusal-reason "
            "distribution from exported task histories; emit canonical JSON."
        ),
    )
    parser.add_argument(
        "input_file", help="Path to a JSON file containing an array of task history records."
    )
    parser.add_argument(
        "--now",
        required=True,
        help=(
            "UTC reference moment in ISO-8601 (e.g. 2026-08-03T00:00:00Z). Required; "
            "this value is never defaulted and the wall clock is never consulted."
        ),
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=(
            "A task with resubmission_rounds strictly greater than this value "
            "triggers EXCESSIVE_RESUBMISSIONS (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--review-overdue-hours",
        type=float,
        default=DEFAULT_REVIEW_OVERDUE_HOURS,
        help=(
            "Hours after a task's latest awaiting_review/submitted event before "
            "REVIEW_OVERDUE fires (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "-o", "--output", help="Write the JSON report to this path instead of stdout."
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits(2) on usage errors

    try:
        now = parse_utc_timestamp(args.now)
    except ValueError as exc:
        print(f"loop_health.py: error: invalid --now value: {exc}", file=sys.stderr)
        return 2

    if args.max_rounds < 0:
        print("loop_health.py: error: --max-rounds must be >= 0", file=sys.stderr)
        return 2

    if args.review_overdue_hours < 0:
        print("loop_health.py: error: --review-overdue-hours must be >= 0", file=sys.stderr)
        return 2

    try:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"loop_health.py: error: input file not found: {args.input_file}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"loop_health.py: error: could not read input file: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"loop_health.py: error: input file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        report, total_findings = build_report(
            data, now, args.max_rounds, args.review_overdue_hours
        )
    except InputError as exc:
        print(f"loop_health.py: error: {exc}", file=sys.stderr)
        return 2

    out = canonical_json(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
        except OSError as exc:
            print(f"loop_health.py: error: could not write output file: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(out)

    return 1 if total_findings > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

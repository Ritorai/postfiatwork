#!/usr/bin/env python3
"""link_integrity.py

Stdlib-only CLI that checks CROSS-EXPORT link integrity between an exported
task-lifecycle stream and an exported evidence-submission stream.

This tool deliberately does NOT re-implement:
  - JSON schema validation (field types/enums beyond the bare minimum needed
    to perform cross-export linking),
  - lifecycle transition-order validation (e.g. whether "submitted" may
    legally follow "rewarded").

Those are the responsibility of other, existing linters. This tool answers a
narrower question: "do the two exports agree with each other?"

See README.md for the full input/output contract.

Exit codes:
  0 = no violations found (clean)
  1 = violations found
  2 = invalid input or usage error (bad JSON, wrong shape, missing file, ...)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

PROG = "link_integrity.py"

TERMINAL_STATES = ("rewarded", "refused")
SUBMITTED_STATE = "submitted"

MIN_YEAR = 2000
MAX_YEAR = 2100

_TS_RE = re.compile(
    r'^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})'
    r'T(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})'
    r'(?P<frac>\.\d+)?'
    r'(?P<tz>Z|\+00:00)$'
)


class InputError(Exception):
    """Raised for invalid input / usage problems -> exit code 2."""


def parse_utc_timestamp(raw):
    """Strictly parse an ISO-8601 UTC timestamp.

    Accepts only 'Z' or a literal '+00:00' offset as UTC designators (any
    other offset is treated as non-UTC and therefore impossible for this
    tool's purposes). Returns (datetime_or_None, error_reason_or_None).
    """
    if not isinstance(raw, str):
        return None, "timestamp is not a string"
    m = _TS_RE.match(raw)
    if not m:
        return None, ("does not match required ISO-8601 UTC format "
                       "(expected e.g. '2026-01-01T00:00:00Z' or "
                       "'2026-01-01T00:00:00+00:00'; other UTC offsets "
                       "are rejected as non-UTC)")
    year = int(m.group("y"))
    if year < MIN_YEAR or year > MAX_YEAR:
        return None, "year %d outside supported range %d-%d" % (year, MIN_YEAR, MAX_YEAR)
    month = int(m.group("mo"))
    day = int(m.group("d"))
    hour = int(m.group("h"))
    minute = int(m.group("mi"))
    second = int(m.group("s"))
    frac = m.group("frac")
    micro = 0
    if frac:
        digits = (frac[1:] + "000000")[:6]
        micro = int(digits)
    try:
        dt = datetime(year, month, day, hour, minute, second, micro, tzinfo=timezone.utc)
    except ValueError as exc:
        return None, "invalid calendar date/time (%s)" % (exc,)
    return dt, None


def _load_json_array(path, label):
    try:
        # utf-8-sig transparently strips a leading UTF-8 BOM if present
        # (common in exports produced by Windows tooling) while behaving
        # identically to plain utf-8 when no BOM is present.
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise InputError("%s file not found: %s" % (label, path))
    except IsADirectoryError:
        raise InputError("%s path is a directory, not a file: %s" % (label, path))
    except OSError as exc:
        raise InputError("could not read %s file %s: %s" % (label, path, exc))

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError("%s file is not valid JSON: %s" % (label, exc))

    if not isinstance(data, list):
        raise InputError("%s file must contain a JSON array at the top level" % (label,))
    return data


def _require_nonempty_string(value, field, label, index):
    if not isinstance(value, str) or value == "":
        raise InputError(
            "%s record at index %d has invalid '%s' field "
            "(must be a non-empty string, got %r)" % (label, index, field, value)
        )
    return value


def validate_lifecycle_records(raw_list):
    """Structural validation only (exit 2 territory). Returns list of dicts
    with keys: index, task_id, state, at (raw string)."""
    out = []
    for i, rec in enumerate(raw_list):
        if not isinstance(rec, dict):
            raise InputError("lifecycle event at index %d is not a JSON object" % (i,))
        for key in ("task_id", "state", "at"):
            if key not in rec:
                raise InputError(
                    "lifecycle event at index %d missing required field '%s'" % (i, key)
                )
        task_id = _require_nonempty_string(rec["task_id"], "task_id", "lifecycle", i)
        state = _require_nonempty_string(rec["state"], "state", "lifecycle", i)
        at = rec["at"]
        if not isinstance(at, str):
            raise InputError(
                "lifecycle event at index %d has invalid 'at' field "
                "(must be a string timestamp, got %r)" % (i, at)
            )
        out.append({"index": i, "task_id": task_id, "state": state, "at": at})
    return out


def validate_evidence_records(raw_list):
    """Structural validation only (exit 2 territory). Returns list of dicts
    with keys: index, submission_id, task_id, submitted_at (raw string)."""
    out = []
    for i, rec in enumerate(raw_list):
        if not isinstance(rec, dict):
            raise InputError("evidence record at index %d is not a JSON object" % (i,))
        for key in ("submission_id", "task_id", "submitted_at"):
            if key not in rec:
                raise InputError(
                    "evidence record at index %d missing required field '%s'" % (i, key)
                )
        submission_id = _require_nonempty_string(
            rec["submission_id"], "submission_id", "evidence", i
        )
        task_id = _require_nonempty_string(rec["task_id"], "task_id", "evidence", i)
        submitted_at = rec["submitted_at"]
        if not isinstance(submitted_at, str):
            raise InputError(
                "evidence record at index %d has invalid 'submitted_at' field "
                "(must be a string timestamp, got %r)" % (i, submitted_at)
            )
        out.append({
            "index": i,
            "submission_id": submission_id,
            "task_id": task_id,
            "submitted_at": submitted_at,
        })
    return out


def _mk_violation(code, message, **fields):
    v = {"code": code, "message": message}
    v.update(fields)
    return v


def check_links(lifecycle, evidence):
    """Core cross-export link-checking logic.

    lifecycle: list of validated lifecycle dicts (index, task_id, state, at)
    evidence: list of validated evidence dicts (index, submission_id, task_id,
              submitted_at)

    Returns a list of violation dicts (unsorted).
    """
    violations = []

    # --- Parse all lifecycle timestamps up front -------------------------
    lifecycle_dt = {}  # index -> datetime or None
    for rec in lifecycle:
        dt, err = parse_utc_timestamp(rec["at"])
        lifecycle_dt[rec["index"]] = dt
        if err is not None:
            violations.append(_mk_violation(
                "IMPOSSIBLE_TIMESTAMP",
                "lifecycle event for task_id=%r has an impossible 'at' "
                "timestamp %r: %s" % (rec["task_id"], rec["at"], err),
                source="lifecycle",
                task_id=rec["task_id"],
                submission_id=None,
                field="at",
                value=rec["at"],
                reason=err,
                index=rec["index"],
            ))

    # --- Parse all evidence timestamps up front ---------------------------
    evidence_dt = {}  # index -> datetime or None
    for rec in evidence:
        dt, err = parse_utc_timestamp(rec["submitted_at"])
        evidence_dt[rec["index"]] = dt
        if err is not None:
            violations.append(_mk_violation(
                "IMPOSSIBLE_TIMESTAMP",
                "evidence submission_id=%r has an impossible 'submitted_at' "
                "timestamp %r: %s" % (rec["submission_id"], rec["submitted_at"], err),
                source="evidence",
                task_id=rec["task_id"],
                submission_id=rec["submission_id"],
                field="submitted_at",
                value=rec["submitted_at"],
                reason=err,
                index=rec["index"],
            ))

    # --- Build per-task lifecycle knowledge --------------------------------
    lifecycle_task_ids = set(rec["task_id"] for rec in lifecycle)

    task_created_dt = {}       # task_id -> earliest valid datetime among all events
    task_terminal = {}         # task_id -> (dt, state) earliest terminal occurrence
    task_has_submitted = {}    # task_id -> True/False
    task_first_submitted_at = {}  # task_id -> raw 'at' string of first submitted event (index order)

    for rec in lifecycle:
        tid = rec["task_id"]
        dt = lifecycle_dt[rec["index"]]

        if dt is not None:
            cur = task_created_dt.get(tid)
            if cur is None or dt < cur:
                task_created_dt[tid] = dt

            if rec["state"] in TERMINAL_STATES:
                cur_t = task_terminal.get(tid)
                if cur_t is None or dt < cur_t[0]:
                    task_terminal[tid] = (dt, rec["state"])

        if rec["state"] == SUBMITTED_STATE:
            task_has_submitted[tid] = True
            if tid not in task_first_submitted_at:
                task_first_submitted_at[tid] = rec["at"]

    # --- DUPLICATE_SUBMISSION_ID -------------------------------------------
    by_submission = {}
    for rec in evidence:
        by_submission.setdefault(rec["submission_id"], []).append(rec)

    for sub_id, recs in by_submission.items():
        if len(recs) > 1:
            task_ids = sorted(set(r["task_id"] for r in recs))
            indices = sorted(r["index"] for r in recs)
            violations.append(_mk_violation(
                "DUPLICATE_SUBMISSION_ID",
                "submission_id %r appears %d times (expected unique), "
                "referencing task_id(s) %s" % (sub_id, len(recs), task_ids),
                submission_id=sub_id,
                count=len(recs),
                task_ids=task_ids,
                evidence_indices=indices,
            ))

    # --- Per-evidence-record checks -----------------------------------
    evidence_task_ids_seen = set()
    for rec in evidence:
        tid = rec["task_id"]
        evidence_task_ids_seen.add(tid)
        ev_dt = evidence_dt[rec["index"]]

        if tid not in lifecycle_task_ids:
            violations.append(_mk_violation(
                "UNKNOWN_TASK_REFERENCE",
                "evidence submission_id=%r references task_id=%r which has "
                "no lifecycle events" % (rec["submission_id"], tid),
                task_id=tid,
                submission_id=rec["submission_id"],
                submitted_at=rec["submitted_at"],
                evidence_index=rec["index"],
            ))
            # Task is unknown: no created/terminal info to compare against.
            continue

        if ev_dt is None:
            # Already reported as IMPOSSIBLE_TIMESTAMP; cannot compare times.
            continue

        created_dt = task_created_dt.get(tid)
        if created_dt is not None and ev_dt < created_dt:
            violations.append(_mk_violation(
                "EVIDENCE_BEFORE_TASK_CREATED",
                "evidence submission_id=%r for task_id=%r was submitted at "
                "%s, before the task's first lifecycle event at %s"
                % (rec["submission_id"], tid, rec["submitted_at"],
                   created_dt.isoformat().replace("+00:00", "Z")),
                task_id=tid,
                submission_id=rec["submission_id"],
                submitted_at=rec["submitted_at"],
                task_created_at=created_dt.isoformat().replace("+00:00", "Z"),
                evidence_index=rec["index"],
            ))

        terminal = task_terminal.get(tid)
        if terminal is not None:
            terminal_dt, terminal_state = terminal
            if ev_dt > terminal_dt:
                violations.append(_mk_violation(
                    "EVIDENCE_AFTER_TERMINAL_STATE",
                    "evidence submission_id=%r for task_id=%r was submitted "
                    "at %s, after the task reached terminal state %r at %s"
                    % (rec["submission_id"], tid, rec["submitted_at"],
                       terminal_state, terminal_dt.isoformat().replace("+00:00", "Z")),
                    task_id=tid,
                    submission_id=rec["submission_id"],
                    submitted_at=rec["submitted_at"],
                    terminal_state=terminal_state,
                    terminal_at=terminal_dt.isoformat().replace("+00:00", "Z"),
                    evidence_index=rec["index"],
                ))

    # --- MISSING_EVIDENCE_FOR_SUBMITTED_STATE -------------------------
    # (Note: a task with no evidence and no 'submitted' state is an
    # ORPHAN_LIFECYCLE_TASK situation, which is explicitly NOT a violation.)
    for tid, has_sub in task_has_submitted.items():
        if not has_sub:
            continue
        if tid not in evidence_task_ids_seen:
            violations.append(_mk_violation(
                "MISSING_EVIDENCE_FOR_SUBMITTED_STATE",
                "task_id=%r has a 'submitted' lifecycle event at %s but no "
                "evidence record references it" % (tid, task_first_submitted_at.get(tid)),
                task_id=tid,
                submitted_state_at=task_first_submitted_at.get(tid),
            ))

    return violations


def _violation_sort_key(v):
    task_id = v.get("task_id")
    submission_id = v.get("submission_id")
    return (
        v.get("code", ""),
        "" if task_id is None else task_id,
        "" if submission_id is None else submission_id,
        json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
    )


def build_report(lifecycle_raw, evidence_raw):
    lifecycle = validate_lifecycle_records(lifecycle_raw)
    evidence = validate_evidence_records(evidence_raw)

    violations = check_links(lifecycle, evidence)
    violations.sort(key=_violation_sort_key)

    counts_by_code = {}
    for v in violations:
        counts_by_code[v["code"]] = counts_by_code.get(v["code"], 0) + 1

    report = {
        "schema_version": "1.0",
        "summary": {
            "is_clean": len(violations) == 0,
            "violation_count": len(violations),
            "counts_by_code": counts_by_code,
        },
        "violations": violations,
    }
    return report


def to_canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Check cross-export link integrity between a task-lifecycle "
            "export and an evidence-submission export."
        ),
    )
    parser.add_argument("lifecycle_file", help="path to lifecycle export JSON array")
    parser.add_argument("evidence_file", help="path to evidence export JSON array")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="write canonical JSON report to this path instead of stdout",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # argparse itself exits(2) on usage errors

    try:
        lifecycle_raw = _load_json_array(args.lifecycle_file, "lifecycle")
        evidence_raw = _load_json_array(args.evidence_file, "evidence")
        report = build_report(lifecycle_raw, evidence_raw)
    except InputError as exc:
        print("%s: error: %s" % (PROG, exc), file=sys.stderr)
        return 2

    text = to_canonical_json(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            print("%s: error: could not write output file %s: %s"
                  % (PROG, args.output, exc), file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)

    return 1 if report["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""preflight.py -- Cross-file readiness checks for contributor submissions.

Stdlib-only CLI that checks a contributor's task export against its evidence
export before submission. See README.md for the full specification of the
task/evidence record shapes and every issue code this tool can emit.

Usage:
    python3 preflight.py TASKS_FILE EVIDENCE_FILE [-o OUTPUT_FILE]
    python3 -m preflight TASKS_FILE EVIDENCE_FILE [-o OUTPUT_FILE]

Exit codes:
    0 - ready to submit (no issues found)
    1 - issues found
    2 - invalid input or usage error (bad CLI usage, missing/unreadable file,
        malformed JSON, or a JSON top-level value that is neither an object
        nor an array)
"""

import argparse
import json
import sys

PROG = "preflight"

# ---------------------------------------------------------------------------
# Issue codes (exported as module-level constants so callers/tests can refer
# to them by name instead of by string literal).
# ---------------------------------------------------------------------------
ORPHAN_EVIDENCE = "ORPHAN_EVIDENCE"
TASK_MISSING_EVIDENCE = "TASK_MISSING_EVIDENCE"
EVIDENCE_TYPE_MISMATCH = "EVIDENCE_TYPE_MISMATCH"
EMPTY_EVIDENCE_VALUE = "EMPTY_EVIDENCE_VALUE"
DUPLICATE_SUBMISSION_ID = "DUPLICATE_SUBMISSION_ID"
MALFORMED_RECORD = "MALFORMED_RECORD"
UNSUBMITTABLE_STATUS = "UNSUBMITTABLE_STATUS"

ALL_CODES = (
    ORPHAN_EVIDENCE,
    TASK_MISSING_EVIDENCE,
    EVIDENCE_TYPE_MISMATCH,
    EMPTY_EVIDENCE_VALUE,
    DUPLICATE_SUBMISSION_ID,
    MALFORMED_RECORD,
    UNSUBMITTABLE_STATUS,
)

# Statuses for which submitting evidence makes no sense.
UNSUBMITTABLE_STATUSES = frozenset({"refused", "rewarded"})

EVIDENCE_REQUIRED_STRING_FIELDS = ("submission_id", "task_id", "evidence_type")


class PreflightInputError(Exception):
    """Structural input problem: bad JSON, bad top-level shape, or file IO."""


def _is_unicode_whitespace_only(s):
    """True if s is a non-empty string made entirely of whitespace.

    Uses str.isspace(), which is unicode-aware: it recognizes not just
    ASCII space/tab/newline but also characters such as U+00A0 (NO-BREAK
    SPACE), U+2000-U+200A (various typographic spaces), and U+3000
    (IDEOGRAPHIC SPACE).
    """
    return len(s) > 0 and s.isspace()


def load_records(path):
    """Load a JSON file that holds either a single record object or an
    array of record objects. Always returns a list (possibly empty, and
    possibly containing non-dict items if the input JSON was malformed at
    the item level -- that is reported later as MALFORMED_RECORD, not
    treated as a fatal error here).

    Raises PreflightInputError for structural problems: missing file,
    unreadable file, invalid JSON syntax, or a top-level JSON value that is
    neither an object nor an array.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise PreflightInputError("file not found: {}".format(path))
    except IsADirectoryError:
        raise PreflightInputError("expected a file, found a directory: {}".format(path))
    except OSError as exc:
        raise PreflightInputError("could not read {}: {}".format(path, exc))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightInputError("invalid JSON in {}: {}".format(path, exc))

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise PreflightInputError(
        "top-level JSON in {} must be an object or an array of objects, got {}".format(
            path, type(data).__name__
        )
    )


def _issue(code, **fields):
    d = {"code": code}
    d.update(fields)
    return d


def _sort_key(issue):
    """Deterministic total ordering over issue dicts: sort by code, then by
    every other field name (alphabetically) and its JSON-serialized value.
    This makes the issue list -- and therefore the whole canonical JSON
    report -- independent of input record order and of dict insertion
    order, so repeated runs on the same logical input are byte-identical.
    """
    parts = [issue.get("code", "")]
    for k in sorted(issue.keys()):
        if k == "code":
            continue
        parts.append(k)
        parts.append(json.dumps(issue[k], sort_keys=True, default=str))
    return tuple(parts)


def _check_task_record(index, record, issues):
    """Validate a single raw task record.

    Returns (task_id, valid): task_id is the record's task_id if it is a
    non-empty string, else None. valid is False if any required field is
    missing or wrong-typed (a MALFORMED_RECORD issue is appended for each
    such field).
    """
    if not isinstance(record, dict):
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="task",
                index=index,
                field=None,
                message="task record at index {} is not a JSON object".format(index),
            )
        )
        return None, False

    valid = True
    task_id = record.get("task_id")

    if not isinstance(task_id, str) or task_id == "":
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="task",
                index=index,
                field="task_id",
                message="task record at index {} has a missing or invalid task_id".format(index),
            )
        )
        valid = False

    if "title" not in record or not isinstance(record.get("title"), str):
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="task",
                index=index,
                field="title",
                message="task record at index {} has a missing or non-string title".format(index),
            )
        )
        valid = False

    status = record.get("status")
    if "status" not in record or not isinstance(status, str) or status == "":
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="task",
                index=index,
                field="status",
                message="task record at index {} has a missing or invalid status".format(index),
            )
        )
        valid = False

    req_ev = record.get("required_evidence")
    if not isinstance(req_ev, list) or not all(isinstance(x, str) for x in req_ev):
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="task",
                index=index,
                field="required_evidence",
                message="task record at index {} has a missing or invalid required_evidence list".format(
                    index
                ),
            )
        )
        valid = False

    resolved_task_id = task_id if isinstance(task_id, str) and task_id != "" else None
    return resolved_task_id, valid


def _check_evidence_record(index, record, issues):
    """Validate a single raw evidence record. Returns True if all required
    fields are present with the correct type (a MALFORMED_RECORD issue is
    appended for each field that is not)."""
    if not isinstance(record, dict):
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="evidence",
                index=index,
                field=None,
                message="evidence record at index {} is not a JSON object".format(index),
            )
        )
        return False

    valid = True
    for field in EVIDENCE_REQUIRED_STRING_FIELDS:
        v = record.get(field)
        if not isinstance(v, str) or v == "":
            issues.append(
                _issue(
                    MALFORMED_RECORD,
                    record_type="evidence",
                    index=index,
                    field=field,
                    message="evidence record at index {} has a missing or invalid {}".format(
                        index, field
                    ),
                )
            )
            valid = False

    if "value" not in record:
        issues.append(
            _issue(
                MALFORMED_RECORD,
                record_type="evidence",
                index=index,
                field="value",
                message="evidence record at index {} is missing the value field".format(index),
            )
        )
        valid = False

    return valid


def _value_is_empty(value):
    """True if value counts as EMPTY_EVIDENCE_VALUE: missing (caller passes
    None for that), JSON null, an empty string, or a whitespace-only string
    (unicode-aware). Non-string values (numbers, lists, objects, booleans)
    are never considered empty by this function, even falsy ones like 0,
    False, [], or {} -- see README "Limitations" for why."""
    if value is None:
        return True
    if isinstance(value, str):
        if value == "":
            return True
        if _is_unicode_whitespace_only(value):
            return True
        return False
    return False


def analyze(task_records, evidence_records):
    """Run all cross-file checks. Returns (ready, issues, summary)."""
    issues = []

    tasks_by_id = {}
    for index, raw in enumerate(task_records):
        task_id, valid = _check_task_record(index, raw, issues)
        if valid and task_id is not None and task_id not in tasks_by_id:
            tasks_by_id[task_id] = raw
            # NOTE: if task_id repeats, the first occurrence wins and later
            # duplicates are silently ignored for cross-file checks. There
            # is no dedicated issue code for a duplicated task_id -- see
            # README "Limitations".

    for task_id, task in tasks_by_id.items():
        status = task.get("status")
        if isinstance(status, str) and status in UNSUBMITTABLE_STATUSES:
            issues.append(
                _issue(
                    UNSUBMITTABLE_STATUS,
                    task_id=task_id,
                    status=status,
                    message="task {} has unsubmittable status '{}'".format(task_id, status),
                )
            )

    evidence_valid_records = []
    submission_id_counts = {}
    for index, raw in enumerate(evidence_records):
        valid = _check_evidence_record(index, raw, issues)
        if valid:
            evidence_valid_records.append(raw)
            sid = raw.get("submission_id")
            submission_id_counts[sid] = submission_id_counts.get(sid, 0) + 1

    for sid, count in submission_id_counts.items():
        if count > 1:
            issues.append(
                _issue(
                    DUPLICATE_SUBMISSION_ID,
                    submission_id=sid,
                    count=count,
                    message="submission_id '{}' appears {} times in the evidence export".format(
                        sid, count
                    ),
                )
            )

    evidence_types_by_task = {}

    for raw in evidence_valid_records:
        task_id = raw.get("task_id")
        submission_id = raw.get("submission_id")
        evidence_type = raw.get("evidence_type")
        value = raw.get("value")

        if task_id not in tasks_by_id:
            issues.append(
                _issue(
                    ORPHAN_EVIDENCE,
                    task_id=task_id,
                    submission_id=submission_id,
                    evidence_type=evidence_type,
                    message="evidence {} references unknown task_id '{}'".format(
                        submission_id, task_id
                    ),
                )
            )
        else:
            task = tasks_by_id[task_id]
            required = task.get("required_evidence")
            if not isinstance(required, list):
                required = []
            if evidence_type not in required:
                issues.append(
                    _issue(
                        EVIDENCE_TYPE_MISMATCH,
                        task_id=task_id,
                        submission_id=submission_id,
                        evidence_type=evidence_type,
                        message=(
                            "evidence {} has evidence_type '{}' which is not in "
                            "required_evidence for task '{}'"
                        ).format(submission_id, evidence_type, task_id),
                    )
                )
            evidence_types_by_task.setdefault(task_id, set()).add(evidence_type)

        if _value_is_empty(value):
            issues.append(
                _issue(
                    EMPTY_EVIDENCE_VALUE,
                    task_id=task_id,
                    submission_id=submission_id,
                    message="evidence {} has an empty value".format(submission_id),
                )
            )

    for task_id, task in tasks_by_id.items():
        required = task.get("required_evidence")
        if not isinstance(required, list):
            continue
        have = evidence_types_by_task.get(task_id, set())
        # Deduplicate while preserving first-seen order: a task whose
        # required_evidence list accidentally repeats a type (e.g.
        # ["url", "url"]) must still only produce ONE missing-evidence
        # issue per type, not one per repetition.
        seen_required_types = set()
        deduped_required = []
        for etype in required:
            if etype not in seen_required_types:
                seen_required_types.add(etype)
                deduped_required.append(etype)
        for etype in deduped_required:
            if etype not in have:
                issues.append(
                    _issue(
                        TASK_MISSING_EVIDENCE,
                        task_id=task_id,
                        evidence_type=etype,
                        message=(
                            "task '{}' requires evidence_type '{}' but no matching "
                            "evidence was found"
                        ).format(task_id, etype),
                    )
                )

    issues.sort(key=_sort_key)

    issue_counts_by_code = {}
    for issue in issues:
        issue_counts_by_code[issue["code"]] = issue_counts_by_code.get(issue["code"], 0) + 1
    for code in ALL_CODES:
        issue_counts_by_code.setdefault(code, 0)

    summary = {
        "task_count": len(task_records),
        "evidence_count": len(evidence_records),
        "issue_count": len(issues),
        "issue_counts_by_code": issue_counts_by_code,
    }

    ready = len(issues) == 0
    return ready, issues, summary


def build_report(task_records, evidence_records):
    ready, issues, summary = analyze(task_records, evidence_records)
    return {
        "ready": ready,
        "summary": summary,
        "issues": issues,
    }


def to_canonical_json(obj):
    """Deterministic JSON: sorted keys, compact separators, ASCII-only
    escaping, single trailing newline. No runtime-dependent fields (no
    timestamps, hostnames, or absolute paths) are ever included."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Check a contributor's task export against its evidence export for "
            "submission readiness (task-ID linkage, evidence-type agreement, "
            "non-empty evidence values)."
        ),
    )
    parser.add_argument("tasks_file", help="path to the task export JSON file")
    parser.add_argument("evidence_file", help="path to the evidence export JSON file")
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="write the canonical JSON report to FILE instead of stdout",
    )
    return parser


def main(argv=None):
    parser = _build_arg_parser()

    # argparse calls sys.exit(2) itself on a usage error (missing/extra
    # positional args, unknown flags, etc.), which already matches our
    # exit-code-2 contract for "invalid input or usage error".
    args = parser.parse_args(argv)

    try:
        task_records = load_records(args.tasks_file)
        evidence_records = load_records(args.evidence_file)
    except PreflightInputError as exc:
        print("{}: error: {}".format(PROG, exc), file=sys.stderr)
        return 2

    report = build_report(task_records, evidence_records)
    output_text = to_canonical_json(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(output_text)
        except OSError as exc:
            print(
                "{}: error: could not write {}: {}".format(PROG, args.output, exc),
                file=sys.stderr,
            )
            return 2
    else:
        sys.stdout.write(output_text)

    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""scorecard.py -- stdlib-only per-contributor scorecard CLI for exported task histories.

Reads an array of task history records (JSON) -- each record a contributor,
a task_id, a chronological list of lifecycle events, and a list of evidence
items -- and reports, per contributor:

  * completion_rate            -- rewarded tasks / tasks that reached a
                                   terminal state (rewarded or refused).
                                   NOT over all tasks: an in-flight task is
                                   not a failure, so it is excluded from
                                   this denominator entirely. See README.md
                                   "Metric denominators".
  * average_verification_rounds -- mean count of adjacent
                                   verification_requested -> submitted
                                   transitions, over tasks that reached a
                                   terminal state (same denominator as
                                   completion_rate).
  * refusal_rate                -- refused tasks / terminal tasks (same
                                   denominator as completion_rate).
  * evidence_type_mix           -- counts and shares per evidence_type,
                                   denominator = that contributor's total
                                   (well-formed) evidence items. This is a
                                   deliberately DIFFERENT denominator from
                                   the three rate metrics above -- see
                                   README.md.

Every numeric rate is reported as an object carrying the rounded decimal
string value ALONGSIDE its raw integer numerator and denominator, so any
reader can recompute it independently -- never a bare, unauditable number.

THIS TOOL DOES NOT RANK, GRADE, OR SCORE CONTRIBUTORS. There is no
percentile, no letter grade, no single composite "score" anywhere in this
file or its output, and scorecards are sorted only by contributor id (never
by any metric -- sorting by a metric value IS a form of ranking). Every
report carries a machine-readable "disclaimer" field stating plainly that
these numbers are descriptive context about throughput and review friction,
not a measure of quality, difficulty, or effort, and must not be used to
penalize a contributor. See README.md "The ethical requirement" for the
full reasoning and README.md "Known limitations" for what these numbers
genuinely cannot tell you.

Data-quality finding codes
---------------------------
  * MALFORMED_RECORD    -- a record, event, or evidence item fails the
                            structural shape contract (see README.md
                            "Input shape").
  * INVALID_TIMESTAMP   -- an event's "at" value is present as a string but
                            fails to parse as a UTC ISO-8601 timestamp.
  * UNKNOWN_STATE        -- an event's "state" is a valid non-empty string
                            but is not one of the seven known lifecycle
                            states.
  * EMPTY_HISTORY        -- a task record's "events" array is present, is a
                            JSON array, and has zero elements.
  * MISSING_CONTRIBUTOR  -- a record's "contributor" field is absent, not a
                            string, or blank -- the record cannot be
                            attributed to anyone and is excluded from every
                            scorecard.
  * INSUFFICIENT_DATA    -- informational only. A contributor's total task
                            count is below --min-tasks: their rate metrics
                            are reported as null rather than a misleadingly
                            precise number computed from very little data.
                            This code alone does NOT set exit code 1 -- see
                            README.md "Does INSUFFICIENT_DATA set exit 1?".

Output is emitted as canonical JSON:

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

plus a single trailing newline.

Reproducibility contract
-------------------------
The wall clock is never consulted anywhere in the report path. The UTC
reference moment used for every computation is supplied exclusively via the
required --now command-line argument and threaded explicitly through every
function that needs it, as an ordinary parameter. This module makes no
wall-clock lookup of any kind (see README.md / captured_output.txt for a
grep proof over this file's source).

This convention -- and the parse_utc_timestamp / iso_z helpers below --
were matched deliberately from the sibling tools staleness-monitor
(staleness.py) and loop-health (loop_health.py), which established the
"injected --now, never read the wall clock" pattern for this family of
tools. See README.md, "What we matched from the sibling tools".

Exit codes
----------
  0  -- input parsed successfully and either no findings were produced, or
        the only findings produced are informational (INSUFFICIENT_DATA).
  1  -- input parsed successfully and at least one non-informational
        finding was produced (MALFORMED_RECORD, INVALID_TIMESTAMP,
        UNKNOWN_STATE, EMPTY_HISTORY, and/or MISSING_CONTRIBUTOR).
  2  -- invalid input or usage error (missing/unparseable --now, unreadable
        or malformed input file, input JSON whose root is not a list,
        negative --min-tasks, etc). Note: a malformed *record* inside an
        otherwise-valid array is NOT a usage error -- it is reported as a
        MALFORMED_RECORD finding (exit 1), exactly as the sibling tools do.
"""

import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from datetime import datetime, timedelta, timezone

__all__ = [
    "parse_utc_timestamp",
    "iso_z",
    "process_record",
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

TERMINAL_STATES = ("rewarded", "refused")

CODE_MALFORMED_RECORD = "MALFORMED_RECORD"
CODE_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
CODE_UNKNOWN_STATE = "UNKNOWN_STATE"
CODE_EMPTY_HISTORY = "EMPTY_HISTORY"
CODE_MISSING_CONTRIBUTOR = "MISSING_CONTRIBUTOR"
CODE_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_CODES = (
    CODE_MALFORMED_RECORD,
    CODE_INVALID_TIMESTAMP,
    CODE_UNKNOWN_STATE,
    CODE_EMPTY_HISTORY,
    CODE_MISSING_CONTRIBUTOR,
    CODE_INSUFFICIENT_DATA,
)

# Finding codes that are purely informational and therefore do NOT set exit
# code 1 by themselves. See README.md "Does INSUFFICIENT_DATA set exit 1?"
# for the reasoning: having few tasks is a fact about the sample, not a
# defect in the data.
INFORMATIONAL_CODES = frozenset({CODE_INSUFFICIENT_DATA})

DEFAULT_MIN_TASKS = 5

RATE_DECIMAL_PLACES = Decimal("0.000001")

DISCLAIMER = {
    "not_a_ranking": True,
    "not_a_basis_for_penalization": True,
    "text": (
        "These figures are descriptive context about throughput and review "
        "friction, computed under the fixed definitions in README.md. They "
        "are not rankings, percentiles, letter grades, or a composite "
        "score, and scorecards below are ordered only by contributor id -- "
        "never by any metric. They must not be used to penalize, discipline, "
        "or rank a contributor. They do not measure code quality, task "
        "difficulty, or effort: a contributor who takes on harder or more "
        "ambiguous tasks will tend to look worse on these numbers than one "
        "who takes on easier ones, even if their work is better."
    ),
}

# Sentinel distinguishing "key absent" from "key present with value None".
_MISSING = object()


class InputError(Exception):
    """Raised for invalid input / usage problems. Maps to exit code 2."""


# --------------------------------------------------------------------------
# Timestamp parsing (matched from staleness.py / loop_health.py; no
# wall-clock reads here)
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


# --------------------------------------------------------------------------
# Exact-ratio rate helper
# --------------------------------------------------------------------------


def make_rate(numerator, denominator, note=None):
    """Build a rate object carrying a rounded decimal-string value ALONGSIDE
    the raw integer numerator/denominator, so a reader can always recompute
    the exact ratio independently -- never a bare, unauditable number.

    ``value`` is None (with ``note`` explaining why) whenever the ratio
    cannot or should not be computed as a number: a zero denominator, or an
    explicit override note such as INSUFFICIENT_DATA supplied by the
    caller. Otherwise ``value`` is a Decimal computed via exact integer
    division (Decimal(numerator) / Decimal(denominator)) and quantized to
    six fractional digits with banker's rounding (ROUND_HALF_EVEN), then
    rendered as a JSON string (never a JSON float) to keep the exact digits
    stable across platforms.
    """
    result = {"numerator": numerator, "denominator": denominator, "value": None, "note": None}
    if note is not None:
        result["note"] = note
        return result
    if denominator == 0:
        result["note"] = "UNDEFINED_ZERO_DENOMINATOR"
        return result
    exact = Decimal(numerator) / Decimal(denominator)
    result["value"] = str(exact.quantize(RATE_DECIMAL_PLACES, rounding=ROUND_HALF_EVEN))
    return result


# --------------------------------------------------------------------------
# Finding construction
# --------------------------------------------------------------------------


def _finding(contributor, task_id, code, message, extra=None):
    f = {"contributor": contributor, "task_id": task_id, "code": code, "message": message}
    if extra:
        f.update(extra)
    return f


def _index_ref(idx):
    return f"<index:{idx}>"


# --------------------------------------------------------------------------
# Per-record processing
# --------------------------------------------------------------------------


class _TaskResult:
    """Everything computed for one structurally-usable, attributed task."""

    __slots__ = ("contributor", "task_id", "terminal_state", "verification_rounds", "evidence_types")

    def __init__(self, contributor, task_id, terminal_state, verification_rounds, evidence_types):
        self.contributor = contributor
        self.task_id = task_id
        self.terminal_state = terminal_state  # "rewarded" / "refused" / None
        self.verification_rounds = verification_rounds
        self.evidence_types = evidence_types  # list of str, well-formed only


def process_record(idx, record, findings):
    """Process one top-level array element. Appends any findings produced
    to ``findings`` (a list, mutated in place) and returns a _TaskResult if
    the record was structurally usable and attributable to a contributor,
    or None if it was not (in which case it contributes to no scorecard).
    """
    if not isinstance(record, dict):
        findings.append(
            _finding(
                None,
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} is not a JSON object",
                extra={"record_index": idx},
            )
        )
        return None

    task_id = record.get("task_id", _MISSING)
    if task_id is _MISSING or not isinstance(task_id, str) or task_id == "":
        findings.append(
            _finding(
                None,
                _index_ref(idx),
                CODE_MALFORMED_RECORD,
                f"record at index {idx} has an invalid task_id (must be a non-empty "
                f"JSON string): {task_id!r}",
                extra={"record_index": idx},
            )
        )
        return None

    contributor = record.get("contributor", _MISSING)
    if (
        contributor is _MISSING
        or not isinstance(contributor, str)
        or contributor.strip() == ""
    ):
        # NOTE: contributor must NOT be interpolated via bare repr() here.
        # When the key is absent, contributor IS the _MISSING sentinel
        # object, and object.__repr__ embeds that object's memory address
        # (e.g. "<object object at 0x7f...>") -- a value that changes
        # between process runs even for byte-identical input. That would
        # silently break the byte-identical-output reproducibility
        # contract. Render a fixed, deterministic placeholder instead.
        contributor_repr = "<absent>" if contributor is _MISSING else repr(contributor)
        findings.append(
            _finding(
                None,
                task_id,
                CODE_MISSING_CONTRIBUTOR,
                f"task {task_id!r} has a missing or blank contributor "
                f"(must be a non-empty, non-whitespace JSON string): {contributor_repr}",
            )
        )
        return None

    if "events" not in record:
        findings.append(
            _finding(
                contributor,
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} is missing required key: events",
            )
        )
        return None

    events = record["events"]
    if not isinstance(events, list):
        findings.append(
            _finding(
                contributor,
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} 'events' must be a JSON array",
            )
        )
        return None

    evidence = record.get("evidence", [])
    if not isinstance(evidence, list):
        findings.append(
            _finding(
                contributor,
                task_id,
                CODE_MALFORMED_RECORD,
                f"task {task_id!r} 'evidence' must be a JSON array when present",
            )
        )
        return None

    if len(events) == 0:
        findings.append(
            _finding(contributor, task_id, CODE_EMPTY_HISTORY, f"task {task_id!r} has zero events")
        )
        # Still a real, attributed task -- just never terminal, zero
        # verification rounds. Evidence is still processed below.

    timed_events = []  # list of (dt, original_index, state)

    for j, ev in enumerate(events):
        if not isinstance(ev, dict):
            findings.append(
                _finding(
                    contributor,
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
                    contributor,
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
                    contributor,
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
                    contributor,
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
                        contributor,
                        task_id,
                        CODE_INVALID_TIMESTAMP,
                        f"task {task_id!r} event at index {j}: {exc}",
                        extra={"event_index": j, "at_raw": at_raw},
                    )
                )

        refusal_reason = ev.get("refusal_reason", _MISSING)
        if (
            refusal_reason is not _MISSING
            and refusal_reason is not None
            and not isinstance(refusal_reason, str)
        ):
            findings.append(
                _finding(
                    contributor,
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} event at index {j} has a non-string refusal_reason",
                    extra={"event_index": j},
                )
            )

        if dt is not None:
            timed_events.append((dt, j, state))

    # Deterministic chronological order: (timestamp, original position).
    # Matches the sibling tools' documented tiebreak convention.
    timed_events.sort(key=lambda t: (t[0], t[1]))

    rounds = 0
    for a, b in zip(timed_events, timed_events[1:]):
        if a[2] == "verification_requested" and b[2] == "submitted":
            rounds += 1

    terminal_state = None
    if timed_events:
        latest_state = timed_events[-1][2]
        if latest_state in TERMINAL_STATES:
            terminal_state = latest_state

    evidence_types = []
    for k, item in enumerate(evidence):
        if not isinstance(item, dict):
            findings.append(
                _finding(
                    contributor,
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} evidence item at index {k} is not a JSON object",
                    extra={"evidence_index": k},
                )
            )
            continue
        etype = item.get("evidence_type", _MISSING)
        if etype is _MISSING or not isinstance(etype, str):
            findings.append(
                _finding(
                    contributor,
                    task_id,
                    CODE_MALFORMED_RECORD,
                    f"task {task_id!r} evidence item at index {k} has a missing or "
                    f"non-string evidence_type",
                    extra={"evidence_index": k},
                )
            )
            continue
        evidence_types.append(etype)

    return _TaskResult(contributor, task_id, terminal_state, rounds, evidence_types)


# --------------------------------------------------------------------------
# Per-contributor aggregation
# --------------------------------------------------------------------------


def _build_scorecard(contributor, tasks, min_tasks, findings):
    """Build one contributor's scorecard dict from their _TaskResult list.
    Appends an INSUFFICIENT_DATA finding to ``findings`` (mutated in place)
    if this contributor's total task count is below ``min_tasks``.
    """
    total_tasks = len(tasks)
    terminal_tasks = [t for t in tasks if t.terminal_state is not None]
    terminal_count = len(terminal_tasks)
    rewarded_count = sum(1 for t in terminal_tasks if t.terminal_state == "rewarded")
    refused_count = sum(1 for t in terminal_tasks if t.terminal_state == "refused")
    rounds_sum = sum(t.verification_rounds for t in terminal_tasks)

    evidence_counts = {}
    evidence_total = 0
    for t in tasks:
        for etype in t.evidence_types:
            evidence_counts[etype] = evidence_counts.get(etype, 0) + 1
            evidence_total += 1

    insufficient = total_tasks < min_tasks
    note = CODE_INSUFFICIENT_DATA if insufficient else None

    if insufficient:
        findings.append(
            {
                "contributor": contributor,
                "task_id": None,
                "code": CODE_INSUFFICIENT_DATA,
                "message": (
                    f"contributor {contributor!r} has {total_tasks} task(s), below "
                    f"--min-tasks={min_tasks}; rate metrics reported as null "
                    f"(informational only, does not affect exit code)"
                ),
                "total_tasks": total_tasks,
                "min_tasks": min_tasks,
            }
        )

    completion_rate = make_rate(rewarded_count, terminal_count, note=note)
    refusal_rate = make_rate(refused_count, terminal_count, note=note)
    average_verification_rounds = make_rate(rounds_sum, terminal_count, note=note)

    # Evidence-type mix is gated on ITS OWN denominator (total evidence
    # items for this contributor) only -- never on --min-tasks, which is a
    # task-count threshold on a different axis. A contributor with few
    # tasks but plenty of evidence still gets an auditable mix. See
    # README.md "Metric denominators" / "Does --min-tasks gate evidence_type_mix?".
    by_type = []
    for etype in sorted(evidence_counts):
        count = evidence_counts[etype]
        by_type.append(
            {
                "evidence_type": etype,
                "count": count,
                "share": make_rate(count, evidence_total),
            }
        )

    return {
        "contributor": contributor,
        "total_tasks": total_tasks,
        "terminal_tasks": terminal_count,
        "rewarded_tasks": rewarded_count,
        "refused_tasks": refused_count,
        "min_tasks_met": not insufficient,
        "completion_rate": completion_rate,
        "average_verification_rounds": average_verification_rounds,
        "refusal_rate": refusal_rate,
        "evidence_type_mix": {
            "total_evidence_items": evidence_total,
            "by_type": by_type,
        },
    }


# --------------------------------------------------------------------------
# Whole-input report assembly
# --------------------------------------------------------------------------


def build_report(data, now, min_tasks):
    """Build the full report dict for ``data`` at reference time ``now``.

    Returns (report_dict, exit_code_relevant_finding_count). Raises
    InputError only if ``data`` itself is not a JSON array -- that is the
    sole *usage*-level shape requirement.
    """
    if not isinstance(data, list):
        raise InputError("input JSON must be an array of task history records")

    all_findings = []
    by_contributor = {}

    for idx, record in enumerate(data):
        result = process_record(idx, record, all_findings)
        if result is not None:
            by_contributor.setdefault(result.contributor, []).append(result)

    scorecards = []
    for contributor in sorted(by_contributor):
        scorecards.append(_build_scorecard(contributor, by_contributor[contributor], min_tasks, all_findings))

    # Final deterministic sort of findings: (contributor-or-"", task_id-or-"",
    # code, event_index-or-(-1), full canonical dump as a last-resort
    # tiebreak). Note: this ordering is for reproducibility only -- it is
    # NOT a ranking of contributors (nothing about finding order reflects
    # standing or quality).
    all_findings.sort(
        key=lambda f: (
            f.get("contributor") or "",
            f.get("task_id") or "",
            f["code"],
            f.get("event_index", f.get("evidence_index", -1)),
            json.dumps(f, sort_keys=True, ensure_ascii=True),
        )
    )

    counts_by_code = {code: 0 for code in ALL_CODES}
    for f in all_findings:
        counts_by_code[f["code"]] += 1

    non_informational = sum(c for code, c in counts_by_code.items() if code not in INFORMATIONAL_CODES)

    summary = {
        "total_records": len(data),
        "total_contributors": len(scorecards),
        "total_findings": len(all_findings),
        "counts_by_code": counts_by_code,
    }

    report = {
        "generated_at": iso_z(now),
        "options": {"min_tasks": min_tasks},
        "disclaimer": DISCLAIMER,
        "summary": summary,
        "scorecards": scorecards,
        "findings": all_findings,
    }
    return report, non_informational


def canonical_json(report):
    """Serialize ``report`` as canonical JSON (sorted keys, compact
    separators, ASCII-only) plus exactly one trailing newline."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="scorecard.py",
        description=(
            "Convert exported task histories into deterministic, descriptive "
            "per-contributor scorecards; emit canonical JSON. Not a ranking tool."
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
        "--min-tasks",
        type=int,
        default=DEFAULT_MIN_TASKS,
        help=(
            "A contributor with fewer than this many total tasks has their rate "
            "metrics reported as null with an INSUFFICIENT_DATA note "
            "(default: %(default)s)."
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
        print(f"scorecard.py: error: invalid --now value: {exc}", file=sys.stderr)
        return 2

    if args.min_tasks < 0:
        print("scorecard.py: error: --min-tasks must be >= 0", file=sys.stderr)
        return 2

    try:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"scorecard.py: error: input file not found: {args.input_file}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"scorecard.py: error: could not read input file: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"scorecard.py: error: input file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        report, non_informational_findings = build_report(data, now, args.min_tasks)
    except InputError as exc:
        print(f"scorecard.py: error: {exc}", file=sys.stderr)
        return 2

    out = canonical_json(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
        except OSError as exc:
            print(f"scorecard.py: error: could not write output file: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(out)

    return 1 if non_informational_findings > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

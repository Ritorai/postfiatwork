#!/usr/bin/env python3
"""
Task Lifecycle Event Linter.

Reads JSONL task events and checks each task's state history against the
lifecycle:

    proposed -> accepted -> submitted -> verification_requested -> rewarded|refused

Allowed transitions (explicit graph):
    proposed              -> accepted, refused
    accepted              -> submitted, refused
    submitted             -> verification_requested, rewarded, refused
    verification_requested-> submitted, rewarded, refused
    rewarded              -> (terminal)
    refused               -> (terminal)

Finding codes:
    MALFORMED_RECORD    line is not a JSON object / missing or bad field types
    UNKNOWN_STATE       state value is not in the lifecycle vocabulary
    MISSING_START       task history does not begin with 'proposed'
    DUPLICATE_STATE     the same state is recorded twice back-to-back (a state
                        may legitimately recur via verification_requested ->
                        submitted, so only immediate repeats are flagged)
    SKIPPED_STATE       transition is forward but not adjacent in the graph
    BACKWARD_TRANSITION transition moves to an earlier lifecycle stage
    POST_TERMINAL_EVENT an event occurs after 'rewarded' or 'refused'
    NON_MONOTONIC_TIME  occurred_at is not strictly increasing within a task

Exit codes: 0 clean | 1 findings present | 2 unreadable input.
"""
import argparse
import json
import sys

ORDER = {
    "proposed": 0,
    "accepted": 1,
    "submitted": 2,
    "verification_requested": 3,
    "rewarded": 4,
    "refused": 4,
}
TERMINAL = {"rewarded", "refused"}
ALLOWED = {
    "proposed": {"accepted", "refused"},
    "accepted": {"submitted", "refused"},
    "submitted": {"verification_requested", "rewarded", "refused"},
    "verification_requested": {"submitted", "rewarded", "refused"},
    "rewarded": set(),
    "refused": set(),
}
REQUIRED = ("task_id", "state", "occurred_at")

MALFORMED_RECORD = "MALFORMED_RECORD"
UNKNOWN_STATE = "UNKNOWN_STATE"
MISSING_START = "MISSING_START"
DUPLICATE_STATE = "DUPLICATE_STATE"
SKIPPED_STATE = "SKIPPED_STATE"
BACKWARD_TRANSITION = "BACKWARD_TRANSITION"
POST_TERMINAL_EVENT = "POST_TERMINAL_EVENT"
NON_MONOTONIC_TIME = "NON_MONOTONIC_TIME"


class InputError(Exception):
    pass


def parse_jsonl(text):
    """Return (events, malformed_findings). Never raises on bad lines."""
    events, findings = [], []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            findings.append({"line": lineno, "task_id": None, "code": MALFORMED_RECORD,
                             "detail": f"invalid JSON: {exc.msg}"})
            continue
        if not isinstance(obj, dict):
            findings.append({"line": lineno, "task_id": None, "code": MALFORMED_RECORD,
                             "detail": f"expected object, got {type(obj).__name__}"})
            continue
        missing = [f for f in REQUIRED if f not in obj]
        if missing:
            findings.append({"line": lineno, "task_id": obj.get("task_id"),
                             "code": MALFORMED_RECORD,
                             "detail": f"missing field(s): {','.join(missing)}"})
            continue
        if not all(isinstance(obj[f], str) and obj[f].strip() for f in REQUIRED):
            findings.append({"line": lineno, "task_id": obj.get("task_id"),
                             "code": MALFORMED_RECORD,
                             "detail": "task_id, state and occurred_at must be non-empty strings"})
            continue
        if obj["state"] not in ORDER:
            findings.append({"line": lineno, "task_id": obj["task_id"], "code": UNKNOWN_STATE,
                             "detail": f"unknown state {obj['state']!r}"})
            continue
        events.append({"line": lineno, "task_id": obj["task_id"],
                       "state": obj["state"], "occurred_at": obj["occurred_at"]})
    return events, findings


def lint(events):
    """Validate per-task histories. Returns a list of findings."""
    findings = []
    by_task = {}
    for e in events:
        by_task.setdefault(e["task_id"], []).append(e)

    for task_id in sorted(by_task):
        history = sorted(by_task[task_id], key=lambda e: (e["occurred_at"], e["line"]))
        raw_order = by_task[task_id]

        prev_ts = None
        for e in raw_order:
            if prev_ts is not None and e["occurred_at"] <= prev_ts:
                findings.append({"line": e["line"], "task_id": task_id,
                                 "code": NON_MONOTONIC_TIME,
                                 "detail": f"occurred_at {e['occurred_at']} not after {prev_ts}"})
            prev_ts = e["occurred_at"]

        if history[0]["state"] != "proposed":
            findings.append({"line": history[0]["line"], "task_id": task_id,
                             "code": MISSING_START,
                             "detail": f"history begins with {history[0]['state']!r}, expected 'proposed'"})

        seen = set()
        terminal_seen = None
        prev = None
        for e in history:
            state = e["state"]
            if terminal_seen is not None:
                findings.append({"line": e["line"], "task_id": task_id,
                                 "code": POST_TERMINAL_EVENT,
                                 "detail": f"{state!r} occurs after terminal {terminal_seen!r}"})
                continue
            # Only an IMMEDIATE repeat is a duplicate. A state may legitimately
            # recur via the verification loop (verification_requested -> submitted),
            # so a naive "seen" set would false-positive on every resubmission.
            if prev == state:
                findings.append({"line": e["line"], "task_id": task_id,
                                 "code": DUPLICATE_STATE,
                                 "detail": f"state {state!r} repeated back-to-back"})
            seen.add(state)

            if prev is not None and state not in ALLOWED[prev]:
                if ORDER[state] < ORDER[prev]:
                    code, detail = BACKWARD_TRANSITION, f"{prev!r} -> {state!r} moves backward"
                else:
                    code, detail = SKIPPED_STATE, f"{prev!r} -> {state!r} skips a required state"
                findings.append({"line": e["line"], "task_id": task_id,
                                 "code": code, "detail": detail})

            if state in TERMINAL:
                terminal_seen = state
            prev = state

    return findings


def build_report(findings, event_count, task_count):
    ordered = sorted(findings, key=lambda f: (f["task_id"] or "", f["code"], f["line"]))
    counts = {}
    for f in ordered:
        counts[f["code"]] = counts.get(f["code"], 0) + 1
    return {
        "report_version": "1.0",
        "totals": {
            "events": event_count,
            "tasks": task_count,
            "findings": len(ordered),
        },
        "finding_counts": dict(sorted(counts.items())),
        "findings": ordered,
        "status": "clean" if not ordered else "issues",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint task lifecycle event histories (JSONL).")
    ap.add_argument("events", help="Path to a JSONL file of task lifecycle events.")
    ap.add_argument("-o", "--out", help="Write the canonical report here instead of stdout.")
    args = ap.parse_args(argv)

    try:
        with open(args.events, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        sys.stderr.write(f"UNREADABLE_INPUT: file not found: {args.events}\n")
        return 2
    except UnicodeDecodeError as exc:
        sys.stderr.write(f"UNREADABLE_INPUT: not valid UTF-8: {exc}\n")
        return 2

    events, malformed = parse_jsonl(text)
    findings = malformed + lint(events)
    tasks = len({e["task_id"] for e in events})
    report = build_report(findings, len(events), tasks)

    text_out = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text_out)
        sys.stdout.write(f"status={report['status']} findings={report['totals']['findings']}\n")
    else:
        sys.stdout.write(text_out)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())

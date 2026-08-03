#!/usr/bin/env python3
"""
Task Lifecycle Event Linter (JSON array input, per-task grouped report).

Reads a JSON ARRAY of task events and validates each task's history against:

    proposed -> accepted -> submitted -> verification_requested -> rewarded|refused

Allowed transitions:
    proposed               -> accepted, refused
    accepted               -> submitted, refused
    submitted              -> verification_requested, rewarded, refused
    verification_requested -> submitted, rewarded, refused
    rewarded / refused     -> terminal

Violation classes:
    ILLEGAL_TRANSITION   transition not permitted by the graph
    POST_TERMINAL_EVENT  event recorded after a terminal state
    TIMESTAMP_DISORDER   occurred_at not strictly increasing in array order
    DUPLICATE_EVENT      identical (state, occurred_at) pair repeated
    MISSING_PROPOSED     task history has no 'proposed' event
    MALFORMED_EVENT      element is not an object / bad or missing fields
    UNKNOWN_STATE        state outside the lifecycle vocabulary

Exit codes: 0 clean | 1 violations | 2 unreadable input.
"""
import argparse
import json
import sys

ALLOWED = {
    "proposed": {"accepted", "refused"},
    "accepted": {"submitted", "refused"},
    "submitted": {"verification_requested", "rewarded", "refused"},
    "verification_requested": {"submitted", "rewarded", "refused"},
    "rewarded": set(),
    "refused": set(),
}
STATES = set(ALLOWED)
TERMINAL = {"rewarded", "refused"}
REQUIRED = ("task_id", "state", "occurred_at")

ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
POST_TERMINAL_EVENT = "POST_TERMINAL_EVENT"
TIMESTAMP_DISORDER = "TIMESTAMP_DISORDER"
DUPLICATE_EVENT = "DUPLICATE_EVENT"
MISSING_PROPOSED = "MISSING_PROPOSED"
MALFORMED_EVENT = "MALFORMED_EVENT"
UNKNOWN_STATE = "UNKNOWN_STATE"


class InputError(Exception):
    pass


def load_events(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"not valid UTF-8: {exc}")
    if not isinstance(data, list):
        raise InputError(f"expected a JSON array, got {type(data).__name__}")
    return data


def partition(events):
    """Split raw array into (clean events, malformed findings)."""
    clean, bad = [], []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            bad.append({"index": i, "task_id": None, "violation": MALFORMED_EVENT,
                        "detail": f"expected object, got {type(e).__name__}"})
            continue
        missing = [f for f in REQUIRED if f not in e]
        if missing:
            bad.append({"index": i, "task_id": e.get("task_id"), "violation": MALFORMED_EVENT,
                        "detail": f"missing field(s): {','.join(missing)}"})
            continue
        if not all(isinstance(e[f], str) and e[f].strip() for f in REQUIRED):
            bad.append({"index": i, "task_id": e.get("task_id"), "violation": MALFORMED_EVENT,
                        "detail": "task_id, state and occurred_at must be non-empty strings"})
            continue
        if e["state"] not in STATES:
            bad.append({"index": i, "task_id": e["task_id"], "violation": UNKNOWN_STATE,
                        "detail": f"unknown state {e['state']!r}"})
            continue
        clean.append({"index": i, "task_id": e["task_id"],
                      "state": e["state"], "occurred_at": e["occurred_at"]})
    return clean, bad


def lint_task(task_id, history):
    """History is in array order. Returns violation dicts for this task."""
    out = []
    if not any(e["state"] == "proposed" for e in history):
        out.append({"index": history[0]["index"], "task_id": task_id,
                    "violation": MISSING_PROPOSED,
                    "detail": "no 'proposed' event in this task's history"})

    seen_pairs = set()
    prev = None
    prev_ts = None
    terminal = None
    for e in history:
        state, ts, idx = e["state"], e["occurred_at"], e["index"]

        pair = (state, ts)
        if pair in seen_pairs:
            out.append({"index": idx, "task_id": task_id, "violation": DUPLICATE_EVENT,
                        "detail": f"({state}, {ts}) already recorded"})
        seen_pairs.add(pair)

        if prev_ts is not None and ts < prev_ts:
            out.append({"index": idx, "task_id": task_id, "violation": TIMESTAMP_DISORDER,
                        "detail": f"occurred_at {ts} precedes previous {prev_ts}"})
        prev_ts = ts

        if terminal is not None:
            out.append({"index": idx, "task_id": task_id, "violation": POST_TERMINAL_EVENT,
                        "detail": f"{state!r} recorded after terminal {terminal!r}"})
            continue

        if prev is not None and state not in ALLOWED[prev]:
            out.append({"index": idx, "task_id": task_id, "violation": ILLEGAL_TRANSITION,
                        "detail": f"{prev!r} -> {state!r} is not an allowed transition"})

        if state in TERMINAL:
            terminal = state
        prev = state
    return out


def build_report(events, malformed):
    by_task = {}
    for e in events:
        by_task.setdefault(e["task_id"], []).append(e)

    grouped = {}
    for f in malformed:
        grouped.setdefault(f["task_id"] or "", []).append(f)
    for task_id in by_task:
        grouped.setdefault(task_id, [])
        grouped[task_id].extend(lint_task(task_id, by_task[task_id]))

    tasks = []
    counts = {}
    total = 0
    for task_id in sorted(grouped):
        vio = sorted(grouped[task_id], key=lambda f: (f["violation"], f["index"]))
        for f in vio:
            counts[f["violation"]] = counts.get(f["violation"], 0) + 1
        total += len(vio)
        tasks.append({
            "task_id": task_id,
            "event_count": len(by_task.get(task_id, [])),
            "violation_count": len(vio),
            "violations": vio,
            "status": "clean" if not vio else "violations",
        })

    return {
        "report_version": "1.0",
        "totals": {"events": len(events), "tasks": len(tasks), "violations": total},
        "violation_counts": dict(sorted(counts.items())),
        "tasks": tasks,
        "status": "clean" if total == 0 else "violations",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint task lifecycle events (JSON array).")
    ap.add_argument("events")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    try:
        raw = load_events(args.events)
    except InputError as exc:
        sys.stderr.write(f"UNREADABLE_INPUT: {exc}\n")
        return 2
    clean, bad = partition(raw)
    report = build_report(clean, bad)
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} violations={report['totals']['violations']}\n")
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())

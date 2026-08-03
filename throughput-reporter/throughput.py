#!/usr/bin/env python3
"""
Contributor Throughput and Reliability Reporter.

Reads task lifecycle records and produces a canonical per-contributor summary:
counts by outcome, refusal rate, median accept->submit and submit->terminal
durations, and a reliability grade.

Grades (applied in order, first match wins):
    INSUFFICIENT_DATA  fewer than --min-tasks terminal outcomes
    A                  refusal_rate <= 0.10 and median_submit_hours <= 24
    B                  refusal_rate <= 0.25
    C                  refusal_rate <= --refusal-ceiling
    D                  refusal_rate above the ceiling

Exit codes: 0 all contributors at/below ceiling | 1 at least one above | 2 bad input.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone

REQUIRED = ("task_id", "contributor", "state", "occurred_at")
TERMINAL = {"rewarded", "refused"}
KNOWN_STATES = {"proposed", "accepted", "submitted", "verification_requested",
                "rewarded", "refused", "cancelled"}


class InputError(Exception):
    pass


def _ts(value, where):
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
    except (ValueError, AttributeError):
        raise InputError(f"{where}: occurred_at is not ISO-8601: {value!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def load_events(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError(f"expected a JSON array, got {type(data).__name__}")
    out = []
    for i, r in enumerate(data):
        where = f"events[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"{where}: record must be an object")
        for f in REQUIRED:
            if f not in r:
                raise InputError(f"{where}: missing required field '{f}'")
            if not isinstance(r[f], str) or not r[f].strip():
                raise InputError(f"{where}: '{f}' must be a non-empty string")
        if r["state"] not in KNOWN_STATES:
            raise InputError(f"{where}: unknown state {r['state']!r}")
        out.append({"task_id": r["task_id"], "contributor": r["contributor"],
                    "state": r["state"], "occurred_at": r["occurred_at"],
                    "_ts": _ts(r["occurred_at"], where)})
    return out


def _median_hours(deltas):
    """Median of a list of timedeltas in hours, rounded to 4dp. None if empty."""
    if not deltas:
        return None
    hours = sorted(d.total_seconds() / 3600.0 for d in deltas)
    return round(statistics.median(hours), 4)


def grade(refusal_rate, median_submit_hours, terminal_count, cfg):
    if terminal_count < cfg["min_tasks"]:
        return "INSUFFICIENT_DATA"
    if refusal_rate <= 0.10 and median_submit_hours is not None and median_submit_hours <= 24:
        return "A"
    if refusal_rate <= 0.25:
        return "B"
    if refusal_rate <= cfg["refusal_ceiling"]:
        return "C"
    return "D"


def analyze(events, cfg):
    by_contrib = {}
    for e in events:
        by_contrib.setdefault(e["contributor"], []).append(e)

    contributors = []
    for name in sorted(by_contrib):
        evs = by_contrib[name]
        tasks = {}
        for e in evs:
            tasks.setdefault(e["task_id"], []).append(e)

        accepted = submitted = rewarded = refused = 0
        accept_to_submit = []
        submit_to_terminal = []

        for tid in sorted(tasks):
            hist = sorted(tasks[tid], key=lambda x: (x["_ts"], x["state"]))
            first = {}
            for e in hist:
                first.setdefault(e["state"], e["_ts"])
            if "accepted" in first:
                accepted += 1
            if "submitted" in first:
                submitted += 1
            if "rewarded" in first:
                rewarded += 1
            elif "refused" in first:
                refused += 1

            if "accepted" in first and "submitted" in first:
                d = first["submitted"] - first["accepted"]
                if d.total_seconds() >= 0:
                    accept_to_submit.append(d)
            term = first.get("rewarded") or first.get("refused")
            if "submitted" in first and term is not None:
                d = term - first["submitted"]
                if d.total_seconds() >= 0:
                    submit_to_terminal.append(d)

        terminal_count = rewarded + refused
        refusal_rate = round(refused / terminal_count, 6) if terminal_count else 0.0
        med_submit = _median_hours(accept_to_submit)
        med_term = _median_hours(submit_to_terminal)

        contributors.append({
            "contributor": name,
            "counts": {"tasks_seen": len(tasks), "accepted": accepted,
                       "submitted": submitted, "rewarded": rewarded,
                       "refused": refused, "terminal": terminal_count},
            "refusal_rate": refusal_rate,
            "median_accept_to_submit_hours": med_submit,
            "median_submit_to_terminal_hours": med_term,
            "grade": grade(refusal_rate, med_submit, terminal_count, cfg),
            "over_ceiling": terminal_count >= cfg["min_tasks"] and refusal_rate > cfg["refusal_ceiling"],
        })

    contributors.sort(key=lambda c: (-c["refusal_rate"], c["contributor"]))
    breaches = [c for c in contributors if c["over_ceiling"]]
    grades = {}
    for c in contributors:
        grades[c["grade"]] = grades.get(c["grade"], 0) + 1

    return {
        "report_version": "1.0",
        "config": {"refusal_ceiling": cfg["refusal_ceiling"], "min_tasks": cfg["min_tasks"]},
        "totals": {"events": len(events), "contributors": len(contributors),
                   "over_ceiling": len(breaches)},
        "grade_counts": dict(sorted(grades.items())),
        "contributors": contributors,
        "status": "ok" if not breaches else "ceiling_breach",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Contributor throughput and reliability report.")
    ap.add_argument("events")
    ap.add_argument("-o", "--out")
    ap.add_argument("--refusal-ceiling", type=float, default=0.5)
    ap.add_argument("--min-tasks", type=int, default=2)
    args = ap.parse_args(argv)
    cfg = {"refusal_ceiling": args.refusal_ceiling, "min_tasks": args.min_tasks}
    try:
        report = analyze(load_events(args.events), cfg)
    except InputError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} contributors={report['totals']['contributors']} "
                         f"over_ceiling={report['totals']['over_ceiling']}\n")
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
XRPL Payout Reference Auditor.

Checks payout records against a supplied task roster. Validates transaction-hash
structure, flags hash reuse within and across tasks, and identifies payouts
referencing unknown task IDs.

Issue codes:
    MALFORMED_TX_HASH   tx_hash is not exactly 64 uppercase hex characters
    REUSED_ACROSS_TASKS same tx_hash appears under two or more distinct task_ids
    REUSED_WITHIN_TASK  same tx_hash appears more than once under one task_id
    UNKNOWN_TASK_ID     task_id is not present in the roster
    MALFORMED_RECORD    element is not an object / missing or bad field types

Exit codes: 0 clean | 1 issues found | 2 unreadable input.
"""
import argparse
import json
import re
import sys

# \A and \Z, not ^ and $. In Python `$` also matches immediately before a
# trailing newline, so `^[0-9A-F]{64}$` accepted a 65-character tx_hash whose
# 65th character was "\n". That value was reported well-formed, and because
# by_hash keys on the raw string it no longer collided with the same hash
# written without the newline, so the reuse checks missed it as well -- one
# appended byte turned a REUSED_ACROSS_TASKS finding into "status":"clean".
# \Z matches only at the true end of the string, so this regex now means what
# the docstring above and the README have always claimed: exactly 64.
TXHASH_RE = re.compile(r"\A[0-9A-F]{64}\Z")
REQUIRED = ("payout_id", "task_id", "wallet", "tx_hash")

MALFORMED_TX_HASH = "MALFORMED_TX_HASH"
REUSED_ACROSS_TASKS = "REUSED_ACROSS_TASKS"
REUSED_WITHIN_TASK = "REUSED_WITHIN_TASK"
UNKNOWN_TASK_ID = "UNKNOWN_TASK_ID"
MALFORMED_RECORD = "MALFORMED_RECORD"


class InputError(Exception):
    pass


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise InputError(f"{label}: file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: invalid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"{label}: not valid UTF-8: {exc}")


def load_roster(path):
    data = _load_json(path, "roster")
    if not isinstance(data, list):
        raise InputError(f"roster: expected a JSON array, got {type(data).__name__}")
    roster = set()
    for i, t in enumerate(data):
        if isinstance(t, str):
            tid = t
        elif isinstance(t, dict) and "task_id" in t:
            tid = t["task_id"]
        else:
            raise InputError(f"roster[{i}]: expected a string or an object with 'task_id'")
        if not isinstance(tid, str) or not tid.strip():
            raise InputError(f"roster[{i}]: task_id must be a non-empty string")
        roster.add(tid)
    return roster


def load_payouts(path):
    data = _load_json(path, "payouts")
    if not isinstance(data, list):
        raise InputError(f"payouts: expected a JSON array, got {type(data).__name__}")
    good, bad = [], []
    for i, r in enumerate(data):
        where = f"payouts[{i}]"
        if not isinstance(r, dict):
            bad.append({"index": i, "payout_id": None, "task_id": None,
                        "issue": MALFORMED_RECORD,
                        "detail": f"expected object, got {type(r).__name__}"})
            continue
        missing = [f for f in REQUIRED if f not in r]
        if missing:
            bad.append({"index": i, "payout_id": r.get("payout_id"), "task_id": r.get("task_id"),
                        "issue": MALFORMED_RECORD,
                        "detail": f"missing field(s): {','.join(missing)}"})
            continue
        if not all(isinstance(r[f], str) and r[f].strip() for f in REQUIRED):
            bad.append({"index": i, "payout_id": r.get("payout_id"), "task_id": r.get("task_id"),
                        "issue": MALFORMED_RECORD,
                        "detail": "payout_id, task_id, wallet and tx_hash must be non-empty strings"})
            continue
        good.append({"index": i, "payout_id": r["payout_id"], "task_id": r["task_id"],
                     "wallet": r["wallet"], "tx_hash": r["tx_hash"]})
    return good, bad


def audit(payouts, roster, malformed):
    issues = list(malformed)

    for p in payouts:
        if not TXHASH_RE.match(p["tx_hash"]):
            issues.append({"index": p["index"], "payout_id": p["payout_id"],
                           "task_id": p["task_id"], "issue": MALFORMED_TX_HASH,
                           "detail": "tx_hash must be exactly 64 uppercase hex characters"})
        if p["task_id"] not in roster:
            issues.append({"index": p["index"], "payout_id": p["payout_id"],
                           "task_id": p["task_id"], "issue": UNKNOWN_TASK_ID,
                           "detail": f"task_id {p['task_id']!r} is not in the roster"})

    by_hash = {}
    for p in payouts:
        by_hash.setdefault(p["tx_hash"], []).append(p)

    for tx in sorted(by_hash):
        group = by_hash[tx]
        tasks = sorted({p["task_id"] for p in group})
        if len(tasks) > 1:
            for p in sorted(group, key=lambda r: r["index"]):
                issues.append({"index": p["index"], "payout_id": p["payout_id"],
                               "task_id": p["task_id"], "issue": REUSED_ACROSS_TASKS,
                               "detail": f"tx_hash shared by task_ids: {','.join(tasks)}"})
        per_task = {}
        for p in group:
            per_task.setdefault(p["task_id"], []).append(p)
        for tid in sorted(per_task):
            dupes = per_task[tid]
            if len(dupes) > 1:
                for p in sorted(dupes, key=lambda r: r["index"]):
                    issues.append({"index": p["index"], "payout_id": p["payout_id"],
                                   "task_id": p["task_id"], "issue": REUSED_WITHIN_TASK,
                                   "detail": f"tx_hash appears {len(dupes)} times under this task"})

    issues.sort(key=lambda f: (f["issue"], f["index"], f["payout_id"] or ""))
    counts = {}
    for f in issues:
        counts[f["issue"]] = counts.get(f["issue"], 0) + 1

    return {
        "report_version": "1.0",
        "totals": {
            "payouts": len(payouts) + len(malformed),
            "well_formed_payouts": len(payouts),
            "roster_tasks": len(roster),
            "distinct_tx_hashes": len(by_hash),
            "issues": len(issues),
        },
        "issue_counts": dict(sorted(counts.items())),
        "issues": issues,
        "status": "clean" if not issues else "issues",
    }


def serialize(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit XRPL payout references against a task roster.")
    ap.add_argument("payouts")
    ap.add_argument("roster")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    try:
        roster = load_roster(args.roster)
        good, bad = load_payouts(args.payouts)
        report = audit(good, roster, bad)
    except InputError as exc:
        sys.stderr.write(f"UNREADABLE_INPUT: {exc}\n")
        return 2
    text = serialize(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write(f"status={report['status']} issues={report['totals']['issues']}\n")
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())

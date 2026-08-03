#!/usr/bin/env python3
"""
Deterministic Reward Reconciliation CLI.

Compares expected task rewards against recorded payouts and exposes settlement
errors before funds are finalized. Output is canonical: byte-identical across
repeated runs on the same inputs.

Issue codes:
  MISSING_PAYOUT     - an expected reward has no corresponding payout
  DUPLICATE_PAYOUT   - more than one payout recorded for the same task
  UNEXPECTED_PAYOUT  - a payout exists for a task with no expected reward
  AMOUNT_MISMATCH    - payout amount differs from the expected amount
  WALLET_MISMATCH    - payout went to a different wallet than expected

Exit codes:
  0 - balanced (no issues)
  1 - mismatched (one or more settlement issues found)
  2 - invalid input / processing error
"""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation

SCALE = Decimal("0.000001")  # 6 dp, PFT settlement precision

MISSING_PAYOUT = "MISSING_PAYOUT"
DUPLICATE_PAYOUT = "DUPLICATE_PAYOUT"
UNEXPECTED_PAYOUT = "UNEXPECTED_PAYOUT"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
WALLET_MISMATCH = "WALLET_MISMATCH"

EXPECTED_FIELDS = ("task_id", "wallet", "amount")
PAYOUT_FIELDS = ("task_id", "wallet", "amount")


class InputError(Exception):
    """Raised for malformed input that should produce exit code 2."""


def _quantize(raw, where):
    """Parse an amount into a fixed-scale Decimal. Rejects floats and junk."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise InputError(f"{where}: amount must be a string or integer, got {type(raw).__name__}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise InputError(f"{where}: amount is not a valid decimal: {raw!r}")
    if value != value:  # NaN guard
        raise InputError(f"{where}: amount is NaN")
    return value.quantize(SCALE)


def _load_records(path, fields, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"{label}: file not found: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: invalid JSON: {exc}")
    if not isinstance(data, list):
        raise InputError(f"{label}: expected a JSON array, got {type(data).__name__}")

    out = []
    for i, rec in enumerate(data):
        where = f"{label}[{i}]"
        if not isinstance(rec, dict):
            raise InputError(f"{where}: record must be an object")
        for f in fields:
            if f not in rec:
                raise InputError(f"{where}: missing required field '{f}'")
        for f in ("task_id", "wallet"):
            if not isinstance(rec[f], str) or not rec[f].strip():
                raise InputError(f"{where}: '{f}' must be a non-empty string")
        out.append({
            "task_id": rec["task_id"],
            "wallet": rec["wallet"],
            "amount": _quantize(rec["amount"], where),
        })
    return out


def reconcile(expected, payouts):
    """Compare expected rewards to payouts. Returns a canonical report dict."""
    exp_by_task = {}
    for e in expected:
        if e["task_id"] in exp_by_task:
            raise InputError(f"expected: duplicate task_id in expected set: {e['task_id']}")
        exp_by_task[e["task_id"]] = e

    pay_by_task = {}
    for p in payouts:
        pay_by_task.setdefault(p["task_id"], []).append(p)

    findings = []

    for task_id in sorted(exp_by_task):
        e = exp_by_task[task_id]
        group = pay_by_task.get(task_id, [])
        if not group:
            findings.append({
                "task_id": task_id, "wallet": e["wallet"], "issue": MISSING_PAYOUT,
                "expected_amount": str(e["amount"]), "payout_amount": None,
            })
            continue
        if len(group) > 1:
            total = sum((g["amount"] for g in group), Decimal("0")).quantize(SCALE)
            findings.append({
                "task_id": task_id, "wallet": e["wallet"], "issue": DUPLICATE_PAYOUT,
                "expected_amount": str(e["amount"]), "payout_amount": str(total),
                "payout_count": len(group),
            })
            continue
        p = group[0]
        if p["wallet"] != e["wallet"]:
            findings.append({
                "task_id": task_id, "wallet": e["wallet"], "issue": WALLET_MISMATCH,
                "expected_amount": str(e["amount"]), "payout_amount": str(p["amount"]),
                "payout_wallet": p["wallet"],
            })
        if p["amount"] != e["amount"]:
            findings.append({
                "task_id": task_id, "wallet": e["wallet"], "issue": AMOUNT_MISMATCH,
                "expected_amount": str(e["amount"]), "payout_amount": str(p["amount"]),
                "delta": str((p["amount"] - e["amount"]).quantize(SCALE)),
            })

    for task_id in sorted(pay_by_task):
        if task_id in exp_by_task:
            continue
        for p in sorted(pay_by_task[task_id], key=lambda r: (r["wallet"], r["amount"])):
            findings.append({
                "task_id": task_id, "wallet": p["wallet"], "issue": UNEXPECTED_PAYOUT,
                "expected_amount": None, "payout_amount": str(p["amount"]),
            })

    findings.sort(key=lambda f: (f["task_id"], f["issue"], f["wallet"]))

    exp_total = sum((e["amount"] for e in expected), Decimal("0")).quantize(SCALE)
    pay_total = sum((p["amount"] for p in payouts), Decimal("0")).quantize(SCALE)

    counts = {}
    for f in findings:
        counts[f["issue"]] = counts.get(f["issue"], 0) + 1

    return {
        "report_version": "1.0",
        "precision": str(SCALE),
        "totals": {
            "expected_records": len(expected),
            "payout_records": len(payouts),
            "expected_total": str(exp_total),
            "payout_total": str(pay_total),
            "net_delta": str((pay_total - exp_total).quantize(SCALE)),
            "findings": len(findings),
        },
        "issue_counts": dict(sorted(counts.items())),
        "findings": findings,
        "status": "balanced" if not findings else "mismatched",
    }


def canonical_json(report):
    """Canonical serialization: sorted keys, fixed separators, trailing newline."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconcile expected task rewards against recorded payouts.")
    ap.add_argument("expected", help="JSON array of expected rewards.")
    ap.add_argument("payouts", help="JSON array of recorded payouts.")
    ap.add_argument("-o", "--out", help="Write the canonical report to this path instead of stdout.")
    args = ap.parse_args(argv)

    try:
        exp = _load_records(args.expected, EXPECTED_FIELDS, "expected")
        pay = _load_records(args.payouts, PAYOUT_FIELDS, "payouts")
        report = reconcile(exp, pay)
    except InputError as exc:
        sys.stderr.write(canonical_json({"error": "INVALID_INPUT", "detail": str(exc)}))
        return 2

    text = canonical_json(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "balanced" else 1


if __name__ == "__main__":
    sys.exit(main())

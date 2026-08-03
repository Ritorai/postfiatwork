#!/usr/bin/env python3
"""
Reward Anomaly Detection CLI.

Extends reward-reconciler (see README.md, "What was reused / what is new")
from a single expected-vs-payout diff into anomaly detection over a *tasks*
export and a *payouts* export: payouts for refused tasks, duplicate
payouts, amounts outside the stated task price, and payouts with no
matching task.

Reuses reward-reconciler's record field name ("task_id"), its "amount"
naming for money fields, and its CLI/module shape: an InputError exception
for exit-2 cases, a pure reconcile() function with no I/O, a
canonical_json() serializer, a thin main()/argparse wrapper, and the
0 = clean / 1 = findings / 2 = invalid-input exit-code contract.

Anomaly codes:
  PAYOUT_FOR_REFUSED_TASK - a payout exists for a task whose status is "refused"
  DUPLICATE_PAYOUT        - two or more payouts reference the same task_id
                             (reports every payout_id involved)
  AMOUNT_ABOVE_PRICE      - payout amount exceeds the task's stated price
                             by more than --tolerance
  AMOUNT_BELOW_PRICE      - payout amount is under the task's stated price
                             by more than --tolerance
  PAYOUT_WITHOUT_TASK     - a payout's task_id does not appear in the tasks export
  DUPLICATE_PAYOUT_ID     - the same payout_id appears more than once in the
                             payouts export
  INVALID_AMOUNT          - a payout's amount is non-numeric, NaN/Infinity,
                             null, boolean, or negative
  INVALID_PRICE           - a task's price is non-numeric, NaN/Infinity,
                             null, boolean, or negative
  MALFORMED_RECORD        - a record is not a JSON object, is missing a
                             required field, has a non-string/empty id, an
                             unrecognized task status, an unparsable "at"
                             timestamp, or duplicates a task_id already seen

Exit codes:
  0 - clean (no anomalies)
  1 - anomalies found
  2 - invalid input / usage error
"""
import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, getcontext

# Generous working precision so summing/differencing many high-precision
# amounts never loses digits to context rounding. Decimal *construction*
# from a string/int (or from json.loads with parse_float=Decimal) is always
# exact regardless of this setting; only +/-/* are affected.
getcontext().prec = 60

PAYOUT_FOR_REFUSED_TASK = "PAYOUT_FOR_REFUSED_TASK"
DUPLICATE_PAYOUT = "DUPLICATE_PAYOUT"
AMOUNT_ABOVE_PRICE = "AMOUNT_ABOVE_PRICE"
AMOUNT_BELOW_PRICE = "AMOUNT_BELOW_PRICE"
PAYOUT_WITHOUT_TASK = "PAYOUT_WITHOUT_TASK"
DUPLICATE_PAYOUT_ID = "DUPLICATE_PAYOUT_ID"
INVALID_AMOUNT = "INVALID_AMOUNT"
INVALID_PRICE = "INVALID_PRICE"
MALFORMED_RECORD = "MALFORMED_RECORD"

VALID_STATUSES = ("proposed", "accepted", "submitted", "rewarded", "refused")
REQUIRED_TASK_FIELDS = ("task_id", "status", "price")
REQUIRED_PAYOUT_FIELDS = ("payout_id", "task_id", "amount", "at")


class InputError(Exception):
    """Raised for malformed input that should produce exit code 2."""


class _NonFinite:
    """Sentinel for a JSON NaN/Infinity/-Infinity literal.

    json.loads() accepts these tokens by default via parse_constant, handing
    back float('nan')/float('inf')/float('-inf'). We intercept that and wrap
    the raw token instead of ever building a Decimal('NaN'): Decimal('NaN')
    compares False to everything (including itself), which would silently
    defeat every '<'/'>'/'!=' comparison downstream and swallow real
    findings. Keeping it as an opaque, non-numeric sentinel forces every
    NaN/Infinity value through the normal INVALID_AMOUNT/INVALID_PRICE
    rejection path instead.
    """
    __slots__ = ("token",)

    def __init__(self, token):
        self.token = token

    def __repr__(self):
        return self.token

    def __eq__(self, other):
        return isinstance(other, _NonFinite) and self.token == other.token

    def __hash__(self):
        return hash(("_NonFinite", self.token))


def _safe_repr(value):
    """Return a value guaranteed to be directly JSON-serializable, used to
    echo arbitrary (possibly malformed) input fields back in findings."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _NonFinite):
        return value.token
    return repr(value)


def _amt_str(d):
    """Canonical string form of a Decimal amount: always plain fixed-point
    (never scientific notation), and -0 normalized to 0."""
    if d == 0:
        d = Decimal(0)
    return format(d, "f")


def _parse_finite_decimal(raw):
    """Parse raw into a finite Decimal (sign unrestricted).

    Accepts JSON strings and JSON numbers (both ints and float-shaped
    literals -- the latter via parse_float=Decimal at the json.loads call
    site, so the Decimal is built directly from the source text, never from
    an intermediate 64-bit float). Returns (Decimal, None) on success or
    (None, "reason") on failure.
    """
    if isinstance(raw, _NonFinite):
        return None, f"is {raw.token} (NaN/Infinity is not permitted)"
    if isinstance(raw, bool):
        return None, "must not be a boolean"
    if raw is None:
        return None, "must not be null"
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, "must not be an empty string"
        try:
            value = Decimal(s)
        except InvalidOperation:
            return None, f"is not a valid decimal number: {raw!r}"
    else:
        return None, f"has unsupported JSON type: {type(raw).__name__}"

    if value.is_nan() or value.is_infinite():
        return None, "must be finite (NaN/Infinity is not permitted)"
    return value, None


def _parse_money(raw, allow_negative=False):
    """Parse a monetary field: a finite Decimal, non-negative unless
    allow_negative. Returns (Decimal, None) or (None, reason)."""
    value, err = _parse_finite_decimal(raw)
    if err is not None:
        return None, err
    if not allow_negative and value < 0:
        return None, f"must not be negative, got {value}"
    return value, None


def _looks_like_iso8601_utc(s):
    """Best-effort ISO-8601 timestamp check: accepts a 'Z' suffix or an
    explicit UTC offset. Does not require the offset to literally be
    +00:00 -- only that the value is a parsable, timezone-aware timestamp."""
    body = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.fromisoformat(body)
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _load_json(path):
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
    except FileNotFoundError:
        raise InputError(f"file not found: {path}")
    except IsADirectoryError:
        raise InputError(f"expected a file, found a directory: {path}")
    except OSError as exc:
        raise InputError(f"could not read file: {path}: {exc}")
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_NonFinite)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON: {exc}")


def _load_array(path, label):
    data = _load_json(path)
    if not isinstance(data, list):
        raise InputError(f"{label}: expected a JSON array, got {type(data).__name__}")
    return data


def _malformed(source, index, task_id=None, payout_id=None, detail=""):
    return {
        "code": MALFORMED_RECORD,
        "task_id": task_id,
        "payout_id": payout_id,
        "index": index,
        "source": source,
        "detail": detail,
    }


def _parse_tasks(raw_list):
    """Structurally + semantically validate the tasks export.

    Returns (tasks: dict[task_id -> {"status", "price", "index"}], findings).
    A task_id seen more than once keeps the FIRST occurrence for all
    downstream lookups (deterministic, order-dependent); every later
    occurrence is reported as MALFORMED_RECORD and otherwise ignored.
    """
    findings = []
    tasks = {}
    for i, rec in enumerate(raw_list):
        if not isinstance(rec, dict):
            findings.append(_malformed("tasks", i, detail="record must be a JSON object"))
            continue
        missing = [f for f in REQUIRED_TASK_FIELDS if f not in rec]
        if missing:
            tid = rec.get("task_id") if isinstance(rec.get("task_id"), str) else None
            findings.append(_malformed("tasks", i, task_id=tid,
                             detail=f"missing required field(s): {', '.join(missing)}"))
            continue
        task_id = rec["task_id"]
        if not isinstance(task_id, str) or not task_id.strip():
            findings.append(_malformed("tasks", i, detail="'task_id' must be a non-empty string"))
            continue
        status = rec["status"]
        if not isinstance(status, str) or status not in VALID_STATUSES:
            findings.append(_malformed("tasks", i, task_id=task_id,
                             detail=f"'status' must be one of {list(VALID_STATUSES)}, got {_safe_repr(status)!r}"))
            continue
        if task_id in tasks:
            findings.append(_malformed("tasks", i, task_id=task_id,
                             detail=f"duplicate task_id, first seen at index {tasks[task_id]['index']}"))
            continue
        price_val, price_err = _parse_money(rec["price"], allow_negative=False)
        if price_err is not None:
            findings.append({
                "code": INVALID_PRICE, "task_id": task_id, "payout_id": None,
                "index": i, "source": "tasks",
                "price": _safe_repr(rec["price"]), "detail": price_err,
            })
            tasks[task_id] = {"status": status, "price": None, "index": i}
            continue
        tasks[task_id] = {"status": status, "price": price_val, "index": i}
    return tasks, findings


def _parse_payouts(raw_list):
    """Structurally + semantically validate the payouts export.

    Returns (payouts: list of structurally-valid parsed dicts, findings).
    A payout with a bad amount is still included in the returned list (with
    amount_valid=False) so it still participates in DUPLICATE_PAYOUT_ID /
    DUPLICATE_PAYOUT / PAYOUT_WITHOUT_TASK / PAYOUT_FOR_REFUSED_TASK checks,
    which don't depend on the amount being valid -- only price-comparison
    checks require amount_valid.
    """
    findings = []
    parsed = []
    for i, rec in enumerate(raw_list):
        if not isinstance(rec, dict):
            findings.append(_malformed("payouts", i, detail="record must be a JSON object"))
            continue
        missing = [f for f in REQUIRED_PAYOUT_FIELDS if f not in rec]
        if missing:
            pid = rec.get("payout_id") if isinstance(rec.get("payout_id"), str) else None
            tid = rec.get("task_id") if isinstance(rec.get("task_id"), str) else None
            findings.append(_malformed("payouts", i, task_id=tid, payout_id=pid,
                             detail=f"missing required field(s): {', '.join(missing)}"))
            continue
        payout_id = rec["payout_id"]
        task_id = rec["task_id"]
        at = rec["at"]
        if not isinstance(payout_id, str) or not payout_id.strip():
            findings.append(_malformed("payouts", i,
                             task_id=task_id if isinstance(task_id, str) else None,
                             detail="'payout_id' must be a non-empty string"))
            continue
        if not isinstance(task_id, str) or not task_id.strip():
            findings.append(_malformed("payouts", i, payout_id=payout_id,
                             detail="'task_id' must be a non-empty string"))
            continue
        if not isinstance(at, str) or not at.strip():
            findings.append(_malformed("payouts", i, task_id=task_id, payout_id=payout_id,
                             detail="'at' must be a non-empty string"))
            continue
        if not _looks_like_iso8601_utc(at):
            findings.append(_malformed("payouts", i, task_id=task_id, payout_id=payout_id,
                             detail=f"'at' is not a valid ISO-8601 UTC timestamp: {at!r}"))
            continue
        amount_val, amount_err = _parse_money(rec["amount"], allow_negative=False)
        amount_valid = amount_err is None
        if not amount_valid:
            findings.append({
                "code": INVALID_AMOUNT, "task_id": task_id, "payout_id": payout_id,
                "index": i, "source": "payouts",
                "amount": _safe_repr(rec["amount"]), "detail": amount_err,
            })
        parsed.append({
            "payout_id": payout_id, "task_id": task_id, "at": at,
            "amount": amount_val, "amount_valid": amount_valid, "index": i,
        })
    return parsed, findings


def _finding_sort_key(f):
    idx = f.get("index")
    if idx is None:
        idx = -1
    return (f["code"], f.get("task_id") or "", f.get("payout_id") or "", f.get("source") or "", idx)


def reconcile(raw_tasks, raw_payouts, tolerance=Decimal("0")):
    """Detect payout anomalies against a tasks export. Pure function, no I/O.

    raw_tasks / raw_payouts are the raw (already JSON-decoded, but otherwise
    unvalidated) lists from the two export files. tolerance is a
    non-negative Decimal: a payout is only flagged AMOUNT_ABOVE_PRICE /
    AMOUNT_BELOW_PRICE if |amount - price| is STRICTLY greater than
    tolerance (a delta exactly equal to tolerance is NOT flagged).
    """
    tasks, findings = _parse_tasks(raw_tasks)
    payouts, payout_findings = _parse_payouts(raw_payouts)
    findings.extend(payout_findings)

    # DUPLICATE_PAYOUT_ID: the same payout_id string used more than once,
    # regardless of which task_id(s) it points at.
    by_payout_id = {}
    for p in payouts:
        by_payout_id.setdefault(p["payout_id"], []).append(p)
    for payout_id in sorted(by_payout_id):
        group = by_payout_id[payout_id]
        if len(group) > 1:
            findings.append({
                "code": DUPLICATE_PAYOUT_ID, "task_id": None, "payout_id": payout_id,
                "index": None, "source": "payouts",
                "indices": sorted(g["index"] for g in group),
                "task_ids": sorted({g["task_id"] for g in group}),
                "count": len(group),
            })

    # DUPLICATE_PAYOUT: more than one (structurally valid) payout referencing
    # the same task_id. Reported once per task_id with every payout_id
    # involved; amount/refused checks below are skipped for these task_ids
    # since it is ambiguous which payout is "the" payout for that task.
    by_task = {}
    for p in payouts:
        by_task.setdefault(p["task_id"], []).append(p)

    dup_task_ids = set()
    for task_id in sorted(by_task):
        group = by_task[task_id]
        if len(group) > 1:
            dup_task_ids.add(task_id)
            findings.append({
                "code": DUPLICATE_PAYOUT, "task_id": task_id, "payout_id": None,
                "index": None, "source": "payouts",
                "payout_ids": sorted(g["payout_id"] for g in group),
                "count": len(group),
            })

    for task_id in sorted(by_task):
        group = by_task[task_id]
        task = tasks.get(task_id)

        if task is None:
            for p in sorted(group, key=lambda g: g["payout_id"]):
                findings.append({
                    "code": PAYOUT_WITHOUT_TASK, "task_id": task_id, "payout_id": p["payout_id"],
                    "index": p["index"], "source": "payouts",
                })
            continue

        if task_id in dup_task_ids:
            continue

        p = group[0]
        if task["status"] == "refused":
            findings.append({
                "code": PAYOUT_FOR_REFUSED_TASK, "task_id": task_id, "payout_id": p["payout_id"],
                "index": p["index"], "source": "payouts",
                "price": _amt_str(task["price"]) if task["price"] is not None else None,
                "amount": _amt_str(p["amount"]) if p["amount_valid"] else None,
            })

        if p["amount_valid"] and task["price"] is not None:
            delta = p["amount"] - task["price"]
            if delta > tolerance:
                findings.append({
                    "code": AMOUNT_ABOVE_PRICE, "task_id": task_id, "payout_id": p["payout_id"],
                    "index": p["index"], "source": "payouts",
                    "price": _amt_str(task["price"]), "amount": _amt_str(p["amount"]),
                    "delta": _amt_str(delta),
                })
            elif delta < -tolerance:
                findings.append({
                    "code": AMOUNT_BELOW_PRICE, "task_id": task_id, "payout_id": p["payout_id"],
                    "index": p["index"], "source": "payouts",
                    "price": _amt_str(task["price"]), "amount": _amt_str(p["amount"]),
                    "delta": _amt_str(delta),
                })

    findings.sort(key=_finding_sort_key)

    counts = {}
    for f in findings:
        counts[f["code"]] = counts.get(f["code"], 0) + 1

    return {
        "report_version": "1.0",
        "tolerance": _amt_str(tolerance),
        "totals": {
            "task_records": len(raw_tasks),
            "payout_records": len(raw_payouts),
            "findings": len(findings),
        },
        "finding_counts": dict(sorted(counts.items())),
        "findings": findings,
        "status": "clean" if not findings else "anomalies",
    }


def canonical_json(report):
    """Canonical serialization: sorted keys, fixed separators, ASCII-only,
    single trailing newline. Byte-identical across repeated runs on the
    same input."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _parse_tolerance(raw):
    value, err = _parse_finite_decimal(raw)
    if err is not None:
        raise InputError(f"--tolerance: invalid value {raw!r} ({err})")
    if value < 0:
        raise InputError(f"--tolerance: must not be negative, got {raw!r}")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect reward-payout anomalies: refused-task payouts, duplicate "
                     "payouts, amounts outside the stated task price, and payouts "
                     "without a matching task."
    )
    ap.add_argument("tasks", help="Path to the tasks export JSON array, or '-' for stdin.")
    ap.add_argument("payouts", help="Path to the payouts export JSON array, or '-' for stdin.")
    ap.add_argument("-o", "--output", help="Write the canonical report to this path instead of stdout.")
    ap.add_argument("--tolerance", default="0",
                     help="Decimal tolerance: a payout is only flagged AMOUNT_ABOVE_PRICE/"
                          "AMOUNT_BELOW_PRICE if |amount - price| is strictly greater than "
                          "this value. Default: 0.")
    args = ap.parse_args(argv)

    if args.tasks == "-" and args.payouts == "-":
        sys.stderr.write(canonical_json({
            "error": "INVALID_INPUT",
            "detail": "tasks and payouts cannot both be read from stdin",
        }))
        return 2

    try:
        tolerance = _parse_tolerance(args.tolerance)
        raw_tasks = _load_array(args.tasks, "tasks")
        raw_payouts = _load_array(args.payouts, "payouts")
        report = reconcile(raw_tasks, raw_payouts, tolerance)
    except InputError as exc:
        sys.stderr.write(canonical_json({"error": "INVALID_INPUT", "detail": str(exc)}))
        return 2

    text = canonical_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())

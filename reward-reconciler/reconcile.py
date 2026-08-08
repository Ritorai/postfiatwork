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

Only those three are ever returned. Anything that escapes as an uncaught
exception would exit 1, which this tool defines as "mismatches found" -- a
malformed input recorded as a settlement result. Every failure path below is
therefore funnelled into InputError and exit 2 on purpose.
"""
import argparse
import errno
import json
import os
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


def _oserror_label(exc):
    """A short, locale-independent name for an OSError.

    str(exc) carries the OS's own strerror, which is locale-dependent and is
    the sort of thing that makes a committed transcript unreproducible. The
    class name alone is not enough either: ELOOP and ENOSPC both surface as
    the bare class OSError, which tells a reader nothing. errno's symbolic
    name is stable across locales and says which failure it was.
    """
    name = type(exc).__name__
    symbol = errno.errorcode.get(exc.errno)
    return "%s/%s" % (name, symbol) if symbol else name


def _quantize(raw, where):
    """Parse an amount into a fixed-scale Decimal. Rejects floats and junk."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise InputError(f"{where}: amount must be a string or integer, got {type(raw).__name__}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise InputError(f"{where}: amount is not a valid decimal: {raw!r}")
    if value.is_nan():
        # is_nan(), not `value != value`. A comparison against a SIGNALLING
        # NaN raises InvalidOperation -- that is what signalling NaNs are
        # for -- so the old guard crashed on "sNaN" and exited 1, which is
        # this tool's code for "mismatched". is_nan() answers the question
        # without signalling.
        raise InputError(f"{where}: amount is NaN")
    try:
        return value.quantize(SCALE)
    except InvalidOperation:
        # quantize() raises too, and used to sit outside the guard above.
        # "1E+999999999" and "Infinity" both parse as Decimals and then fail
        # here, so the tool exited 1 with a traceback -- the code a caller
        # reads as "mismatches found". README limitation 2 documented that;
        # this is the repair.
        raise InputError(
            f"{where}: amount is not representable at {SCALE} precision: {raw!r}")


def _load_records(path, fields, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"{label}: file not found: {path}")
    except UnicodeDecodeError:
        raise InputError(f"{label}: not valid UTF-8: {path}")
    except OSError as exc:
        # FileNotFoundError is an OSError and is caught above, so this is
        # every OTHER way "unreadable file" happens: the path is a directory
        # (IsADirectoryError), permission is denied (PermissionError), a
        # symlink loop (OSError/ELOOP). The README documents all of those as
        # exit 2; they used to escape as tracebacks and exit 1. The class
        # name rather than str(exc) keeps the OS's own message -- which is
        # locale-dependent -- out of the output.
        raise InputError(f"{label}: could not read {path}: {_oserror_label(exc)}")
    except RecursionError:
        # json's pure-Python scanner recurses per nesting level, so a
        # deeply nested document raises RecursionError rather than
        # JSONDecodeError. It is still "bad JSON" by the README's own row.
        raise InputError(f"{label}: JSON nesting too deep: {path}")
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


def _write_stderr(text):
    """Best-effort diagnostic. A closed or full fd 2 must not change the code.

    `python3 reconcile.py bad.json x.json 2>&-` closes fd 2, which makes
    sys.stderr None; writing to it raises AttributeError and the process
    exits 1 -- "mismatched" -- for an input that was rejected. The exit code
    is the contract; the message is a courtesy.
    """
    try:
        if sys.stderr is not None:
            sys.stderr.write(text)
            sys.stderr.flush()
    except (OSError, AttributeError, ValueError):
        pass


def _discard_stdout():
    """Point fd 1 at the null device after a failed write.

    CPython flushes sys.stdout again during interpreter shutdown. If that
    flush raises -- and it does, because the failed write left the buffer
    full -- Py_FinalizeEx prints "Exception ignored in: <_io.TextIOWrapper>"
    and forces the process status to 120, overriding the 2 main() returned.
    A run under PYTHONUNBUFFERED=1 never sees this because there is nothing
    left buffered, which is exactly the kind of environment dependence that
    makes a test pass for the wrong reason. Redirecting fd 1 to the null
    device gives the shutdown flush somewhere harmless to go.
    """
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(null_fd, 1)
    except OSError:
        pass
    finally:
        try:
            os.close(null_fd)
        except OSError:
            pass


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
        _write_stderr(canonical_json({"error": "INVALID_INPUT", "detail": str(exc)}))
        return 2

    text = canonical_json(report)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            # Without this the reconciliation verdict is destroyed by an
            # unrelated I/O failure: an unwritable --out on a BALANCED run
            # exited 1, and 1 is this tool's code for "mismatched, one or
            # more settlement issues". A caller reading exit codes recorded a
            # clean settlement as a broken one.
            _write_stderr(canonical_json({
                "error": "OUTPUT_ERROR",
                "detail": f"could not write --out {args.out}: {_oserror_label(exc)}",
            }))
            return 2
    elif sys.stdout is None:
        # fd 1 closed at startup (`reconcile.py ... >&-`). Python sets
        # sys.stdout to None, so the write below would raise AttributeError
        # rather than OSError and sail past the guard.
        _write_stderr(canonical_json({
            "error": "OUTPUT_ERROR",
            "detail": "could not write the report to stdout: stdout is closed",
        }))
        return 2
    else:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except OSError as exc:
            # The stdout branch needs the same guard, and for the same
            # reason. It is the branch every documented invocation without
            # -o takes, and it fails in ordinary use: a closed pipe
            # (BrokenPipeError, e.g. `reconcile.py ... | head -1`) or a full
            # disk (ENOSPC on a redirect). Guarding only --out would have
            # left the exact defect this repair is about sitting three lines
            # below the repair.
            _write_stderr(canonical_json({
                "error": "OUTPUT_ERROR",
                "detail": f"could not write the report to stdout: {_oserror_label(exc)}",
            }))
            _discard_stdout()
            return 2
    return 0 if report["status"] == "balanced" else 1


if __name__ == "__main__":
    sys.exit(main())

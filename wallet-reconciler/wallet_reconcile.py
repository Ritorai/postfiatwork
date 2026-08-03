#!/usr/bin/env python3
"""
Deterministic Wallet Ledger Reconciliation CLI.

Extends the reward-reconciler concept (see README.md, "What was reused") from a
two-file expected/payout diff into a single chronological wallet ledger: an
opening balance, a sequence of events (reward | grant | airdrop | chat_spend),
and a stated closing balance. The tool replays the ledger with Decimal-safe
arithmetic, producing a full per-event running-balance trace plus a canonical,
deterministic findings report.

Sign convention (amounts are always given as positive magnitudes; the event
"type" determines the sign applied to the running balance):
  reward, grant, airdrop  -> balance increases (+amount)
  chat_spend              -> balance decreases (-amount)

Finding codes:
  DUPLICATE_EVENT_ID       - an event_id appears more than once in the ledger
  OUT_OF_ORDER_TIMESTAMP   - an event's "at" is earlier than the immediately
                              preceding (timestamp-valid) event's "at"
  NEGATIVE_RUNNING_BALANCE - the running balance is below zero after an event,
                              or the opening balance itself is negative
  CLOSING_BALANCE_MISMATCH - computed closing balance != stated closing balance
  UNKNOWN_EVENT_TYPE       - "type" is not one of reward/grant/airdrop/chat_spend
  INVALID_AMOUNT           - amount is non-numeric, NaN/Infinity, negative,
                              null, or boolean
  INVALID_TIMESTAMP        - "at" cannot be parsed as a UTC ISO-8601 timestamp

Exit codes:
  0 - reconciled (no findings)
  1 - findings (one or more issues found; ledger did not reconcile cleanly)
  2 - invalid input / usage error (malformed JSON, missing fields, bad file)
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, getcontext

# Generous working precision so long chronological ledgers with many
# high-precision amounts never lose digits to context rounding during
# addition/subtraction. Decimal *construction* from a string or int is
# always exact regardless of this setting; only +/-/* are affected.
getcontext().prec = 60

DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
OUT_OF_ORDER_TIMESTAMP = "OUT_OF_ORDER_TIMESTAMP"
NEGATIVE_RUNNING_BALANCE = "NEGATIVE_RUNNING_BALANCE"
CLOSING_BALANCE_MISMATCH = "CLOSING_BALANCE_MISMATCH"
UNKNOWN_EVENT_TYPE = "UNKNOWN_EVENT_TYPE"
INVALID_AMOUNT = "INVALID_AMOUNT"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"

# type -> sign applied to the running balance
KNOWN_TYPES = {"reward": 1, "grant": 1, "airdrop": 1, "chat_spend": -1}

REQUIRED_EVENT_FIELDS = ("event_id", "type", "amount", "at")
REQUIRED_TOP_FIELDS = ("opening_balance", "closing_balance", "events")


class InputError(Exception):
    """Raised for malformed input that should produce exit code 2."""


class _NonFinite:
    """Sentinel for a JSON NaN/Infinity/-Infinity literal.

    json.loads() accepts these tokens by default via parse_constant, handing
    back float('nan') / float('inf') / float('-inf'). We intercept that and
    wrap the raw token instead of ever building a Decimal('NaN') or float:
    Decimal('NaN') compares False to everything (including itself), which
    would silently defeat every '< 0' / '!=' check downstream and swallow
    real findings. Keeping it as an opaque, non-numeric sentinel forces every
    NaN/Infinity value through the normal INVALID_AMOUNT rejection path.
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
    """Return a value guaranteed to be directly JSON-serializable, for
    echoing arbitrary (possibly malformed) input fields back in findings."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _NonFinite):
        return value.token
    return repr(value)


def _amt_str(d):
    """Canonical string form of a Decimal amount: always plain fixed-point
    (never scientific notation, e.g. never '1E-10'), and -0 normalized to
    0 so a zero-effect event never prints a misleading negative-looking
    balance."""
    if d == 0:
        d = Decimal(0)
    return format(d, "f")


def _parse_finite_decimal(raw, subject):
    """Parse raw into a finite Decimal (sign not restricted here).

    Accepts JSON strings and JSON numbers (both ints and float-shaped
    literals, the latter via parse_float=Decimal so the Decimal is built
    from the source text, never from a 64-bit float). Returns
    (Decimal, None) on success or (None, "reason") on failure.
    """
    if isinstance(raw, _NonFinite):
        return None, f"{subject} is {raw.token} (NaN/Infinity is not permitted)"
    if isinstance(raw, bool):
        return None, f"{subject} must not be a boolean"
    if raw is None:
        return None, f"{subject} must not be null"
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        try:
            value = Decimal(s)
        except InvalidOperation:
            return None, f"{subject} is not a valid decimal number: {raw!r}"
    else:
        return None, f"{subject} has unsupported JSON type: {type(raw).__name__}"

    if value.is_nan() or value.is_infinite():
        return None, f"{subject} must be finite (NaN/Infinity is not permitted)"
    return value, None


def _coerce_amount(raw):
    """Parse an event amount: a finite, non-negative magnitude. The sign is
    applied separately based on event "type" -- a negative amount in the
    input is always a data error (INVALID_AMOUNT), never a valid debit.
    Returns (Decimal, None) or (None, "reason")."""
    value, err = _parse_finite_decimal(raw, "amount")
    if err is not None:
        return None, err
    if value < 0:
        return None, f"amount must be a non-negative magnitude, got {value}"
    return value, None


def _coerce_balance(raw, label):
    """Parse a top-level opening/closing balance field.

    Unlike event amounts, a balance MAY legitimately be negative (e.g. an
    already-overdrawn opening balance) -- that is exactly what
    NEGATIVE_RUNNING_BALANCE exists to flag, so it must not be rejected
    here. Only non-finite / non-numeric / wrong-typed values are structural
    errors (exit 2), since the tool cannot produce a meaningful trace
    without a usable starting/ending balance.
    """
    value, err = _parse_finite_decimal(raw, label)
    if err is not None:
        raise InputError(err)
    return value


def _coerce_timestamp(raw):
    """Parse an ISO-8601 UTC timestamp string into an aware datetime.

    Requires an explicit 'Z' suffix or a '+00:00'/'-00:00' offset -- naive
    timestamps and non-UTC offsets are rejected, since the input contract
    defines "at" as ISO-8601 UTC specifically. Returns (datetime, None) or
    (None, "reason").
    """
    if not isinstance(raw, str):
        return None, f"'at' must be a string, got {type(raw).__name__}"
    s = raw.strip()
    if not s:
        return None, "'at' must not be empty"
    body = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.fromisoformat(body)
    except ValueError:
        return None, f"'at' is not a valid ISO-8601 timestamp: {raw!r}"
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None, "'at' must include a UTC offset or a 'Z' suffix"
    if dt.utcoffset() != timedelta(0):
        return None, "'at' must be expressed in UTC (offset must be +00:00)"
    return dt, None


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


def _load_ledger(path):
    """Parse and structurally validate a ledger document.

    Returns (opening_balance: Decimal, closing_balance: Decimal,
    events: list[dict]) where each event dict has exactly the four raw
    (un-coerced) fields event_id/type/amount/at. event_id is guaranteed to
    be a non-empty string here; type/amount/at are passed through as-is for
    semantic (finding-level) validation in reconcile().
    """
    data = _load_json(path)
    if not isinstance(data, dict):
        raise InputError(f"top-level JSON must be an object, got {type(data).__name__}")
    for key in REQUIRED_TOP_FIELDS:
        if key not in data:
            raise InputError(f"missing required top-level field '{key}'")

    opening = _coerce_balance(data["opening_balance"], "opening_balance")
    closing = _coerce_balance(data["closing_balance"], "closing_balance")

    events = data["events"]
    if not isinstance(events, list):
        raise InputError(f"'events' must be an array, got {type(events).__name__}")

    parsed_events = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise InputError(f"events[{i}] must be an object, got {type(ev).__name__}")
        for key in REQUIRED_EVENT_FIELDS:
            if key not in ev:
                raise InputError(f"events[{i}]: missing required field '{key}'")
        eid = ev["event_id"]
        if not isinstance(eid, str) or not eid.strip():
            raise InputError(f"events[{i}]: 'event_id' must be a non-empty string")
        parsed_events.append({
            "event_id": eid,
            "type": ev["type"],
            "amount": ev["amount"],
            "at": ev["at"],
        })
    return opening, closing, parsed_events


def _finding_sort_key(f):
    # Findings without an "index" (currently only CLOSING_BALANCE_MISMATCH,
    # a whole-ledger check) sort after every per-event/opening-balance
    # finding. Within the same index, codes sort alphabetically.
    idx = f.get("index")
    if idx is None:
        idx = 1 << 62
    return (idx, f["code"])


def reconcile(opening, closing, events):
    """Replay a wallet ledger and produce a canonical reconciliation report.

    opening/closing are already-coerced Decimals; events is a list of raw
    (structurally valid, semantically unchecked) event dicts as returned by
    _load_ledger. Pure function: no I/O, so it's directly unit-testable.
    """
    findings = []
    trace = []
    seen = {}          # event_id -> first index seen
    running = opening
    prev_dt = None
    prev_at_raw = None
    prev_event_id = None

    if running < 0:
        findings.append({
            "code": NEGATIVE_RUNNING_BALANCE,
            "event_id": None,
            "index": -1,
            "context": "opening_balance",
            "balance": _amt_str(running),
        })

    for i, ev in enumerate(events):
        eid = ev["event_id"]
        codes_here = []

        if eid in seen:
            findings.append({
                "code": DUPLICATE_EVENT_ID,
                "event_id": eid,
                "index": i,
                "first_index": seen[eid],
            })
            codes_here.append(DUPLICATE_EVENT_ID)
        else:
            seen[eid] = i

        raw_type = ev["type"]
        sign = KNOWN_TYPES.get(raw_type) if isinstance(raw_type, str) else None
        if sign is None:
            findings.append({
                "code": UNKNOWN_EVENT_TYPE,
                "event_id": eid,
                "index": i,
                "type": _safe_repr(raw_type),
            })
            codes_here.append(UNKNOWN_EVENT_TYPE)

        amount_val, amount_err = _coerce_amount(ev["amount"])
        if amount_err is not None:
            findings.append({
                "code": INVALID_AMOUNT,
                "event_id": eid,
                "index": i,
                "amount": _safe_repr(ev["amount"]),
                "detail": amount_err,
            })
            codes_here.append(INVALID_AMOUNT)

        dt, ts_err = _coerce_timestamp(ev["at"])
        if ts_err is not None:
            findings.append({
                "code": INVALID_TIMESTAMP,
                "event_id": eid,
                "index": i,
                "at": _safe_repr(ev["at"]),
                "detail": ts_err,
            })
            codes_here.append(INVALID_TIMESTAMP)
        else:
            if prev_dt is not None and dt < prev_dt:
                findings.append({
                    "code": OUT_OF_ORDER_TIMESTAMP,
                    "event_id": eid,
                    "index": i,
                    "at": ev["at"],
                    "previous_event_id": prev_event_id,
                    "previous_at": prev_at_raw,
                })
                codes_here.append(OUT_OF_ORDER_TIMESTAMP)
            prev_dt = dt
            prev_at_raw = ev["at"]
            prev_event_id = eid

        applied = sign is not None and amount_val is not None
        signed_amount = None
        if applied:
            signed_amount = amount_val * sign
            running = running + signed_amount
            if running < 0:
                findings.append({
                    "code": NEGATIVE_RUNNING_BALANCE,
                    "event_id": eid,
                    "index": i,
                    "context": None,
                    "balance": _amt_str(running),
                })
                codes_here.append(NEGATIVE_RUNNING_BALANCE)

        trace.append({
            "index": i,
            "event_id": eid,
            "type": _safe_repr(raw_type),
            "at": _safe_repr(ev["at"]),
            "amount": _amt_str(amount_val) if amount_val is not None else None,
            "signed_amount": _amt_str(signed_amount) if signed_amount is not None else None,
            "applied": applied,
            "running_balance": _amt_str(running),
            "codes": sorted(set(codes_here)),
        })

    computed_closing = running
    delta = computed_closing - closing
    if delta != 0:
        findings.append({
            "code": CLOSING_BALANCE_MISMATCH,
            "computed_closing_balance": _amt_str(computed_closing),
            "stated_closing_balance": _amt_str(closing),
            "delta": _amt_str(delta),
        })

    findings.sort(key=_finding_sort_key)

    counts = {}
    for f in findings:
        counts[f["code"]] = counts.get(f["code"], 0) + 1

    return {
        "ledger_version": "1.0",
        "sign_convention": {"reward": "+", "grant": "+", "airdrop": "+", "chat_spend": "-"},
        "opening_balance": _amt_str(opening),
        "stated_closing_balance": _amt_str(closing),
        "computed_closing_balance": _amt_str(computed_closing),
        "closing_delta": _amt_str(delta),
        "event_count": len(events),
        "finding_counts": dict(sorted(counts.items())),
        "findings": findings,
        "trace": trace,
        "status": "reconciled" if not findings else "findings",
    }


def canonical_json(report):
    """Canonical serialization: sorted keys, fixed separators, ASCII-only,
    single trailing newline. Byte-identical across repeated runs on the
    same input."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Reconcile a wallet ledger (opening balance, chronological "
                     "events, stated closing balance) with Decimal-safe running balances."
    )
    ap.add_argument("ledger", help="Path to the ledger JSON file, or '-' to read from stdin.")
    ap.add_argument("-o", "--output", help="Write the canonical report to this path instead of stdout.")
    args = ap.parse_args(argv)

    try:
        opening, closing, events = _load_ledger(args.ledger)
        report = reconcile(opening, closing, events)
    except InputError as exc:
        sys.stderr.write(canonical_json({"error": "INVALID_INPUT", "detail": str(exc)}))
        return 2

    text = canonical_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "reconciled" else 1


if __name__ == "__main__":
    sys.exit(main())
